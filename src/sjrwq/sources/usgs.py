"""USGS harvest via the modernized OGC APIs at api.waterdata.usgs.gov.

Written against the OGC endpoints, not the legacy waterservices.usgs.gov host
or the dataretrieval.nwis module, both of which are scheduled for
decommissioning in Q1 2027.

Division of labor with the Water Quality Portal module: this covers
continuous and daily sensor records, and time-series-metadata has begin and end
per parameter, so period of record comes for free. The portal holds the
discrete USGS samples, and `sources/wqp.py` collects them there.
"""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from ..config import ParameterLookup, fetch, log

BASE = "https://api.waterdata.usgs.gov/ogcapi/v0/collections"
SOURCE = "usgs"
PAGE = 10000

# The OGC time-series metadata includes the computation period, which separates
# a continuous record from an annual peak. 1,420 of the series in this bbox are
# "Water Year"/"Max At Event Time", the crest-stage peak-flow record: one value
# a year, read off a stick after the flood, rather than a continuous discharge
# series.
PERIOD_TO_FREQUENCY = {
    "Points": "sub-daily",
    "Daily": "daily",
    "Water Year": "annual",
}
PERIOD_TO_RECORD_TYPE = {
    "Points": "continuous",
    "Daily": "continuous",
    "Water Year": "discrete",
}

SITE_TYPE_TO_STATION_TYPE = {
    "Stream": "streamgage",
    "Lake, Reservoir, Impoundment": "reservoir",
    "Well": "groundwater_well",
    "Spring": "spring",
    "Estuary": "estuary",
}


def _page(collection: str, params: dict, label: str) -> list[dict]:
    """Page through an OGC items endpoint. numberMatched is not populated, so
    we follow the `next` link until a short page comes back."""
    feats: list[dict] = []
    offset, page_no = 0, 0
    while True:
        p = dict(params, limit=PAGE, offset=offset, f="json")
        txt = fetch(f"{BASE}/{collection}/items", SOURCE, params=p,
                    label=f"{label}_p{page_no}.json")
        gj = json.loads(txt)
        batch = gj.get("features") or []
        feats.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
        page_no += 1
        if page_no > 100:
            log.warning("%s: stopped at 100 pages, results may be truncated", label)
            break
    return feats


def time_series(bbox: list[float], lookup: ParameterLookup | None = None) -> pd.DataFrame:
    """One row per (station, parameter, statistic) time series with period of record."""
    lookup = lookup or ParameterLookup.build()
    bbox_s = ",".join(str(b) for b in bbox)
    rows = []
    for pcode in sorted(lookup.by_usgs_pcode):
        feats = _page("time-series-metadata",
                      {"bbox": bbox_s, "parameter_code": pcode},
                      f"ts_{pcode}")
        log.info("usgs pcode %s -> %d time series", pcode, len(feats))
        for f in feats:
            p = f["properties"]
            rows.append({
                "source_station_id": p["monitoring_location_id"],
                "parameter_code": p["parameter_code"],
                "parameter_source_name": p.get("parameter_name"),
                "unit": p.get("unit_of_measure"),
                "statistic": p.get("computation_identifier"),
                "computation_period": p.get("computation_period_identifier"),
                "frequency": PERIOD_TO_FREQUENCY.get(
                    p.get("computation_period_identifier"), "unknown"),
                "record_type": PERIOD_TO_RECORD_TYPE.get(
                    p.get("computation_period_identifier"), "discrete"),
                "por_start": pd.to_datetime(p.get("begin"), errors="coerce"),
                "por_end": pd.to_datetime(p.get("end"), errors="coerce"),
                "huc12": p.get("hydrologic_unit_code"),
                "primary": p.get("primary"),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["parameter"] = df["parameter_code"].map(lookup.by_usgs_pcode)
    return df


def monitoring_locations(bbox: list[float]) -> gpd.GeoDataFrame:
    bbox_s = ",".join(str(b) for b in bbox)
    feats = _page("monitoring-locations", {"bbox": bbox_s}, "mloc")
    log.info("usgs monitoring locations in bbox: %d", len(feats))
    rows, geoms = [], []
    for f in feats:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        rows.append({
            "source_station_id": f["id"],
            "site_no": p.get("monitoring_location_number"),
            "name": p.get("monitoring_location_name"),
            "operator": p.get("agency_name"),
            "agency_code": p.get("agency_code"),
            "site_type": p.get("site_type"),
            "county": p.get("county_name"),
            "huc12_usgs": p.get("hydrologic_unit_code"),
            "drainage_area_sqmi": p.get("drainage_area"),
            "altitude_ft": p.get("altitude"),
        })
        geoms.append(shape(f["geometry"]))
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs="EPSG:4326")
    return gdf


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    ts = time_series(bbox, lookup)
    if ts.empty:
        raise RuntimeError("USGS time-series query returned nothing")

    wanted = set(ts["source_station_id"])
    locs = monitoring_locations(bbox)
    locs = locs[locs["source_station_id"].isin(wanted)].reset_index(drop=True)

    locs["source"] = SOURCE
    locs["station_type"] = locs["site_type"].map(SITE_TYPE_TO_STATION_TYPE).fillna("other")
    locs["url"] = "https://waterdata.usgs.gov/monitoring-location/" + locs["site_no"].astype(str)
    locs["access"] = "api"
    locs["lon"] = locs.geometry.x
    locs["lat"] = locs.geometry.y

    # Collapse multiple statistics of the same parameter into one availability
    # row, per computation period. The period stays a grouping key because the
    # crest-stage peak record and the continuous record are two records with
    # two periods of record: San Joaquin at Vernalis reads 1923 for its annual
    # stage peaks and 2007 for its continuous stage, and taking the minimum
    # across both dates the continuous series from 1923. 114 of the 1,357 rows
    # take their start from a peak series that way.
    sp = (ts.groupby(["source_station_id", "parameter", "parameter_source_name",
                      "unit", "record_type", "frequency"], dropna=False)
            .agg(por_start=("por_start", "min"),
                 por_end=("por_end", "max"),
                 n_series=("parameter_code", "size"))
            .reset_index())
    sp["source"] = SOURCE
    log.info("usgs: %d stations, %d station-parameter rows", len(locs), len(sp))
    return locs, sp

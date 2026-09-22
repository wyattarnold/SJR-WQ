"""DWR Water Data Library continuous stations, via the CNRA data portal.

The Water Data Library website has no API. Its machine-readable front door is
the CNRA CKAN dataset "DWR Continuous Data Download Links", which publishes
three live datastore tables:

    Stations      1,372 rows, coordinates, datum, positional accuracy, CDEC id
    Station-Trace 4,053 rows, one per station and parameter, with the period
                  of record and a direct download URL for the series
    Parameters    25 rows, the controlled parameter vocabulary

The middle table is the useful one. It has a start and an end for every
published series, so the whole availability picture comes back in one query
with no download. It also has `output_interval`, RAW or DAYMEAN, which gives
the sampling frequency.

Two things this source adds and the others do not. It publishes `lldatum`, so
its stations are the ones in the inventory whose horizontal datum is known
rather than assumed to be WGS84. And it publishes a `Salinity` parameter, which
USGS, the portal, and eSMR each hold a crosswalk entry for and 0 rows of. EDI
is the other salinity source.

It also has `cdec_id`, a stated cross-source link rather than one the
deduplicator infers from coordinates.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import classify
from ..config import ParameterLookup, fetch, log

CKAN = "https://data.cnra.ca.gov/api/3/action"
SOURCE = "dwr_wdl"

STATIONS = "c2b08f48-acfd-4a5b-9799-0f3e07d83192"
TRACES = "cdb5dd35-c344-4969-8ab2-d0e2d6c00821"

# CKAN caps a datastore_search_sql response at 32,000 rows. Both tables sit far
# below that, so reaching the cap means the query changed shape and the answer
# is short.
ROW_LIMIT = 32000

STATION_TYPE_MAP = {
    "Surface Water": "continuous_sonde",
    "Groundwater": "groundwater_well",
    "Water Quality": "continuous_sonde",
    "Reservoir": "reservoir",
    "Meteorological": "weather",
}

# RAW is the instrument's own timestep, typically 15 minutes; DAYMEAN is the
# daily mean computed from it. Both come off a continuous monitor.
INTERVAL_TO_FREQUENCY = {"RAW": "sub-daily", "DAYMEAN": "daily"}


def _sql(sql: str, label: str) -> pd.DataFrame:
    txt = fetch(f"{CKAN}/datastore_search_sql", SOURCE, params={"sql": sql},
                label=label, timeout=300)
    payload = json.loads(txt)
    if not payload.get("success"):
        raise RuntimeError(f"CKAN refused the query: {payload.get('error')}")
    records = payload["result"]["records"]
    if len(records) >= ROW_LIMIT:
        log.warning("dwr_wdl: %s hit the %d row cap, the result is truncated",
                    label, ROW_LIMIT)
    return pd.DataFrame(records)


def stations(bbox: list[float]) -> pd.DataFrame:
    sql = (
        # "County" is capitalised in the published table and Postgres folds
        # unquoted identifiers to lower case, so it has to be quoted.
        f'SELECT station_number, station_name, station_short_name, cdec_id, '
        f'station_type, "County" AS county, latitude, longitude, lldatum, '
        f'positional_accuracy FROM "{STATIONS}" '
        f'WHERE latitude::float8 BETWEEN {bbox[1]} AND {bbox[3]} '
        f'AND longitude::float8 BETWEEN {bbox[0]} AND {bbox[2]}'
    )
    df = _sql(sql, "stations.json")
    log.info("dwr_wdl: %d stations inside the study bbox", len(df))
    return df


def traces(station_numbers: list[str]) -> pd.DataFrame:
    """Period of record per station and parameter, straight from the table."""
    if not station_numbers:
        return pd.DataFrame()
    ids = ", ".join("'" + s.replace("'", "''") + "'" for s in station_numbers)
    sql = (
        f'SELECT station_number, parameter, "desc", output_interval, '
        f'datasource, start_time, end_time, download_link '
        f'FROM "{TRACES}" WHERE station_number IN ({ids})'
    )
    df = _sql(sql, "traces.json")
    log.info("dwr_wdl: %d published series", len(df))
    return df


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    st = stations(bbox)
    if st.empty:
        raise RuntimeError("DWR WDL returned no stations inside the study bbox")

    st = st[st["latitude"].notna() & st["longitude"].notna()].copy()
    st["lat"] = st["latitude"].astype(float)
    st["lon"] = st["longitude"].astype(float)
    st["source_station_id"] = st["station_number"].astype(str)

    tr = traces(sorted(st["source_station_id"].unique()))
    if tr.empty:
        return _to_gdf(st), pd.DataFrame()

    # DWR names its own trace column `parameter`, so the source name moves to
    # `parameter_source_name` before the canonical name lands in `parameter`.
    tr = tr.rename(columns={"parameter": "parameter_source_name"})
    by_name = {k.lower(): v for k, v in lookup.by_dwr_name.items()}
    tr["parameter"] = (tr["parameter_source_name"].astype(str).str.strip()
                       .str.lower().map(by_name))

    unmapped = tr[tr["parameter"].isna()]["parameter_source_name"].value_counts()
    if not unmapped.empty:
        log.info("dwr_wdl: %d parameters outside the crosswalk: %s",
                 len(unmapped), ", ".join(f"{n} ({c})" for n, c in unmapped.items()))
    tr = tr[tr["parameter"].notna()].copy()

    tr["frequency"] = tr["output_interval"].map(INTERVAL_TO_FREQUENCY).fillna("unknown")
    tr["record_type"] = np.where(
        tr["frequency"].isin(classify.CONTINUOUS_FREQUENCIES),
        "continuous", "discrete")
    tr["por_start"] = pd.to_datetime(tr["start_time"], errors="coerce")
    tr["por_end"] = pd.to_datetime(tr["end_time"], errors="coerce")
    tr["source_station_id"] = tr["station_number"].astype(str)

    sp = (tr.groupby(["source_station_id", "parameter", "parameter_source_name",
                      "record_type", "frequency"], dropna=False)
            .agg(por_start=("por_start", "min"),
                 por_end=("por_end", "max"),
                 n_series=("download_link", "size"))
            .reset_index())
    sp["source"] = SOURCE
    # WDL publishes no unit on the trace table. The parameter code implies the
    # unit (ECat25C is uS/cm, WaterTemp is degC), so this module sets the
    # canonical unit rather than leaving it blank for the resolver to flag.
    sp["unit"] = sp["parameter"].map(lookup.units)

    st = st[st["source_station_id"].isin(set(sp["source_station_id"]))].copy()
    log.info("dwr_wdl: %d stations, %d station-parameter rows", len(st), len(sp))
    return _to_gdf(st), sp


def _to_gdf(st: pd.DataFrame) -> gpd.GeoDataFrame:
    out = gpd.GeoDataFrame(
        pd.DataFrame({
            "source_station_id": st["source_station_id"],
            "name": st["station_name"].fillna(st["station_short_name"]),
            "operator": "California Department of Water Resources",
            "station_type": st["station_type"].map(STATION_TYPE_MAP)
                              .fillna("continuous_sonde"),
            "horizontal_datum": st["lldatum"],
            "positional_accuracy": st.get("positional_accuracy"),
            "cdec_id": st.get("cdec_id").replace({"None": None}),
            "county": st.get("county"),
            "lat": st["lat"],
            "lon": st["lon"],
        }),
        geometry=gpd.points_from_xy(st["lon"], st["lat"]),
        crs="EPSG:4326",
    ).reset_index(drop=True)
    out["source"] = SOURCE
    out["url"] = ("https://wdl.water.ca.gov/WaterDataLibrary/StationDetails.aspx"
                  "?Station=" + out["source_station_id"].astype(str))
    out["access"] = "api"
    return out

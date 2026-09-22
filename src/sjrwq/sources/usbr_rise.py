"""Reclamation Information Sharing Environment (RISE).

RISE is Reclamation's own operations database, and it holds what the other
seven sources do not: release temperature at Friant and New Melones, San Luis
and Delta-Mendota Canal operations, and reservoir inflow, storage, and elevation
as Reclamation itself reports them rather than as CDEC republishes them.

It is small. Of 1,012 published locations nationally, 24 fall inside the study
bbox, 19 publish at least one series, and 8 survive the study-area clip. The
rest are Sacramento and Delta sites the clip removes. That is the point: RISE
is narrow, and what remains is the boundary conditions, Millerton and Friant,
New Melones, and the canals that import water into the basin.

Three notes on the API:

  - It is JSON:API and needs `Accept: application/vnd.api+json`. Without the
    header the service returns HTML.
  - There is no bounding-box filter on `/location`, and the `stateId` filter
    returns 0 rows for every id tried, so this module pages the whole location
    list and filters locally. It is 11 pages.
  - RISE accepts `/catalog-item?locationId=N` and ignores it, returning the
    entire national catalog, 8,852 items, for every value of N. Its first page
    looks plausible for a reservoir, so the failure stays quiet. Only the
    relationship graph reaches the series belonging to a location: location ->
    catalogRecord -> catalogItem. This module pages both collections in full
    and joins them locally, which is 102 cached requests, and no shorter route
    returns a correct answer.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from ..config import ParameterLookup, fetch, log

BASE = "https://data.usbr.gov/rise/api"
SOURCE = "usbr_rise"
HEADERS = {"Accept": "application/vnd.api+json"}
PAGE = 100

# RISE types a reservoir and a stream gage both as a location, so only the name
# separates them. These are the words that decide, channel words first: half the
# canal and river gages here are named for the dam they sit below, so "Friant
# Kern Canal at Millerton Dam" and "San Joaquin River below Friant Dam" both
# come out reservoirs if the reservoir words are tested first.
NAME_TO_STATION_TYPE = [
    ("streamgage", ("river", "creek", "canal", "aqueduct", "slough", "bypass",
                    "drain", "channel")),
    ("reservoir", ("reservoir", "lake", " dam", "forebay", "afterbay")),
]

# Parameters that are real but outside this inventory's scope. Listed so the
# unmapped-parameter warning stays short and means something.
IGNORED_PARAMETERS = {
    "Air Temperature", "Precipitation", "Snow Depth", "Snow Water Content",
    "Wind Speed", "Wind Direction", "Relative Humidity", "Solar Radiation",
    "Dew Point Temperature", "Evapotranspiration",
}


def _page(collection: str, params: dict, label: str) -> list[dict]:
    """Page a JSON:API collection until a short page comes back."""
    out: list[dict] = []
    page = 1
    while True:
        q = dict(params, itemsPerPage=PAGE, page=page)
        txt = fetch(f"{BASE}/{collection}", SOURCE, params=q,
                    label=f"{label}_p{page}.json", timeout=180, headers=HEADERS)
        batch = json.loads(txt).get("data") or []
        out.extend(batch)
        if len(batch) < PAGE:
            break
        page += 1
        if page > 200:
            log.warning("rise %s: stopped at 200 pages", label)
            break
    return out


def locations(bbox: list[float]) -> gpd.GeoDataFrame:
    """Every RISE location whose coordinate falls inside the bbox."""
    rows, geoms = [], []
    for item in _page("location", {}, "location"):
        a = item["attributes"]
        coords = (a.get("locationCoordinates") or {}).get("coordinates") or []
        if len(coords) < 2 or not isinstance(coords[0], (int, float)):
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        name = a.get("locationName") or ""
        low = name.lower()
        stype = "other"
        for candidate, words in NAME_TO_STATION_TYPE:
            if any(w in low for w in words):
                stype = candidate
                break
        rows.append({
            "source_station_id": str(a["_id"]),
            "name": name,
            "operator": "Bureau of Reclamation",
            "station_type": stype,
            "horizontal_datum": (a.get("horizontalDatum") or {}).get("_id"),
            "elevation_ft": a.get("elevation"),
            "lat": lat,
            "lon": lon,
        })
        geoms.append(Point(lon, lat))
    log.info("rise: %d locations inside the study bbox", len(rows))
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs="EPSG:4326")


def _record_locations() -> dict[str, str]:
    """catalog-record id to location id.

    Every published series hangs off a catalog record, and the record holds
    the link to its location. The item does not.
    """
    out: dict[str, str] = {}
    for item in _page("catalog-record", {}, "record"):
        rid = str(item["attributes"]["_id"])
        rel = (item.get("relationships") or {}).get("location") or {}
        target = (rel.get("data") or {}).get("id")
        if target:
            out[rid] = target.rsplit("/", 1)[-1]
    log.info("rise: %d catalog records, %d with a location",
             len(out), sum(1 for v in out.values() if v))
    return out


def catalog_items(location_ids: set[str]) -> pd.DataFrame:
    """One row per published series at the requested locations.

    `hasProfile` passes through unchanged. It is the one field among the
    harvested sources that flags a depth profile, which is the dataset the gap
    analysis reports as missing.
    """
    record_to_location = _record_locations()
    wanted_records = {r for r, loc in record_to_location.items()
                      if loc in location_ids}
    rows = []
    for item in _page("catalog-item", {}, "item"):
        rel = (item.get("relationships") or {}).get("catalogRecord") or {}
        target = (rel.get("data") or {}).get("id")
        rid = target.rsplit("/", 1)[-1] if target else None
        if rid not in wanted_records:
            continue
        a = item["attributes"]
        rows.append({
            "source_station_id": record_to_location[rid],
            "parameter_source_name": a.get("parameterName"),
            "unit": a.get("parameterUnit"),
            "timestep": a.get("parameterTimestep"),
            "has_profile": bool(a.get("hasProfile")),
            "is_modeled": bool(a.get("isModeled")),
            "por_start": pd.to_datetime(a.get("temporalStartDate"),
                                        errors="coerce", utc=True),
            "por_end": pd.to_datetime(a.get("temporalEndDate"),
                                      errors="coerce", utc=True),
            "item_id": a.get("_id"),
        })
    df = pd.DataFrame(rows)
    log.info("rise: %d catalog items at %d in-scope locations",
             len(df), df["source_station_id"].nunique() if not df.empty else 0)
    return df


# RISE timesteps to this inventory's frequency vocabulary, and to whether the
# series came off an instrument. `periodic` and `intermittent` are RISE's words
# for a series with no schedule, which covers 3,802 of the 8,852 catalog items
# nationally: they state no frequency, so the frequency is unknown and the
# record is a sample rather than a monitor.
TIMESTEP_TO_FREQUENCY = {
    "one minute": "sub-daily", "instantaneous": "sub-daily",
    "15 minute": "sub-daily", "hourly": "sub-daily",
    "every two hours": "sub-daily",
    "daily": "daily", "monthly": "monthly", "quarterly": "quarterly",
    "biannually": "annual", "annual": "annual", "water year": "annual",
    "periodic": "unknown", "intermittent": "unknown",
}
CONTINUOUS_TIMESTEPS = {"one minute", "instantaneous", "15 minute", "hourly",
                        "every two hours", "daily"}


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    locs = locations(bbox)
    if locs.empty:
        raise RuntimeError("RISE returned no locations inside the study bbox")

    items = catalog_items(set(locs["source_station_id"]))
    if items.empty:
        return locs.assign(source=SOURCE, access="api"), pd.DataFrame()

    by_name = {k.strip().lower(): v for k, v in lookup.by_usbr_name.items()}
    items["parameter"] = items["parameter_source_name"].astype(str).str.strip() \
        .str.lower().map(by_name)

    unmapped = items[items["parameter"].isna()]
    skipped = unmapped[~unmapped["parameter_source_name"].isin(IGNORED_PARAMETERS)]
    if not skipped.empty:
        counts = skipped["parameter_source_name"].value_counts()
        log.info("rise: %d parameters not in the crosswalk: %s",
                 len(counts), "; ".join(f"{n} ({c})" for n, c in counts.head(12).items()))
    items = items[items["parameter"].notna()].copy()

    # A modeled series is a reconstruction rather than a measurement, and
    # unioning one into a period of record antedates the gage that made it. New
    # Melones Dam was completed in 1979 and a monthly back-cast dates the
    # station from 1921. `sources/edi.py` rejects modeled packages the same
    # way.
    modeled = items["is_modeled"].fillna(False).astype(bool)
    if modeled.any():
        log.info("rise: dropped %d modeled series at %d locations; a "
                 "reconstruction is not a measurement", int(modeled.sum()),
                 items.loc[modeled, "source_station_id"].nunique())
        items = items[~modeled].copy()

    step = items["timestep"].astype(str).str.strip().str.lower()
    items["frequency"] = step.map(TIMESTEP_TO_FREQUENCY).fillna("unknown")
    items["record_type"] = np.where(step.isin(CONTINUOUS_TIMESTEPS),
                                    "continuous", "discrete")
    # RISE publishes a timestep for some series and leaves it null for others,
    # including every reservoir series at Millerton. Those are the daily
    # operations record, so an absent timestep reads as daily. A stated
    # `periodic` is not absent, and keeps the unknown frequency the word means.
    absent = items["timestep"].isna() | (step.isin(("", "none", "nan")))
    items.loc[absent, "frequency"] = "daily"
    items.loc[absent, "record_type"] = "continuous"

    sp = (items.groupby(["source_station_id", "parameter", "parameter_source_name",
                         "unit", "record_type", "frequency"], dropna=False)
               .agg(por_start=("por_start", "min"),
                    por_end=("por_end", "max"),
                    n_series=("item_id", "size"),
                    has_profile=("has_profile", "any"))
               .reset_index())
    for col in ("por_start", "por_end"):
        sp[col] = pd.to_datetime(sp[col], errors="coerce", utc=True).dt.tz_localize(None)
    sp["source"] = SOURCE

    n_prof = int(sp["has_profile"].sum())
    if n_prof:
        log.info("rise: %d series carry a depth profile", n_prof)

    keep = set(sp["source_station_id"])
    locs = locs[locs["source_station_id"].isin(keep)].copy()
    locs["source"] = SOURCE
    locs["url"] = "https://data.usbr.gov/rise/#/dataset/" + locs["source_station_id"]
    locs["access"] = "api"
    log.info("rise: %d locations, %d station-parameter rows", len(locs), len(sp))
    return locs, sp

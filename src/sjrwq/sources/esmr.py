"""eSMR harvest: NPDES self-monitoring reports from the CIWQS database.

Dischargers with an individual NPDES permit file their monitoring results
electronically through CIWQS, and the State Water Board republishes them on
data.ca.gov as one CKAN resource per sampling year. This is the one source in
the inventory with effluent temperature, EC, and flow at the point where
treated wastewater enters the river, plus the receiving-water samples taken
upstream and downstream of each outfall.

None of it reaches the Water Quality Portal, so the municipal return flows come
from here.

Two things make the harvest cheap. Each yearly resource is a live datastore
table, so the whole aggregation runs server-side through
`datastore_search_sql` and only the summary comes back: a full year of
statewide records reduces to about 1,500 rows for this basin in a few seconds.
And because the resources split on sampling year, period of record comes from
a min and max across the yearly summaries.

Data quirks handled here:

  - Longitude sign is inconsistent. About a fifth of records in the study
    latitude band publish a positive longitude, which for California is a
    dropped minus sign, so the query filters on ABS(longitude) and this module
    restores the sign on the way out.
  - Units are per-record free text and sometimes wrong: electrical
    conductivity appears with units of MGD, salinity with a coliform unit.
    This module keeps the modal unit per station and parameter and counts the
    variants rather than repairing them.
  - Temperature arrives in both Fahrenheit and Celsius, roughly 70/30 by
    record count. The unit column is the one way to separate the two.
"""

from __future__ import annotations

import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import classify
from ..config import ParameterLookup, fetch, log

CKAN = "https://data.ca.gov/api/3/action"
PACKAGE = "water-quality-effluent-electronic-self-monitoring-report-esmr-data"
SOURCE = "esmr"

# CKAN caps a datastore_search_sql response. Yearly summaries for this basin
# run to a couple of thousand rows, so hitting the cap means the query changed
# shape and the result is silently truncated.
ROW_LIMIT = 32000

PLACE_TYPE_TO_STATION_TYPE = {
    "Effluent Monitoring": "effluent",
    "Receiving Water Monitoring": "receiving_water",
    "Influent Monitoring": "influent",
    "Internal Monitoring": "internal",
    "Land Discharge Monitoring": "land_discharge",
    "Groundwater Monitoring": "groundwater_well",
    "Storm Water Monitoring": "stormwater",
}

# Parameters that are administrative rather than measured: percent removal,
# net values, and the load form of a concentration. Excluded so they do not
# inflate the station-parameter table.
_DERIVED = re.compile(r"percent removal|net value", re.IGNORECASE)


def year_resources() -> dict[int, str]:
    """Map sampling year to CKAN resource id for the yearly analytical tables."""
    txt = fetch(f"{CKAN}/package_show", SOURCE, params={"id": PACKAGE},
                label="package.json")
    pkg = json.loads(txt)["result"]
    years: dict[int, str] = {}
    for r in pkg["resources"]:
        if not r.get("datastore_active"):
            continue
        m = re.match(r"^(\d{4}) eSMR Analytical Data$", r.get("name") or "")
        if m:
            years[int(m.group(1))] = r["id"]
    if not years:
        raise RuntimeError("no yearly eSMR datastore resources found; check the package")
    log.info("esmr: %d yearly resources, %d-%d",
             len(years), min(years), max(years))
    return years


def _sql(query: str, label: str) -> list[dict]:
    txt = fetch(f"{CKAN}/datastore_search_sql", SOURCE, params={"sql": query},
                label=label, timeout=600, retries=2)
    payload = json.loads(txt)
    if not payload.get("success"):
        raise RuntimeError(f"CKAN SQL failed: {json.dumps(payload)[:400]}")
    return payload["result"]["records"]


def year_summary(year: int, resource_id: str, bbox: list[float]) -> pd.DataFrame:
    """Per-location, per-parameter summary for one sampling year.

    The aggregation runs in the datastore rather than here. The yearly tables
    hold up to two million statewide records each, and this inventory stores
    counts and periods rather than values, so downloading them to count rows
    would be wasted traffic.
    """
    west, south, east, north = bbox
    query = f'''
        SELECT location_place_id, location, location_place_type,
               facility_name, facility_place_id, region, receiving_water_body,
               parameter, units, latitude, longitude,
               MIN(sampling_date) AS por_start,
               MAX(sampling_date) AS por_end,
               COUNT(*) AS n_obs
        FROM "{resource_id}"
        WHERE latitude BETWEEN {south} AND {north}
          AND ABS(longitude) BETWEEN {abs(east)} AND {abs(west)}
        GROUP BY 1,2,3,4,5,6,7,8,9,10,11
    '''
    recs = _sql(query, f"summary_{year}.json")
    if len(recs) >= ROW_LIMIT:
        log.warning("esmr %d: %d rows, at or above the CKAN row cap; result may be "
                    "truncated", year, len(recs))
    log.info("esmr %d -> %d location-parameter rows", year, len(recs))
    df = pd.DataFrame(recs)
    if not df.empty:
        df["year"] = year
    return df


def _coordinates(raw: pd.DataFrame) -> pd.DataFrame:
    """One coordinate per monitoring location, weighted by observation count.

    A location occasionally shifts between reports, usually by a few meters of
    rounding. The busiest coordinate wins rather than the mean, so the point
    stays on a real published position instead of somewhere between two.
    """
    w = (raw.groupby(["location_place_id", "lat", "lon"], as_index=False)["n_obs"]
            .sum()
            .sort_values(["location_place_id", "n_obs"], ascending=[True, False]))
    best = w.groupby("location_place_id", as_index=False).head(1)
    spread = w.groupby("location_place_id").size()
    moved = int((spread > 1).sum())
    if moved:
        log.info("esmr: %d locations publish more than one coordinate, "
                 "using the most-reported one", moved)
    return best[["location_place_id", "lat", "lon"]]


# eSMR publishes no sampling frequency, so it comes from the record itself.
# Each row has an observation count and a period of record, and their ratio is
# the reporting interval. Measured that way, effluent discharge has a median
# interval of 3.4 days and a first quartile of 1.0, because a permit that sets
# an average daily flow limit obliges the operator to report daily. Specific
# conductance comes in at 27 days and temperature at 14.
#
# One label for the whole source gets both ends wrong. `discrete` hides a daily
# flow record at 10 outfalls, and `continuous` puts monthly EC samples into a
# calibration shortlist. The interval decides, per parameter.
#
# eSMR is a monthly reporting obligation, so its finest honest band is daily: a
# permit that requires continuous monitoring still reports one daily value, and
# the series whose implied interval falls under a day are reporting several
# samples from one collection date rather than running a sonde. That is what
# `classify.SAMPLE_COUNT_BANDS` is for, and this module uses it unchanged.


def _frequency(sp: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Interval, frequency, and record type, from count over span.

    A permit that reports one flow value a day is reporting a daily mean off a
    meter, so a daily frequency here is a continuous record. Everything coarser
    is a sample somebody collected and sent to a laboratory.
    """
    interval, freq = classify.measured_frequency(
        sp["por_start"], sp["por_end"], sp["n_obs"],
        classify.SAMPLE_COUNT_BANDS)
    record_type = pd.Series(
        np.where(freq.isin(classify.CONTINUOUS_FREQUENCIES),
                 "continuous", "discrete"),
        index=sp.index, dtype=object)
    log.info("esmr: frequency from the reported interval %s",
             freq.value_counts().to_dict())
    return interval, freq, record_type


def harvest(bbox: list[float], lookup: ParameterLookup | None = None,
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    years = year_resources()

    parts = []
    for year in sorted(years):
        df = year_summary(year, years[year], bbox)
        if not df.empty:
            parts.append(df)
    if not parts:
        raise RuntimeError("eSMR returned nothing for this bounding box")
    raw = pd.concat(parts, ignore_index=True)

    raw["n_obs"] = raw["n_obs"].astype(int)
    raw["lat"] = raw["latitude"].astype(float)
    # See the module docstring: positive longitudes here are dropped minus signs.
    raw["lon"] = -raw["longitude"].astype(float).abs()
    for col in ("por_start", "por_end"):
        raw[col] = pd.to_datetime(raw[col], errors="coerce")

    coords = _coordinates(raw)

    # Station identity is the monitoring location, not the facility: a plant
    # typically reports an outfall plus receiving-water points upstream and
    # downstream of it, each with its own coordinate.
    stations = (raw.sort_values("year")
                   .groupby("location_place_id", as_index=False)
                   .agg(location=("location", "last"),
                        place_type=("location_place_type", "last"),
                        facility_name=("facility_name", "last"),
                        facility_place_id=("facility_place_id", "last"),
                        region=("region", "last"),
                        receiving_water_body=("receiving_water_body", "last")))
    stations = stations.merge(coords, on="location_place_id", how="left")
    stations["name"] = (stations["facility_name"].fillna("") + " "
                        + stations["location"].fillna("")).str.strip()
    stations["station_type"] = stations["place_type"].map(PLACE_TYPE_TO_STATION_TYPE) \
        .fillna("effluent")
    stations["source"] = SOURCE
    stations["source_station_id"] = stations["location_place_id"].astype(str)
    stations["operator"] = stations["facility_name"]
    stations["url"] = ("https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/"
                       "CiwqsReportServlet?inCommand=reset&reportName=esmrAnalyticalData")
    stations["access"] = "api"

    gdf = gpd.GeoDataFrame(
        stations,
        geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
        crs="EPSG:4326",
    )

    # Availability across every year, keyed on the canonical parameter. eSMR
    # names its own column `parameter`, so the source name moves to
    # `parameter_source_name` before the canonical name lands in `parameter`.
    meas = raw[~raw["parameter"].str.contains(_DERIVED, na=False)].copy()
    meas = meas.rename(columns={"parameter": "parameter_source_name"})
    meas["parameter"] = meas["parameter_source_name"].str.strip().str.lower() \
        .map(lookup.by_esmr_parameter)

    unmapped = (meas[meas["parameter"].isna()]
                .groupby("parameter_source_name")
                .agg(n_obs=("n_obs", "sum"),
                     sites=("location_place_id", "nunique"))
                .sort_values("n_obs", ascending=False))
    if not unmapped.empty:
        top = unmapped.head(12)
        log.info("esmr: %d parameters not in the crosswalk (%d records). Largest: %s",
                 len(unmapped), int(unmapped["n_obs"].sum()),
                 "; ".join(f"{p} ({int(r.n_obs)} recs, {int(r.sites)} sites)"
                           for p, r in top.iterrows()))

    meas = meas[meas["parameter"].notna()].copy()
    # One row per location, parameter, and unit. Turlock reports 1,813 effluent
    # temperatures in Celsius and 1,710 in Fahrenheit under one parameter name,
    # and keeping the modal unit alone applies one conversion factor to both
    # halves. Splitting on the unit gives each half its own row and its own
    # factor, and the caller sees two records rather than one wrong one.
    sp = (meas.groupby(["location_place_id", "parameter", "units"], as_index=False)
              .agg(por_start=("por_start", "min"),
                   por_end=("por_end", "max"),
                   n_obs=("n_obs", "sum"),
                   parameter_source_name=("parameter_source_name",
                                          lambda s: ";".join(sorted(set(s))))))
    sp = sp.rename(columns={"location_place_id": "source_station_id",
                            "units": "unit"})
    sp["n_units"] = sp.groupby(["source_station_id", "parameter"])["unit"] \
                      .transform("nunique")
    split = int((sp["n_units"] > 1).sum())
    if split:
        log.info("esmr: %d rows come from a series reported in more than one "
                 "unit, kept apart so each takes its own conversion", split)
    sp["source_station_id"] = sp["source_station_id"].astype(str)
    sp["source"] = SOURCE
    sp["median_interval_days"], sp["frequency"], sp["record_type"] = _frequency(sp)

    log.info("esmr: %d monitoring locations, %d station-parameter rows",
             len(gdf), len(sp))
    return gdf, sp

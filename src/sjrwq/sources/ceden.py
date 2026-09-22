"""CEDEN surface water chemistry, direct from data.ca.gov.

Most of CEDEN reaches the Water Quality Portal, so the portal sweep runs
first. This module measures how much of CEDEN the portal misses.

CEDEN is the state's own store for surface water chemistry, and its yearly
chemistry tables arrive as live CKAN datastore resources, one per sampling
year, about 1.8 million statewide records each. The aggregation runs
server-side, as it does for eSMR, so a full sampling year reduces to a few
thousand station-analyte rows.

What this catches that the portal does not: records CEDEN has yet to exchange
upward, and records from programs that publish to CEDEN alone. The gap analysis
reports the overlap, so "is the portal enough" has a number in this repository
rather than an assumption behind it.

One quirk. The chemistry tables hold `Latitude`/`Longitude` for the sampling
point, and a separate table holds station-level coordinates. This module uses
the per-record coordinate, resolved to one point per station by observation
count, because it needs no second query and it is the position tied to the
laboratory result.
"""

from __future__ import annotations

import json
import re

import geopandas as gpd
import pandas as pd

from .. import classify
from ..config import ParameterLookup, fetch, log

CKAN = "https://data.ca.gov/api/3/action"
PACKAGE = "surface-water-chemistry-results"
SOURCE = "ceden"

# CKAN caps a datastore_search_sql response. A basin-year summary runs to a few
# thousand rows, so reaching the cap means the answer is short.
ROW_LIMIT = 32000

# Only the water column. CEDEN reports sediment and tissue in the same tables
# and a selenium concentration in fish tissue is not a water quality
# observation.
WATER_MATRICES = ("samplewater", "samplewater_sed")


def year_resources() -> dict[int, str]:
    txt = fetch(f"{CKAN}/package_show", SOURCE, params={"id": PACKAGE},
                label="package.json", timeout=180)
    payload = json.loads(txt)
    years: dict[int, str] = {}
    for r in payload["result"]["resources"]:
        if not r.get("datastore_active"):
            continue
        m = re.match(r"^(\d{4}) CEDEN Water Chemistry Data$", r.get("name") or "")
        if m:
            years[int(m.group(1))] = r["id"]
    if not years:
        raise RuntimeError("no yearly CEDEN chemistry resources found")
    log.info("ceden: %d yearly resources, %d-%d", len(years), min(years), max(years))
    return years


def _sql(query: str, label: str) -> list[dict]:
    txt = fetch(f"{CKAN}/datastore_search_sql", SOURCE, params={"sql": query},
                label=label, timeout=900, retries=2, backoff=20.0)
    payload = json.loads(txt)
    if not payload.get("success"):
        raise RuntimeError(f"CKAN SQL failed: {json.dumps(payload)[:400]}")
    return payload["result"]["records"]


def year_summary(year: int, resource_id: str, bbox: list[float],
                 analytes: list[str]) -> pd.DataFrame:
    """Per-station, per-analyte summary for one sampling year."""
    west, south, east, north = bbox
    wanted = ", ".join("'" + a.replace("'", "''") + "'" for a in analytes)
    matrices = ", ".join(f"'{m}'" for m in WATER_MATRICES)
    query = f'''
        SELECT "StationCode", "StationName", "Analyte", "Unit",
               "Latitude", "Longitude",
               MIN("SampleDate") AS por_start,
               MAX("SampleDate") AS por_end,
               COUNT(*) AS n_obs
        FROM "{resource_id}"
        WHERE "Latitude" BETWEEN {south} AND {north}
          AND "Longitude" BETWEEN {west} AND {east}
          AND "MatrixName" IN ({matrices})
          AND "Analyte" IN ({wanted})
        GROUP BY 1,2,3,4,5,6
    '''
    recs = _sql(query, f"summary_{year}.json")
    if len(recs) >= ROW_LIMIT:
        log.warning("ceden %d: %d rows, at or above the CKAN cap", year, len(recs))
    log.info("ceden %d -> %d station-analyte rows", year, len(recs))
    df = pd.DataFrame(recs)
    if not df.empty:
        df["year"] = year
    return df


def analyte_names(lookup: ParameterLookup) -> list[str]:
    """CEDEN analyte names to ask for.

    CEDEN's vocabulary is close to the portal's but not identical, so this
    list starts from the portal characteristic names and adds the CEDEN
    spellings. Asking for a name CEDEN does not use is harmless; missing one it
    does use loses data without a warning.
    """
    names = set(lookup.wqp_characteristic_names())
    names |= {
        "Temperature", "SpecificConductivity", "Conductivity",
        "Oxygen, Dissolved", "Salinity", "Turbidity", "pH",
        "Chloride, Dissolved", "Chloride, Total",
        "Sulfate, Dissolved", "Sulfate, Total",
        "Boron, Dissolved", "Boron, Total",
        "Selenium, Dissolved", "Selenium, Total",
        "TotalDissolvedSolids", "SuspendedSedimentConcentration",
        "OrganicCarbon, Dissolved", "OrganicCarbon, Total",
    }
    return sorted(names)


# CEDEN spellings that the portal crosswalk does not already cover.
EXTRA_ALIASES = {
    "temperature": "water_temperature",
    "specificconductivity": "specific_conductance",
    "oxygen, dissolved": "dissolved_oxygen",
    "chloride, dissolved": "chloride",
    "chloride, total": "chloride",
    "sulfate, dissolved": "sulfate",
    "sulfate, total": "sulfate",
    "boron, dissolved": "boron",
    "boron, total": "boron",
    "selenium, dissolved": "selenium",
    "selenium, total": "selenium",
    "totaldissolvedsolids": "total_dissolved_solids",
    "suspendedsedimentconcentration": "suspended_solids",
    "organiccarbon, dissolved": "organic_carbon",
    "organiccarbon, total": "organic_carbon",
}


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    years = year_resources()
    analytes = analyte_names(lookup)

    parts = []
    for year in sorted(years):
        df = year_summary(year, years[year], bbox, analytes)
        if not df.empty:
            parts.append(df)
    if not parts:
        raise RuntimeError("CEDEN returned nothing inside the study bbox")

    raw = pd.concat(parts, ignore_index=True)
    raw["lat"] = pd.to_numeric(raw["Latitude"], errors="coerce")
    raw["lon"] = pd.to_numeric(raw["Longitude"], errors="coerce")
    raw["n_obs"] = pd.to_numeric(raw["n_obs"], errors="coerce").fillna(0).astype(int)
    raw = raw[raw["lat"].notna() & raw["lon"].notna()].copy()

    by_name = dict(lookup.by_wqp_name)
    by_name.update(EXTRA_ALIASES)
    raw["parameter"] = raw["Analyte"].astype(str).str.strip().str.lower().map(by_name)

    unmapped = raw[raw["parameter"].isna()]["Analyte"].value_counts()
    if not unmapped.empty:
        log.info("ceden: %d analytes returned but not in the crosswalk: %s",
                 len(unmapped),
                 ", ".join(f"{n} ({c})" for n, c in unmapped.head(10).items()))
    raw = raw[raw["parameter"].notna()].copy()

    # One coordinate per station, the busiest rather than the mean, so the
    # point stays on a published position.
    w = (raw.groupby(["StationCode", "lat", "lon"], as_index=False)["n_obs"].sum()
            .sort_values(["StationCode", "n_obs"], ascending=[True, False]))
    coords = w.groupby("StationCode", as_index=False).head(1)[
        ["StationCode", "lat", "lon"]]
    moved = int((w.groupby("StationCode").size() > 1).sum())
    if moved:
        log.info("ceden: %d stations publish more than one coordinate, "
                 "using the most-reported one", moved)

    names = (raw.sort_values("n_obs", ascending=False)
                .drop_duplicates("StationCode")[["StationCode", "StationName"]])
    st = coords.merge(names, on="StationCode", how="left")

    gdf = gpd.GeoDataFrame(
        pd.DataFrame({
            "source_station_id": st["StationCode"].astype(str),
            "name": st["StationName"].fillna(st["StationCode"]),
            "operator": "CEDEN contributing programs",
            "station_type": "discrete_grab",
            "lat": st["lat"].astype(float),
            "lon": st["lon"].astype(float),
        }),
        geometry=gpd.points_from_xy(st["lon"].astype(float), st["lat"].astype(float)),
        crs="EPSG:4326",
    ).reset_index(drop=True)
    gdf["source"] = SOURCE
    gdf["url"] = "https://ceden.waterboards.ca.gov/AdvancedQueryTool"
    gdf["access"] = "api"

    for col in ("por_start", "por_end"):
        raw[col] = pd.to_datetime(raw[col], errors="coerce")
    sp = (raw.groupby(["StationCode", "parameter", "Analyte", "Unit"], dropna=False)
             .agg(por_start=("por_start", "min"),
                  por_end=("por_end", "max"),
                  n_obs=("n_obs", "sum"))
             .reset_index()
             .rename(columns={"StationCode": "source_station_id",
                              "Analyte": "parameter_source_name",
                              "Unit": "unit"}))
    sp["source"] = SOURCE
    # Every CEDEN record is a sample somebody collected, so the record type is
    # settled. The frequency is not, and CEDEN states none, so it comes from
    # the count over the span the yearly query already returns. Without it all
    # 1,638 rows read `unknown`, which puts a monthly irrigated-lands
    # program in the same bucket as two samples thirty years apart.
    sp["median_interval_days"], sp["frequency"] = classify.measured_frequency(
        sp["por_start"], sp["por_end"], sp["n_obs"],
        classify.SAMPLE_COUNT_BANDS)
    sp["record_type"] = "discrete"
    log.info("ceden: frequency from the reported count over span %s",
             sp["frequency"].value_counts().to_dict())
    log.info("ceden: %d stations, %d station-parameter rows", len(gdf), len(sp))
    return gdf, sp

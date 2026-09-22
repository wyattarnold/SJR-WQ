"""CDEC (California Data Exchange Center) station harvest.

CDEC has no station metadata web service. Two HTML endpoints cover what the
inventory needs:

  staSearch  - station list filtered by sensor number and lat/lon box
  staMeta    - per-station page with coordinates, operator, and one row per
               sensor giving sensor number, duration code, collection method,
               and period of record

The staMeta period of record is the reason this module scrapes rather than
probing CSVDataServlet: availability comes free with the metadata, so measuring
a record's length costs no download.
"""

from __future__ import annotations

import html
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from .. import classify
from ..config import ParameterLookup, fetch, log

SEARCH = "https://cdec.water.ca.gov/dynamicapp/staSearch"
META = "https://cdec.water.ca.gov/dynamicapp/staMeta"
SOURCE = "cdec"

# Duration codes CDEC uses in the staMeta sensor table. CDEC publishes one per
# sensor, which is why record type and frequency are per-sensor columns rather
# than a station attribute: a station can record stage every 15 minutes and
# storage once a day.
DURATION_TO_FREQUENCY = {
    "event": "sub-daily",
    "hourly": "sub-daily",
    "daily": "daily",
    "monthly": "monthly",
}


def _cells(row_html: str) -> list[str]:
    txt = html.unescape(re.sub(r"<[^>]+>", "\t", row_html))
    return [c.strip() for c in txt.split("\t") if c.strip()]


def search_stations(sensor: int, bbox: list[float]) -> pd.DataFrame:
    """Stations reporting `sensor` inside bbox = [west, south, east, north]."""
    west, south, east, north = bbox
    params = {
        "sta": "", "sensor_chk": "on", "sensor": str(sensor),
        "collect": "NONE SPECIFIED", "dur": "", "active": "",
        "loc_chk": "on", "lon1": west, "lon2": east, "lat1": south, "lat2": north,
        "elev1": -5, "elev2": 99000, "nearby": "",
        "basin": "NONE SPECIFIED", "hydro": "NONE SPECIFIED",
        "county": "NONE SPECIFIED", "agency_num": "", "display": "sta",
    }
    txt = fetch(SEARCH, SOURCE, params=params, label=f"search_sensor{sensor}.html")
    rows = []
    for r in re.findall(r"<tr>(.*?)</tr>", txt, flags=re.DOTALL | re.IGNORECASE):
        if "staMeta" not in r:
            continue
        c = _cells(r)
        if len(c) < 7:
            continue
        try:
            lon, lat = float(c[4]), float(c[5])
        except ValueError:
            continue
        rows.append({
            "station_id": c[0], "name": c[1], "river_basin": c[2], "county": c[3],
            "lon": lon, "lat": lat, "elevation_ft": c[6],
            "operator": c[7] if len(c) > 7 else "",
            "sensor": sensor,
        })
    log.info("cdec sensor %s -> %d stations", sensor, len(rows))
    return pd.DataFrame(rows)


def station_sensors(station_id: str) -> pd.DataFrame:
    """Parse the staMeta sensor table: one row per sensor and duration."""
    txt = fetch(META, SOURCE, params={"station_id": station_id},
                label=f"meta_{station_id}.html")
    block = txt[txt.find("Sensor Number"):]
    rows = []
    for r in re.findall(r"<tr>(.*?)</tr>", block, flags=re.DOTALL | re.IGNORECASE):
        c = _cells(r)
        # sensor rows look like: [desc, ', UNITS', num, '(', dur, ')', '(', plot, ')', collect, POR]
        if len(c) < 8 or not c[2].isdigit():
            continue
        por = c[-1]
        m = re.match(r"(\d{2}/\d{2}/\d{4})\s+to\s+(present|\d{2}/\d{2}/\d{4})", por)
        start = pd.to_datetime(m.group(1), format="%m/%d/%Y") if m else pd.NaT
        end_raw = m.group(2) if m else ""
        end = pd.Timestamp.today().normalize() if end_raw == "present" else (
            pd.to_datetime(end_raw, format="%m/%d/%Y", errors="coerce") if end_raw else pd.NaT
        )
        rows.append({
            "station_id": station_id,
            "sensor": int(c[2]),
            "description": c[0],
            "unit": c[1].lstrip(", "),
            "duration": c[4] if len(c) > 4 else "",
            "collection": c[-2],
            "por_start": start,
            "por_end": end,
            "por_ongoing": end_raw == "present",
        })
    return pd.DataFrame(rows)


def station_notes(station_id: str) -> pd.DataFrame:
    """The dated comment log at the bottom of a staMeta page.

    Records rating changes, buried orifice lines, sensor swaps, and coordinate
    corrections. Worth keeping, because those events show up as step changes in
    a calibration time series.
    """
    txt = fetch(META, SOURCE, params={"station_id": station_id},
                label=f"meta_{station_id}.html")
    rows = []
    for r in re.findall(r"<tr>(.*?)</tr>", txt, flags=re.DOTALL | re.IGNORECASE):
        c = _cells(r)
        if len(c) == 2 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", c[0]):
            rows.append({"station_id": station_id,
                         "date": pd.to_datetime(c[0], format="%m/%d/%Y"),
                         "note": c[1]})
    return pd.DataFrame(rows)


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (stations, station_parameter, notes) for every relevant sensor."""
    lookup = lookup or ParameterLookup.build()
    sensors = sorted(lookup.by_cdec_sensor)

    found = [search_stations(s, bbox) for s in sensors]
    found = [f for f in found if not f.empty]
    if not found:
        raise RuntimeError("CDEC search returned nothing; check the bbox or endpoint")
    hits = pd.concat(found, ignore_index=True)

    stations = (hits.drop(columns="sensor")
                    .drop_duplicates(subset="station_id")
                    .reset_index(drop=True))
    log.info("cdec: %d unique stations across %d sensors", len(stations), len(sensors))

    sp, notes = [], []
    for i, sid in enumerate(stations["station_id"], 1):
        if i % 50 == 0:
            log.info("cdec staMeta %d/%d", i, len(stations))
        try:
            sp.append(station_sensors(sid))
            notes.append(station_notes(sid))
        except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
            log.warning("cdec staMeta failed for %s: %s", sid, exc)

    sp = pd.concat([d for d in sp if not d.empty], ignore_index=True)
    notes = pd.concat([d for d in notes if not d.empty], ignore_index=True) \
        if any(not d.empty for d in notes) else pd.DataFrame()

    sp["parameter"] = sp["sensor"].map(lookup.by_cdec_sensor)
    sp["frequency"] = sp["duration"].map(DURATION_TO_FREQUENCY).fillna("unknown")
    sp["record_type"] = np.where(
        sp["frequency"].isin(classify.CONTINUOUS_FREQUENCIES),
        "continuous", "discrete")
    sp = sp[sp["parameter"].notna()].reset_index(drop=True)

    gdf = gpd.GeoDataFrame(
        stations,
        geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
        crs="EPSG:4326",
    )
    gdf["source"] = SOURCE
    gdf["source_station_id"] = gdf["station_id"]
    gdf["url"] = META + "?station_id=" + gdf["station_id"]
    gdf["access"] = "api"
    return gdf, sp, notes

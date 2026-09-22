"""Water Quality Portal harvest, using the WQX 3.0 services.

One sweep of the WQP picks up CEDEN, SWAMP, the irrigated lands coalitions,
USGS discrete samples, EPA STORET, and local dischargers, because they publish
through it. That makes it the highest-yield source for discrete water quality
in the basin.

Three things to know about the endpoints:

  - WQX 3.0 is at /wqx3/, not /beta/. The beta path returns 404 as of
    2026-08-19.
  - The legacy /data/ (WQX 2.2) profiles omit USGS results added after
    2024-03-11, so this module skips them.
  - The summary/monitoringLocation service returns an empty body, so this
    module computes period of record from the downloaded results.
"""

from __future__ import annotations

import io

import geopandas as gpd
import pandas as pd

from .. import classify
from ..config import ParameterLookup, fetch, log

BASE = "https://www.waterqualitydata.us/wqx3"
SOURCE = "wqp"

# The portal's gzip encoder truncates on slow result queries. See results().
NO_GZIP = {"Accept-Encoding": "identity"}

# Synthetic USGS records published through the portal. See harvest().
TEST_SITE_ID = r"1231231231231\d\d"

# Media the portal reports that are not water. The narrow profile carries
# Activity_Media on 137,277 of its rows and leaves it null on 45,338 more, so
# the filter names what to drop rather than what to keep: a null media on a
# temperature result is an unfilled field, and dropping it would cost 29,895
# water temperatures.
#
# Sediment is the one that matters. The NURE stream-sediment survey ran 1977 to
# 1980 and reports boron, selenium, and the major cations as milligrams per
# kilogram of dry sediment, which is a different quantity from the same element
# in water.
NON_WATER_MEDIA = {"Sediment", "Tissue", "Biological", "Other", "Air", "Soil",
                   "Habitat", "Sediment Pore Water"}

# Organizations whose records are not observations of this basin's water.
# DEMOTEST_WQX is the portal's own demonstration account and republishes real
# gages under a prefixed identifier, so DEMOTEST_WQX-USGS-11303000 is a second
# copy of Stanislaus at Ripon. The Air Quality System is EPA's air monitoring
# network, which shares the portal but measures the atmosphere.
JUNK_ORGS = {"DEMOTEST_WQX", "Air Quality System"}

# Characteristics the portal refused to serve, by HUC. Reported at the end of
# the harvest so coverage gaps are visible rather than silent.
DROPPED: dict[str, list[str]] = {}

# What the two endpoints returned, filled in by `harvest`. The gap analysis
# quotes both, and neither survives into `data/processed`: the locations that
# report no target parameter leave at the harvest, so counting them later is
# impossible. `sjrwq.harvest` writes this beside the interim files.
# `locations_from_station_endpoint` counts the distinct locations Station/search
# returned, `locations_filtered_out` the ones the coordinate, test-site, and
# JUNK_ORGS filters removed, and `locations_dropped` the ones left after those
# filters that report no target characteristic.
COUNTS: dict[str, int] = {}

LOCATION_TYPE_TO_STATION_TYPE = {
    "River/Stream": "discrete_grab",
    "Canal Drainage": "drain",
    "Canal Irrigation": "canal",
    "Canal Transport": "canal",
    "Lake": "reservoir",
    "Reservoir": "reservoir",
    "Well": "groundwater_well",
    "Facility Municipal Sewage (POTW)": "effluent",
    "Facility Other": "effluent",
    "Estuary": "estuary",
}


def stations(huc8: str, bbox: list[float] | None = None) -> pd.DataFrame:
    params = {"huc": huc8, "mimeType": "csv"}
    if bbox:
        params["bBox"] = ",".join(str(b) for b in bbox)
    txt = fetch(f"{BASE}/Station/search", SOURCE, params=params,
                label=f"station_{huc8}.csv")
    df = pd.read_csv(io.StringIO(txt), low_memory=False)
    log.info("wqp %s -> %d stations", huc8, len(df))
    return df


def _result_query(characteristics: list[str], label: str,
                  huc8: str | None = None,
                  bbox: list[float] | None = None,
                  retries: int = 4,
                  backoff: float = 30.0) -> pd.DataFrame:
    params = [("mimeType", "csv"), ("dataProfile", "narrow")]
    if huc8:
        params.append(("huc", huc8))
    if bbox:
        params.append(("bBox", ",".join(str(b) for b in bbox)))
    params += [("characteristicName", c) for c in characteristics]
    txt = fetch(f"{BASE}/Result/search", SOURCE, params=params,
                label=label, timeout=900, retries=retries, backoff=backoff,
                headers=NO_GZIP)
    if not txt.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(txt), low_memory=False)


def results(huc8: str, characteristics: list[str],
            bbox: list[float] | None = None,
            batch_size: int = 6) -> pd.DataFrame:
    """Narrow-profile results for the requested characteristics.

    The narrow profile is the most compact shape available, and the inventory
    needs identifier, date, and characteristic rather than the full analytical
    metadata.

    **Ask for identity encoding.** This is the setting that matters. The portal
    advertises gzip, and requests asks for it by default, but its encoder
    truncates on a result query slow enough to matter. The connection closes
    mid-body and urllib3 raises "Response ended prematurely", which looks like
    a query the server refuses and is not one. The same six characteristics
    over HUC 18040011 fail four times running under gzip and return 778 rows in
    90 seconds under identity. All 36 return 5,023 rows in 99 seconds. Gateway
    504s on the same queries are the same fault surfacing one layer up.

    **Cost is flat.** Once the encoding is right, a query costs about 90
    seconds no matter what it asks for, and the time does not track the rows
    returned. Six characteristics over one HUC8 take 90 seconds for 778 rows;
    all 36 take 99 seconds for 5,023. The portal scans on the HUC filter, so a
    narrow query saves no time. Ask for everything at once.

    Both facts point the same way: narrowing a failed query is wasted effort.
    Batching here is damage control for a portal outage, and this module logs a
    batch that still fails so the caller can record the gap.
    """
    dropped: list[str] = []
    try:
        df = _result_query(characteristics, f"result_{huc8}.csv", huc8=huc8, bbox=bbox)
        if not df.empty:
            log.info("wqp %s -> %d results", huc8, len(df))
        return df
    except Exception:  # noqa: BLE001 - the portal is having a patch, split and wait
        log.warning("wqp %s: combined query failed, switching to batches of %d",
                    huc8, batch_size)

    parts = []
    batches = [characteristics[i:i + batch_size]
               for i in range(0, len(characteristics), batch_size)]
    for n, chunk in enumerate(batches):
        try:
            df = _result_query(chunk, f"result_{huc8}_b{n}.csv", huc8=huc8, bbox=bbox)
        except Exception:  # noqa: BLE001 - record the loss, keep going
            dropped.extend(chunk)
            log.warning("wqp %s: batch %d/%d failed, dropping %s",
                        huc8, n + 1, len(batches), ", ".join(chunk))
            continue
        if not df.empty:
            parts.append(df)

    if dropped:
        DROPPED.setdefault(huc8, []).extend(dropped)
    if not parts:
        log.warning("wqp %s -> no results", huc8)
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    log.info("wqp %s -> %d results (batched, %d characteristics dropped)",
             huc8, len(df), len(dropped))
    return df


def _median_interval(dates: pd.Series) -> float | None:
    """Median days between distinct sampling dates.

    The portal is the one source that publishes every sample date, so it is
    the one where frequency comes from a measurement rather than an assumption.
    This function collapses duplicate dates first: a profile or a replicate set
    produces several rows on one day and would otherwise come out at an
    interval of zero.

    A single sample has no interval, so the result is None rather than zero.
    """
    d = pd.Series(pd.to_datetime(dates, errors="coerce")).dropna().unique()
    if len(d) < 2:
        return None
    gaps = pd.Series(sorted(d)).diff().dropna().dt.total_seconds() / 86400.0
    return float(gaps.median())


def harvest(huc8_list: list[str], lookup: ParameterLookup | None = None,
            huc_bbox: dict[str, list[float]] | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Harvest the listed HUC8s.

    huc_bbox restricts a HUC to a bounding box. The Delta HUC uses it: the
    inventory needs Vernalis and Mossdale from that HUC, and pulling the whole
    Delta is slow and mostly discarded by the study-area clip.
    """
    lookup = lookup or ParameterLookup.build()
    characteristics = lookup.wqp_characteristic_names()
    huc_bbox = huc_bbox or {}

    st_parts, res_parts = [], []
    for huc8 in huc8_list:
        bbox = huc_bbox.get(huc8)
        st_parts.append(stations(huc8, bbox=bbox))
        r = results(huc8, characteristics, bbox=bbox)
        if not r.empty:
            res_parts.append(r)

    st = pd.concat(st_parts, ignore_index=True).drop_duplicates("Location_Identifier")
    res = pd.concat(res_parts, ignore_index=True) if res_parts else pd.DataFrame()
    n_endpoint = len(st)

    # A location with no coordinates cannot be clipped to the study area, so the
    # harvest drops it here and drops its results at the intersection below.
    unplaced = st["Location_Latitude"].isna() | st["Location_Longitude"].isna()
    if unplaced.any():
        n_results = (int(res["Location_Identifier"].isin(
            set(st.loc[unplaced, "Location_Identifier"])).sum()) if not res.empty else 0)
        log.info("wqp: dropped %d locations without coordinates, holding %d results",
                 int(unplaced.sum()), n_results)
        st = st[~unplaced].copy()

    # USGS publishes a handful of synthetic records through the portal under
    # made-up site numbers like 123123123123123, named "ITT test site N". They
    # hold no results, so they reach no figure, and they do inflate the station
    # count. Drop them by the repeated-digit ID rather than by name,
    # because the names are inconsistently capitalised.
    junk = st["Location_Identifier"].astype(str).str.contains(TEST_SITE_ID)
    if junk.any():
        log.info("wqp: dropped %d synthetic USGS test sites", int(junk.sum()))
        st = st[~junk].copy()

    for col in ("Org_Identifier", "Org_FormalName"):
        if col not in st.columns:
            continue
        bad = st[col].astype(str).isin(JUNK_ORGS)
        if bad.any():
            for org, n in st.loc[bad, col].value_counts().items():
                log.info("wqp: dropped %d stations from %s", n, org)
            st = st[~bad].copy()
    gdf = gpd.GeoDataFrame(
        pd.DataFrame({
            "source_station_id": st["Location_Identifier"],
            "name": st["Location_Name"],
            "operator": st["Org_FormalName"],
            "organization": st["Org_Identifier"],
            "provider": st["ProviderName"],
            "location_type": st["Location_Type"],
            "county": st.get("Location_CountyName"),
            "huc12_wqp": st.get("Location_HUCTwelveDigitCode"),
            "lat": st["Location_Latitude"].astype(float),
            "lon": st["Location_Longitude"].astype(float),
        }),
        geometry=gpd.points_from_xy(st["Location_Longitude"].astype(float),
                                    st["Location_Latitude"].astype(float)),
        crs="EPSG:4326",
    ).reset_index(drop=True)
    gdf["source"] = SOURCE
    gdf["station_type"] = gdf["location_type"].map(LOCATION_TYPE_TO_STATION_TYPE) \
        .fillna("discrete_grab")
    gdf["url"] = "https://www.waterqualitydata.us/provider/" + \
        gdf["provider"].astype(str) + "/" + gdf["organization"].astype(str) + \
        "/" + gdf["source_station_id"].astype(str) + "/"
    gdf["access"] = "api"

    if res.empty:
        return gdf, pd.DataFrame()

    # Results belonging to a station the filters just removed would otherwise
    # survive as orphans and inflate the row counts logged below.
    res = res[res["Location_Identifier"].isin(set(st["Location_Identifier"]))].copy()

    if "Activity_Media" in res.columns:
        wrong = res["Activity_Media"].isin(NON_WATER_MEDIA)
        if wrong.any():
            for media, n in res.loc[wrong, "Activity_Media"].value_counts().items():
                log.info("wqp: dropped %d results sampled from %s", n, media)
            res = res[~wrong].copy()

    res["parameter"] = res["Result_Characteristic"].str.strip().str.lower() \
        .map(lookup.by_wqp_name)
    res = res[res["parameter"].notna()].copy()
    res["date"] = pd.to_datetime(res["Activity_StartDate"], errors="coerce")

    # The portal states the reporting basis in Result_MethodSpeciation ("as N",
    # "as NH3", "as PO4") and leaves it out of the unit, so 1,365 ammonia
    # results at 36 locations arrive in plain mg/L as NH3. The name takes the
    # basis in the "(as X)" form `units.restate_basis` parses, and the name is
    # part of the grouping key, so each basis at a location gets its own row
    # and its own factor.
    name = res["Result_Characteristic"]
    if "Result_MethodSpeciation" in res.columns:
        spec = res["Result_MethodSpeciation"].astype("string").str.strip()
        stated = spec.fillna("").ne("")
        name = name.where(~stated, name + " (" + spec + ")")
    res["parameter_source_name"] = name

    # One row per stated unit, as eSMR does: a location that logged water
    # temperature in deg F and later in deg C gets two rows and two factors.
    # A result with no unit takes the location's commonest stated unit.
    key = ["Location_Identifier", "parameter", "parameter_source_name"]
    unit = res["Result_MeasureUnit"].astype("string").str.strip().replace("", pd.NA)
    modal = (unit.groupby([res[k] for k in key], dropna=False)
                 .transform(lambda s: s.dropna().mode().iloc[0]
                            if not s.dropna().empty else pd.NA))
    res["unit"] = unit.fillna(modal)
    mixed = res.groupby(key, dropna=False)["unit"].nunique()
    if (mixed > 1).any():
        log.info("wqp: %d location-parameter groups report more than one unit and "
                 "split into one row per unit (%s)", int((mixed > 1).sum()),
                 mixed[mixed > 1].reset_index()["parameter"].value_counts().to_dict())

    sp = (res.groupby(key + ["unit"], dropna=False)
             .agg(por_start=("date", "min"),
                  por_end=("date", "max"),
                  n_obs=("date", "size"),
                  median_interval_days=("date", _median_interval))
             .reset_index()
             .rename(columns={"Location_Identifier": "source_station_id"}))
    sp["unit"] = sp["unit"].astype(object).where(sp["unit"].notna(), None)
    sp["source"] = SOURCE
    # Every portal result is a sample somebody collected, so the record type is
    # settled. The frequency is not, and the portal is the one source that
    # publishes every sample date, so `_median_interval` measures it rather
    # than assuming it. That separates a monthly sampling program from two
    # samples thirty years apart, which one label for the whole source could
    # not. The log line below counts what each band picked up.
    sp["record_type"] = "discrete"
    sp["frequency"] = [classify.frequency_from_interval(v)
                       for v in sp["median_interval_days"]]
    log.info("wqp: frequency from the measured sampling interval %s",
             sp["frequency"].value_counts().to_dict())

    # Keep only the locations that returned one of the target parameters.
    #
    # The two WQP endpoints return different things. Station/search returns
    # every monitoring location in the HUC; Result/search returns this
    # inventory's characteristic list. Keeping the difference puts 7,802
    # locations into the catalog with no parameter attached, which the figures
    # then show as stations with no data. Those locations hold groundwater
    # levels, nutrients, pesticides, and bacteria, all outside this inventory's
    # 36 characteristics. A location that measures 0 target parameters is
    # outside the inventory, so it goes here rather than to the figures as an
    # empty row.
    keep = set(sp["source_station_id"])
    dropped = len(gdf) - int(gdf["source_station_id"].isin(keep).sum())
    if dropped:
        log.info("wqp: dropped %d locations reporting none of the %d target "
                 "characteristics", dropped, len(characteristics))
    COUNTS.update({
        "locations_from_station_endpoint": n_endpoint,
        "locations_filtered_out": n_endpoint - len(gdf),
        "locations_with_a_target_parameter": len(keep),
        "locations_dropped": dropped,
        "characteristics_requested": len(characteristics),
        "locations_publishing_a_huc12": int(
            gdf["huc12_wqp"].notna().sum()) if "huc12_wqp" in gdf.columns else 0,
    })
    gdf = gdf[gdf["source_station_id"].isin(keep)].reset_index(drop=True)

    log.info("wqp: %d stations, %d station-parameter rows", len(gdf), len(sp))
    return gdf, sp

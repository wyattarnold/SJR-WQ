"""Merge per-source harvests into the canonical inventory.

    python -m sjrwq.inventory

Reads data/interim/*, clips to the study area, deduplicates across sources,
and writes:

    data/processed/stations.gpkg          layer `stations`, one row per source record
    data/processed/station_parameter.parquet
    data/processed/catalog.csv            one row per deduplicated station
    data/processed/catalog_filtered.csv   the temperature + EC shortlist
    data/processed/coverage_sets.csv      the concurrent-coverage sets, named
    data/processed/funnel.csv             what each filtering step removes
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from . import classify, coverage, units
from .config import INTERIM, PROCESSED, ParameterLookup, domain_config, log
from .coverage import station_level
from .dedupe import assign_station_uid
from .domain import classify_points

SOURCES = ["usgs", "cdec", "wqp", "esmr", "ceden", "dwr_wdl", "usbr_rise",
           "edi"]

CANONICAL = [
    "station_uid", "is_primary", "n_sources", "source", "source_station_id",
    "name", "operator", "station_type", "channel_class", "conveyance_role",
    "lat", "lon",
    "huc8", "huc8_name", "huc12", "waterbody", "horizontal_datum",
    "river", "mainstem_km",
    "distance_to_river_m", "boundary", "anchor", "county", "url",
    "n_parameters", "por_start", "por_end", "last_obs", "years_since_last",
    "active", "record_type", "frequency", "parameter_groups", "parameters",
]

# One copy of the frequency order, in classify with the vocabulary it ranks.
FREQUENCY_RANK = classify.FREQUENCY_RANK


def _load_source(source: str) -> tuple[gpd.GeoDataFrame | None, pd.DataFrame | None]:
    st_path = INTERIM / f"{source}_stations.gpkg"
    vp = INTERIM / f"{source}_station_parameter.parquet"
    if not st_path.exists():
        log.warning("no interim file for %s, skipping", source)
        return None, None
    st = gpd.read_file(st_path, layer="stations")
    sp = pd.read_parquet(vp) if vp.exists() else pd.DataFrame()
    if "source" not in st.columns:
        st["source"] = source
    if not sp.empty and "source" not in sp.columns:
        sp["source"] = source
    # CDEC keys its availability table on station_id, not source_station_id.
    if not sp.empty and "source_station_id" not in sp.columns and "station_id" in sp.columns:
        sp = sp.rename(columns={"station_id": "source_station_id"})
    log.info("%s: %d stations, %d station-parameter rows", source, len(st), len(sp))
    return st, sp


# No instrumental hydrologic record in this basin predates the 1850s, so an
# earlier date is a source typo rather than a very long record.
EARLIEST_PLAUSIBLE = pd.Timestamp("1850-01-01")


def _guard_dates(sp: pd.DataFrame) -> pd.DataFrame:
    """Null out implausible periods of record and log the drops.

    Agencies do publish bad dates. CDEC station SMN advertises a river stage
    record starting 03/01/0010, while the same page shows 03/01/2010 for
    another sensor. Left alone, that one typo produces a 2,000-year record
    that rescales every figure. This function nulls rather than corrects:
    reading 0010 as 2010 is a guess, and a missing date is more honest than a
    fabricated one.
    """
    today = pd.Timestamp.today().normalize()
    too_early = sp["por_start"].notna() & (sp["por_start"] < EARLIEST_PLAUSIBLE)
    too_late = sp["por_end"].notna() & (sp["por_end"] > today + pd.Timedelta(days=1))
    reversed_ = (sp["por_start"].notna() & sp["por_end"].notna()
                 & (sp["por_end"] < sp["por_start"]))

    for mask, col, why in ((too_early, "por_start", "before 1850"),
                           (too_late, "por_end", "in the future"),
                           (reversed_, "por_start", "end before start")):
        if mask.any():
            for _, r in sp.loc[mask, ["station_uid", "source", "parameter",
                                      "por_start", "por_end"]].iterrows():
                log.warning("implausible period of record (%s), nulling: %s %s %s %s to %s",
                            why, r["source"], r["station_uid"], r["parameter"],
                            r["por_start"], r["por_end"])
            sp.loc[mask, col] = pd.NaT
    return sp


# A record shorter than this, ending before the calibration period opens, is a
# campaign rather than a monitoring record.
SHORT_RECORD_YEARS = 2.0


def _drop_short_historic(sp: pd.DataFrame,
                         min_years: float = SHORT_RECORD_YEARS) -> pd.DataFrame:
    """Remove brief records that end before the calibration period.

    The inventory exists to support a model aimed at 2000 onward. A record that
    ran for eighteen months and stopped in 1979 calibrates no part of that
    window, and it cannot establish a baseline either, because eighteen
    months does not span a wet year and a dry one.

    They are not harmless. 2,814 of the 2,865 rows this removes are Water
    Quality Portal grab campaigns, and 447 stations have no other row. 45 of
    those come from one National Park Service survey of the upper Merced and
    Tuolumne, sampled 1978 to 1980 and not revisited, which shows up as a spike
    in the count-through-time figure and as 45 markers in the headwaters of
    each map.

    The cutoff is the calibration period start from config/domain.yml, so
    moving the window moves this with it.

    The test runs on a source's whole record of a parameter at a station, not
    on each row. WQP writes one row per reporting basis and per unit, so a
    40-year nitrate record reported as N and then as NO3 arrives as two rows,
    and either row alone can be shorter than the cutoff.
    """
    start = pd.Timestamp(domain_config()["calibration_period"]["start"])
    record = sp.groupby(["source", "source_station_id", "parameter"], dropna=False)
    first = record["por_start"].transform("min")
    last = record["por_end"].transform("max")
    span = (last - first).dt.days / 365.25
    short = (span < min_years) & last.notna() & (last < start)
    if short.any():
        by_source = sp.loc[short, "source"].value_counts().to_dict()
        kept = set(sp.loc[~short, "station_uid"])
        lost = len(set(sp.loc[short, "station_uid"]) - kept)
        log.info("dropped %d station-parameter rows shorter than %g years and "
                 "ending before %s (%s); %d stations had nothing else",
                 int(short.sum()), min_years, start.date(),
                 ", ".join(f"{k} {v}" for k, v in by_source.items()), lost)
    return sp[~short].reset_index(drop=True)


def _add_position(stations: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach the nearest mapped river and the distance along it.

    River kilometer lands on the station record here, so the catalog sorts
    downstream to upstream, which is the order a modeller reads a river in.
    `maps.fig_longitudinal` and `gaps.mainstem_gaps` both work from the same
    measure.
    """
    from .rivers import ON_CHANNEL_M, merged_mainstem_line, river_distance_km

    try:
        lines = gpd.read_file(PROCESSED / "domain.gpkg", layer="mainstem")
        tribs = gpd.read_file(PROCESSED / "domain.gpkg", layer="tributaries")
    except Exception:  # noqa: BLE001 - a missing domain layer is not fatal
        log.warning("domain.gpkg has no river layers; skipping river_km")
        for col in ("river", "mainstem_km", "distance_to_river_m"):
            stations[col] = None
        return stations

    crs_area = domain_config()["crs"]["area"]
    both = pd.concat([lines, tribs], ignore_index=True)
    rivers = gpd.GeoDataFrame(both, geometry="geometry",
                              crs=lines.crs).to_crs(crs_area).dissolve(by="river")
    pts = stations.to_crs(crs_area)

    # Nearest mapped river to each station, and how far off it sits. A station
    # 20 km from the nearest mapped channel is on a tributary this inventory
    # does not trace, so the distance goes in a column rather than out of
    # sight.
    near = gpd.sjoin_nearest(pts[["geometry"]], rivers.reset_index()[["river", "geometry"]],
                             how="left", distance_col="distance_to_river_m")
    near = near[~near.index.duplicated(keep="first")]
    dist = near["distance_to_river_m"].round(1)
    # Nearest is not the same as on. Cal Aqueduct Check 13 is nearest to the
    # San Joaquin and sits on the California Aqueduct, tens of kilometers away.
    # Only a station within ON_CHANNEL_M is named for a river; the rest keep
    # the distance so the miss is visible instead of silent.
    stations["river"] = near["river"].where(dist <= ON_CHANNEL_M).values
    stations["distance_to_river_m"] = dist.values

    ms = merged_mainstem_line(lines, crs_area)
    stations["mainstem_km"] = river_distance_km(stations, ms, crs_area).round(2)
    return stations


def _add_waterbody(stations: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep the receiving water body each source already names.

    eSMR names the receiving water for every outfall and CDEC names a river
    basin for every station. Without this step the catalog cannot settle "what
    does this outfall discharge into" without a trip back to the interim files.
    """
    parts = []
    for col in ("receiving_water_body", "river_basin", "waterbody"):
        if col in stations.columns:
            parts.append(stations[col])
    if not parts:
        stations["waterbody"] = None
        return stations
    wb = parts[0]
    for other in parts[1:]:
        wb = wb.fillna(other)
    stations["waterbody"] = wb.replace({"": None})
    return stations


def build() -> tuple[gpd.GeoDataFrame, pd.DataFrame, dict[str, int]]:
    """The inventory, plus the count surviving each stage of the build.

    `funnel` needs the two counts that exist only inside this function: what
    the harvests returned before the study-area clip, and what survived it.
    Neither reaches `data/processed`, so figure 09 drew its first bar from the
    stations that came out the far end and understated the filtering
    threefold.
    """
    lookup = ParameterLookup.build()
    stages: dict[str, int] = {}
    st_parts, sp_parts = [], []
    for source in SOURCES:
        st, sp = _load_source(source)
        if st is None:
            continue
        st_parts.append(st)
        if sp is not None and not sp.empty:
            sp_parts.append(sp)

    if not st_parts:
        raise RuntimeError("no interim harvests found; run `python -m sjrwq.harvest` first")

    stations = pd.concat(st_parts, ignore_index=True)
    stations = gpd.GeoDataFrame(stations, geometry="geometry", crs="EPSG:4326")
    sp = pd.concat(sp_parts, ignore_index=True)
    stages["harvested"] = len(stations)

    # Clip to the study area, keeping the flagged downstream boundary sites.
    stations = classify_points(stations)
    n_before = len(stations)
    stations = stations[stations["in_aoi"]].reset_index(drop=True)
    stages["in_aoi"] = len(stations)
    log.info("clipped to study area: %d -> %d stations (%d boundary)",
             n_before, len(stations), int(stations["boundary"].sum()))

    stations = assign_station_uid(stations)

    # Keep only availability rows whose station survived the clip, then attach
    # the uid so availability can be rolled up per physical site.
    key = stations[["source", "source_station_id", "station_uid"]].drop_duplicates()
    sp = sp.merge(key, on=["source", "source_station_id"], how="inner")
    sp["group"] = sp["parameter"].map(lookup.groups)
    for col in ("por_start", "por_end"):
        sp[col] = pd.to_datetime(sp[col], errors="coerce")
    sp = _drop_short_historic(_guard_dates(sp))

    # A station whose only records were short and historic leaves with them.
    # The inventory holds stations that report a target parameter, so
    # keeping an empty row would put it back on the maps as a location with no
    # data, which is the reading the WQP station filter exists to prevent.
    keep = set(sp["station_uid"])
    empty = ~stations["station_uid"].isin(keep)
    if empty.any():
        log.info("dropped %d stations left with no station-parameter row",
                 int(empty.sum()))
        stations = stations[~empty].reset_index(drop=True)

    # Record what conversion each source unit needs. The value stays as
    # published, and the row holds the factor so a later time-series pull skips
    # working out that CDEC sensor 25 reports Fahrenheit.
    for col, default in (("record_type", "discrete"), ("frequency", "unknown")):
        if col not in sp.columns:
            sp[col] = default
        sp[col] = sp[col].fillna(default)
    sp = units.annotate(sp, lookup.units)
    bad = int((~sp["unit_convertible"]).sum())
    if bad:
        log.info("units: %d of %d station-parameter rows cannot be converted to "
                 "the canonical unit", bad, len(sp))
        for note, n in sp.loc[~sp["unit_convertible"], "unit_note"].value_counts().items():
            log.info("  %4d  %s", n, note)

    # Dates from the rows whose period of record belongs to the station. A
    # dataset-scope row holds its package's period, and a station taking its
    # catalog start date from one advertises decades it has no record of. A
    # station whose rows are all dataset-scope keeps those dates, because the
    # alternative leaves it undated.
    def _dates(frame: pd.DataFrame) -> pd.DataFrame:
        return (frame.groupby("station_uid")
                     .agg(por_start=("por_start", "min"),
                          por_end=("por_end", "max"))
                     .reset_index())

    dates = _dates(station_level(sp))
    fallback = _dates(sp)
    dates = pd.concat(
        [dates, fallback[~fallback["station_uid"].isin(set(dates["station_uid"]))]],
        ignore_index=True)
    dates["last_obs"] = dates["por_end"]

    roll = (sp.groupby("station_uid")
              .agg(n_parameters=("parameter", "nunique"),
                   record_type=("record_type",
                                lambda c: "continuous"
                                if (c == "continuous").any() else "discrete"),
                   frequency=("frequency",
                              lambda c: min(c, key=lambda x: FREQUENCY_RANK.get(x, 9))))
              .reset_index()
              .merge(dates, on="station_uid", how="left"))
    groups = (sp.dropna(subset=["group"]).groupby("station_uid")["group"]
                .agg(lambda s: ",".join(sorted(set(s)))).rename("parameter_groups").reset_index())
    varlist = (sp.groupby("station_uid")["parameter"]
                 .agg(lambda s: ",".join(sorted(set(s)))).rename("parameters").reset_index())

    stations = (stations.merge(roll, on="station_uid", how="left")
                        .merge(groups, on="station_uid", how="left")
                        .merge(varlist, on="station_uid", how="left"))
    stations["n_parameters"] = stations["n_parameters"].fillna(0).astype(int)

    stations = classify.add_channel_class(stations)
    stations = classify.refine_with_parameters(stations, sp)
    stations = classify.add_conveyance_role(stations)

    # CDEC publishes no station type. Its sensor duration codes imply one, and
    # without this backfill 48 hourly CDEC sondes count as discrete sampling
    # sites in the shortlist.
    missing = stations["station_type"].isna() | (stations["station_type"] == "")
    if missing.any():
        filled = [classify.station_type_from_frequency(rt, f, ch)
                  for rt, f, ch in zip(stations.loc[missing, "record_type"],
                                       stations.loc[missing, "frequency"],
                                       stations.loc[missing, "channel_class"],
                                       strict=True)]
        stations.loc[missing, "station_type"] = filled
        log.info("backfilled station_type for %d stations from the sampling "
                 "frequency", int(missing.sum()))

    cutoff_years = domain_config()["calibration_period"]["active_within_years"]
    today = pd.Timestamp.today().normalize()
    age = (today - pd.to_datetime(stations["last_obs"], errors="coerce")).dt.days / 365.25
    stations["years_since_last"] = age.round(1)
    stations["active"] = age.notna() & (age <= cutoff_years)

    stations = _add_waterbody(stations)
    stations = _add_position(stations)

    for col in CANONICAL:
        if col not in stations.columns:
            stations[col] = None
    stations = stations[CANONICAL + ["geometry"]]
    stages["with_a_record"] = len(stations)
    return (gpd.GeoDataFrame(stations, geometry="geometry", crs="EPSG:4326"),
            sp, stages)


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Make the CSVs readable by a human opening them in a spreadsheet.

    Coordinates arrive with seventeen significant figures and timestamps with
    a stray microsecond, both artefacts of round-tripping through the source
    APIs. Six decimal degrees is about ten centimeters, far finer than an
    agency knows where its gage is.
    """
    out = df.copy()
    for col in out.columns:
        if col in ("lat", "lon"):
            out[col] = out[col].astype(float).round(6)
        elif pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif col.endswith("_interval_days") or col in ("mainstem_km", "years_since_last"):
            out[col] = out[col].astype("Float64").round(1)
        elif col.endswith("_n_discrete"):
            # Nullable integer, so a missing count stays blank instead of
            # becoming "333.0" or, worse, "0".
            out[col] = out[col].astype("Float64").round().astype("Int64")
    return out


def catalog(stations: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per deduplicated physical station."""
    cat = stations[stations["is_primary"]].copy()
    src = (stations.groupby("station_uid")["source"]
                   .agg(lambda s: ",".join(sorted(set(s)))).rename("sources"))
    tagged = stations["source"] + ":" + stations["source_station_id"]
    ids = (tagged.groupby(stations["station_uid"])
                 .agg(lambda s: ";".join(sorted(s)))
                 .rename("all_source_ids"))
    cat = cat.merge(src, on="station_uid").merge(ids, on="station_uid")
    cols = ["station_uid", "name", "sources", "n_sources", "all_source_ids", "operator",
            "station_type", "channel_class", "conveyance_role",
            "record_type", "frequency", "lat", "lon",
            "huc8", "huc8_name", "huc12", "waterbody", "horizontal_datum",
    "river", "mainstem_km",
            "boundary", "n_parameters", "por_start", "por_end", "last_obs",
            "years_since_last", "active", "parameter_groups", "parameters", "url"]
    return _tidy(cat[cols].sort_values(["huc8", "name"]).reset_index(drop=True))


# Classes that are real measurements of something other than the modeled
# channel: an import, a return flow, or an aquifer. They belong in the
# inventory and they do not belong at the top of a river calibration shortlist,
# so the catalog flags them rather than dropping them.
#
# Cal Aqueduct Check 13 has a 36-year continuous record of temperature and
# conductance, which on record length alone sorts it above half the river
# gages. It is Delta water pumped south, an import term in a salt balance
# rather than an observation of the San Joaquin.
#
# `off_channel` rather than `boundary`, which `stations.gpkg` already uses for
# the two anchor buffers at the downstream edge of the study area. This set is
# not the complement of `gaps.RIVER_CLASSES`: `reservoir` and `unknown` sit in
# neither. A reservoir station is on the modeled water body and not on a river
# reach, so it belongs in the main block of the shortlist and out of the
# mainstem gap analysis.
OFF_CHANNEL_CLASSES = {"conveyance", "drain", "groundwater", "effluent"}


def calibration_catalog(cat: pd.DataFrame, sp: pd.DataFrame) -> pd.DataFrame:
    """Stations reporting water temperature or specific conductance.

    Most rows of the full catalog are one-off grab sites and groundwater
    wells. This frame holds the ones that measure a state variable
    of the model, with per-parameter start and end dates rather than the
    station-wide span, because a station whose EC record stopped in 2009 and
    whose temperature record began in 2015 measured both and can calibrate
    neither against the other.

    `usable` marks the shortlist: at least `min_record_years` of temperature or
    of conductance inside the calibration period. Either parameter, not both. A
    thermograph on the Tuolumne calibrates the temperature field whether or not
    somebody measures conductance beside it, and about half the stations on the
    shortlist measure one of the two. Co-location earns its keep for a salt load,
    where conductance and discharge have to sit at one point, so `has_pair`,
    `has_flow`, and `overlap_years_in_period` stay on the row as columns.

    The sample counts sum `n_obs` over the discrete rows. WQP, eSMR, CEDEN,
    and EDI publish a count. USGS and CDEC publish a period of record and no
    count, so a 41-year continuous gage is blank here. The sum skips
    continuous rows, where eSMR and EDI count logged readings rather than
    samples. Blank means the sources at the station reported no count of
    discrete samples, not that the record is empty, and that is the reason for
    `_n_discrete` rather than `_n_obs` in the column name.

    Sorted by overlap length, longest first, so the top of the file is where to
    start.

    Built from station-scope records only; see `coverage.station_level`.
    """
    core = list(coverage.CORE_PARAMETERS)
    # Station-scope records only. Every date in this table decides an outcome:
    # the start and end columns, the overlap, the sort order, and which
    # stations reach the shortlist. A dataset-scope row holds its package's
    # period of record, and 110 of them would date stations from 1950 whose
    # continuous monitors went in during the 1980s.
    sub = station_level(sp[sp["parameter"].isin(core)]).copy()
    sub["_cont"] = sub["record_type"] == "continuous"
    # The WQP, eSMR, CEDEN, and EDI modules write `n_obs` and
    # `median_interval_days` together, and the USGS, CDEC, WDL, and RISE
    # modules write neither. After a harvest of those four alone, `sp` has
    # neither column, and the loop adds both as blanks.
    for col in ("n_obs", "median_interval_days"):
        if col not in sub.columns:
            sub[col] = float("nan")
    # On a continuous row, `n_obs` is the number of logged readings rather
    # than of samples.
    sub["_n_discrete"] = sub["n_obs"].where(sub["record_type"] == "discrete")
    wide = (sub.groupby(["station_uid", "parameter"])
            .agg(start=("por_start", "min"), end=("por_end", "max"),
                 n_discrete=("_n_discrete", lambda s: s.sum(min_count=1)),
                 interval=("median_interval_days", "median"),
                 cont=("_cont", "any"))
            .unstack("parameter"))

    have_either = wide["start"].get("water_temperature").notna() | \
        wide["start"].get("specific_conductance").notna()
    wide = wide[have_either]

    out = pd.DataFrame(index=wide.index)
    for param, short in (("water_temperature", "temp"),
                       ("specific_conductance", "ec"),
                       ("discharge", "flow")):
        if param in wide["start"].columns:
            out[f"{short}_start"] = wide["start"][param]
            out[f"{short}_end"] = wide["end"][param]
            out[f"{short}_n_discrete"] = wide["n_discrete"][param]
            out[f"{short}_interval_days"] = wide["interval"][param]
            out[f"{short}_continuous"] = wide["cont"][param].fillna(False)
        else:
            out[f"{short}_start"] = pd.NaT
            out[f"{short}_end"] = pd.NaT
            out[f"{short}_n_discrete"] = pd.NA
            out[f"{short}_interval_days"] = pd.NA
            out[f"{short}_continuous"] = False

    out["has_pair"] = out["temp_start"].notna() & out["ec_start"].notna()
    out["has_flow"] = out["flow_start"].notna()

    # Where the two records run together. `max` and `min` skip a missing value,
    # so a station carrying one parameter would otherwise report its own record
    # as an overlap with a record it has none of.
    pair = out["has_pair"]
    ov_start = out[["temp_start", "ec_start"]].max(axis=1).where(pair)
    ov_end = out[["temp_end", "ec_end"]].min(axis=1).where(pair)
    out["overlap_start"] = ov_start
    out["overlap_end"] = ov_end
    out["overlap_years"] = ((ov_end - ov_start).dt.days / 365.25).round(1).clip(lower=0)

    # Everything below is measured against the period the model is aimed at. A
    # station whose records ran for thirty years ending in 1998 is real history
    # and calibrates no part of the window.
    window = domain_config()["calibration_period"]
    w_start = pd.Timestamp(window["start"])
    w_end = pd.Timestamp(window["end"]) if window.get("end") else pd.Timestamp.today()

    def _in_period(start: pd.Series, end: pd.Series) -> pd.Series:
        """Years of a record that fall inside the calibration period.

        A station missing the parameter gets NaN rather than a number. Both
        `max` and `min` skip a missing value, so an unguarded clip would hand
        a station with no conductance record the whole window for it.
        """
        lo = pd.concat([start, pd.Series(w_start, index=start.index)],
                       axis=1).max(axis=1)
        hi = pd.concat([end, pd.Series(w_end, index=end.index)],
                       axis=1).min(axis=1)
        years = ((hi - lo).dt.days / 365.25).clip(lower=0)
        return years.where(start.notna() & end.notna())

    out["overlap_years_in_period"] = _in_period(ov_start, ov_end).fillna(0.0)
    # The shortlist rule reads the longer of the two records rather than their
    # overlap. A station measuring one parameter has no overlap and still has a
    # record, and requiring both would drop about half the stations that meet
    # the rule. The gap report and the funnel log give the count.
    out["temp_years_in_period"] = _in_period(out["temp_start"], out["temp_end"])
    out["ec_years_in_period"] = _in_period(out["ec_start"], out["ec_end"])
    out["record_years_in_period"] = out[
        ["temp_years_in_period", "ec_years_in_period"]].max(axis=1).fillna(0.0)
    out = out.reset_index()

    keep = ["station_uid", "name", "sources", "operator", "station_type",
            "channel_class", "lat", "lon", "huc8_name", "river", "mainstem_km",
            "boundary", "active", "years_since_last", "url"]
    merged = out.merge(cat[keep], on="station_uid", how="left")
    merged["continuous"] = merged["temp_continuous"].astype(bool) & \
        merged["ec_continuous"].astype(bool)

    # Active has to mean these two records are current, not that something at
    # the site is. Merced near Stevinson gages discharge to the present and
    # stopped reporting temperature and EC in 2012; the station-level `active`
    # column marks it true and for this purpose it is not. `min` skips a
    # missing value, so `both_ends` requires both end dates, and a station
    # with one parameter takes False.
    cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(
        years=window["active_within_years"])
    both_ends = merged["temp_end"].notna() & merged["ec_end"].notna()
    pair_end = merged[["temp_end", "ec_end"]].min(axis=1).where(both_ends)
    merged["pair_active"] = pair_end.notna() & (pair_end >= cutoff)
    # A canal, a drain, a well, or an outfall measures something off the
    # modeled channel rather than a point on it. Both are usable, for
    # different things, so the flag stays on the row and the sort keeps river
    # stations on top.
    merged["off_channel"] = merged["channel_class"].isin(OFF_CHANNEL_CLASSES)
    # Record type is a column, not a filter. A model calibrated on this basin
    # can use a monthly conductance sample: it constrains the seasonal signal
    # and the load even where it cannot resolve a diel cycle. Requiring a
    # continuous monitor cuts the shortlist and drops the permitted outfalls
    # and their receiving-water points, which are the return-flow terms in a
    # salt balance. `continuous` stays a column and sorts the continuous-record
    # sites to the top of each block.
    #
    # Discharge is a column too, for the same reason: it decides whether a
    # concentration converts to a load rather than whether the station is worth
    # having. So is currency, in `pair_active` and `years_since_last`.
    merged["usable"] = merged["record_years_in_period"] >= float(
        window["min_record_years"])
    merged = _rank_shortlist(merged)
    # The rule and the rank use the unrounded years. The file stores them
    # rounded down to a tenth, so a 2.96-year record prints as 2.9 rather than
    # 3.0, and `usable` equals `record_years_in_period >= min_record_years` in
    # catalog_filtered.csv.
    for col in ("record_years_in_period", "temp_years_in_period",
                "ec_years_in_period", "overlap_years_in_period"):
        merged[col] = np.floor(merged[col] * 10) / 10
    cols = (["rank", "station_uid", "name", "sources", "operator",
             "station_type", "channel_class", "off_channel", "has_pair",
             "continuous", "active", "pair_active", "usable",
             "lat", "lon", "huc8_name", "river", "mainstem_km", "boundary",
             "record_years_in_period", "temp_years_in_period",
             "ec_years_in_period",
             "overlap_start", "overlap_end", "overlap_years",
             "overlap_years_in_period", "has_flow", "years_since_last"]
            + [f"{s}_{f}" for s in ("temp", "ec", "flow")
               for f in ("start", "end", "n_discrete", "interval_days", "continuous")]
            + ["url"])
    return _tidy(merged[cols]
                 .sort_values(["usable", "rank"], ascending=[False, True],
                              na_position="last")
                 .reset_index(drop=True))


def _rank_shortlist(cal: pd.DataFrame) -> pd.DataFrame:
    """Number the shortlist once, downstream to upstream along the mainstem.

    The number is the handle a reader carries between the map and the table: a
    numbered marker on figure 08 and the row in `catalog_filtered.csv` share
    it. Ranking here rather than in `maps.py` keeps one order, so the figure
    and the file cannot disagree about which station is number 7.

    Stations off the mainstem follow the ones on it, and the off-channel
    classes follow both, so Cal Aqueduct Check 13 does not pass for a San
    Joaquin station. Rows below the shortlist rule take no number.
    """
    cal = cal.copy()
    cal["rank"] = pd.NA
    sel = cal[cal["usable"].astype(bool)]
    if sel.empty:
        return cal
    order = sel.assign(off_mainstem=sel["mainstem_km"].isna()).sort_values(
        ["off_channel", "off_mainstem", "mainstem_km", "record_years_in_period"],
        ascending=[True, True, True, False]).index
    cal.loc[order, "rank"] = range(1, len(order) + 1)
    cal["rank"] = cal["rank"].astype("Int64")
    return cal


def funnel(stations: gpd.GeoDataFrame, cat: pd.DataFrame, cal: pd.DataFrame,
           stages: dict[str, int] | None = None) -> pd.DataFrame:
    """The counts that narrow, in the order they narrow.

    One row per step. `label` names what the step removes, `n` is what survives
    it, and `removed` is the difference from the row above. Every row is a
    subset of the row above, which is what lets a reader subtract two of them
    and get the number a step took out, and what lets figure 09 draw the table
    as a funnel.

    The Water Quality Portal returns every monitoring location in a HUC from
    its station endpoint regardless of what it measures, and `sources/wqp.py`
    drops the ones reporting no target parameter at the harvest, so the first
    row here is already short of what the portal served.

    The chain tests existence and length: does a station measure a state
    variable of the model, and does it measure one for long enough inside the
    calibration period to constrain anything. Both questions take a yes or a
    no. Everything that is a matter of degree stays a column on
    `catalog_filtered.csv`: channel class, record type, discharge, currency,
    and whether the two records overlap. A station on a canal is a boundary
    term a salt balance needs, a monthly conductance sample still constrains a
    seasonal signal, and a record that ended in 2014 still covers fourteen
    years of the period.

    The last step reads the longer of the temperature and conductance records
    rather than their overlap. Requiring both drops about half the shortlist,
    the stations that measure one, and a thermograph calibrates the
    temperature field with no conductance beside it.
    """
    stages = stages or {}
    window = domain_config()["calibration_period"]
    least = window["min_record_years"]
    since = pd.Timestamp(window["start"]).year
    short = cal["usable"].astype(bool)

    steps = [
        ("records the eight harvests returned", stages.get("harvested")),
        ("outside the study area", stages.get("in_aoi")),
        ("no usable record of a target parameter", len(stations)),
        ("the same station under another source's identifier", len(cat)),
        ("no temperature or conductance record", len(cal)),
        (f"under {least} years of either one since {since}",
         int(short.sum())),
    ]
    out = pd.DataFrame([{"label": lab, "n": n} for lab, n in steps
                        if n is not None])
    out["removed"] = (out["n"].shift(1) - out["n"]).fillna(0).astype(int)
    return out


def funnel_lines(steps: pd.DataFrame, cal: pd.DataFrame,
                 sp: pd.DataFrame) -> list[str]:
    """The funnel as log lines, plus the counts that sit beside the chain."""
    lines = [f"{r.n:>6,}  {r.label}" for r in steps.itertuples()]
    short = cal[cal["usable"].astype(bool)]
    recent = domain_config()["calibration_period"]["active_within_years"]
    aside = (f"of the {len(short)} that reach the shortlist: "
             f"{int(short['has_pair'].astype(bool).sum())} measure both "
             f"parameters, "
             f"{int(short['continuous'].astype(bool).sum())} from a continuous "
             f"monitor on both, "
             f"{int(short['has_flow'].astype(bool).sum())} also gaging "
             f"discharge, "
             f"{int(short['off_channel'].astype(bool).sum())} off the modeled "
             f"channel, "
             f"{int(short['pair_active'].astype(bool).sum())} with both "
             f"records reaching into the last {recent} years")
    lines += [aside, f"station-parameter rows: {len(sp)}"]
    return lines


def _station_notes(stations: gpd.GeoDataFrame) -> pd.DataFrame | None:
    """CDEC's dated operator log, keyed to station_uid.

    These are the entries that explain a step change: a rating shift, a sensor
    moved, an orifice buried by sediment. Without them a calibration run cannot
    separate an instrument change from a real signal.
    """
    path = INTERIM / "cdec_notes.parquet"
    if not path.exists():
        return None
    notes = pd.read_parquet(path)
    key = (stations.loc[stations["source"] == "cdec",
                        ["source_station_id", "station_uid", "name"]]
           .drop_duplicates("source_station_id"))
    merged = notes.merge(key, left_on="station_id", right_on="source_station_id",
                         how="inner")
    return merged[["station_uid", "name", "date", "note"]].sort_values(
        ["station_uid", "date"]).reset_index(drop=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stations, sp, stages = build()
    # `main` builds every table before its first write, so an exception in a
    # build leaves data/processed as the previous run wrote it.
    cat = catalog(stations)
    cal = calibration_catalog(cat, sp)
    cov = coverage.coverage_table(cat, sp)
    notes = _station_notes(stations)
    steps = funnel(stations, cat, cal, stages)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    out = PROCESSED / "stations.gpkg"
    stations.to_file(out, layer="stations", driver="GPKG")
    sp.to_parquet(PROCESSED / "station_parameter.parquet", index=False)
    cat.to_csv(PROCESSED / "catalog.csv", index=False)
    cal.to_csv(PROCESSED / "catalog_filtered.csv", index=False)
    cov.to_csv(PROCESSED / "coverage_sets.csv", index=False)
    log.info("coverage_sets: %d stations meet at least one rule (%s)",
             len(cov), ", ".join(
                 f"{k} {int(cov[k].sum())}" for k in coverage.COVERAGE_SETS))
    if notes is not None:
        notes.to_parquet(PROCESSED / "station_notes.parquet", index=False)
        log.info("station_notes: %d dated operator notes on %d stations",
                 len(notes), notes["station_uid"].nunique())

    steps.to_csv(PROCESSED / "funnel.csv", index=False)
    for line in funnel_lines(steps, cal, sp):
        log.info("%s", line)
    log.info("multi-source stations: %d", int((cat["n_sources"] > 1).sum()))
    log.info("wrote %s, station_parameter.parquet, catalog.csv", out)

    counts = (sp.groupby("parameter")["station_uid"].nunique()
                .sort_values(ascending=False))
    log.info("stations per parameter:\n%s", counts.to_string())


if __name__ == "__main__":
    main()

"""Gap analysis: where the observational coverage runs out.

    python -m sjrwq.gaps

Writes docs/gap_analysis.md. Every number in that document comes from
data/processed, so a fresh harvest regenerates it.
"""

from __future__ import annotations

import logging
from itertools import pairwise

import geopandas as gpd
import pandas as pd
from shapely.ops import linemerge

from . import classify, units
from .config import DOCS, INTERIM, PROCESSED, ROOT, ParameterLookup, domain_config, log
from .coverage import (
    CORE_PARAMETERS,
    colocated,
    colocated_overlap,
    reporting_since,
    station_level,
)
from .coverage import label as window_label
from .inventory import EARLIEST_PLAUSIBLE
from .places import FIXED_POINTS, RESERVOIR_LABEL, RESERVOIRS
from .rivers import ON_CHANNEL_M, confluences, merged_mainstem_line, river_distance_km

CORE = list(CORE_PARAMETERS)


def wqp_endpoint_counts() -> dict:
    """What the portal's two endpoints returned, from the harvest.

    `Station/search` returns every monitoring location in a HUC and
    `Result/search` returns the target characteristics, so the difference
    between them is locations that report nothing this inventory asks for. They
    leave at the harvest and reach no processed table, so `sjrwq.harvest`
    writes the counts and this reads them back. An absent file returns an empty
    dict, and the sentences that quote it drop out.
    """
    path = INTERIM / "wqp_endpoint_counts.csv"
    if not path.exists():
        log.warning("no wqp_endpoint_counts.csv; re-run the wqp harvest")
        return {}
    return pd.read_csv(path).iloc[0].to_dict()


def _wqp_characteristics(wq: dict) -> int:
    """How many characteristics the portal harvest requests.

    The count file has it where the harvest wrote it. Otherwise it comes from
    `config/parameters.yml`, the list `sources/wqp.py` sends.
    """
    if pd.notna(wq.get("characteristics_requested")):
        return int(wq["characteristics_requested"])
    return len(ParameterLookup.build().wqp_characteristic_names())


def _wqp_removed(wq: dict) -> str:
    """The harvest's two cuts to the station endpoint's locations, as prose.

    A count file without `locations_filtered_out` gives the one cut it has.
    """
    n_char = _wqp_characteristics(wq)
    dropped = int(wq["locations_dropped"])
    if pd.notna(wq.get("locations_filtered_out")):
        return (f"The harvest removes {int(wq['locations_filtered_out']):,} of them "
                "for a missing coordinate, a synthetic test-site number, or an "
                f"organization outside water monitoring, and drops {dropped:,} of "
                f"the rest because they report 0 of this inventory's {n_char} "
                "characteristics.")
    return (f"The harvest drops the {dropped:,} of them that report 0 of this "
            f"inventory's {n_char} characteristics.")

# What counts as a station on the river, for the purpose of calling the river
# observed there.
#
# `mainstem_km` lands on every station within `rivers.ON_CHANNEL_M` of the
# centerline, which on a valley floor threaded with canals and monitoring wells
# covers a lot of things that are not the river: CDEC groundwater piezometers,
# the Delta-Mendota and Friant-Kern canals where they run beside the river, and
# agricultural drains, one of which last reported in 1994. Without this filter,
# groundwater temperature closes river temperature gaps and the longest gap
# comes out short.
#
# receiving_water is in. Those are real river points, sampled under a
# discharger's permit rather than by a monitoring program, and a bracketing
# pair above and below an outfall observes the river as well as another grab
# site does.
#
# Canals are out of *this* test and out of no other. A canal beside the river
# does not measure the river's temperature there, regardless of what it moves,
# so it cannot close a gap in the longitudinal coverage. It can still be the most
# important measurement on the page: the Delta-Mendota Canal's delivery to
# Mendota Pool is the largest single inflow to the modeled reach. Those links
# get their own section, `conveyance_links` below, rather than folding into the
# river gaps or reading as imported water.
RIVER_CLASSES = {"natural", "receiving_water"}

# Who holds the reservoir depth profiles the eight APIs do not publish: the dam
# operators. Reclamation runs Friant and New Melones, the Turlock and Modesto
# Irrigation Districts run Don Pedro, Merced Irrigation District runs Lake
# McClure, and the USACE Sacramento District runs Hidden and Buchanan dams.
PROFILE_HOLDERS = ("Reclamation, the USACE Sacramento District, the Turlock and Modesto "
                   "Irrigation Districts, and Merced Irrigation District hold the profiles")

# Each rim dam sits in one subbasin and its release runs down one river, so
# the reservoir search runs on the dam's own river, or on its own subbasin
# where the river has no traced centerline. On distance alone a dam can match
# a gage on the next river over. The Fresno and the Chowchilla have no NLDI
# trace in this inventory, so their two dams take the subbasin match. The
# README bullet and the report's reservoir table both search with these.
RESERVOIR_RIVER = {
    "Millerton Lake (Friant)": "San Joaquin River",
    "New Melones (Stanislaus)": "Stanislaus River",
    "Don Pedro (Tuolumne)": "Tuolumne River",
    "Lake McClure (Merced)": "Merced River",
}
RESERVOIR_HUC8 = {
    "Hensley Lake (Fresno R)": "Fresno River",
    "Eastman Lake (Chowchilla R)": "Middle San Joaquin-Lower Chowchilla",
}

# A RISE station within this distance of a rim dam counts as RISE publishing
# at that dam.
RISE_AT_DAM_KM = 5.0


def _rise_profiles(st, sp, crs_area: str = "EPSG:3310") -> dict:
    """What RISE's `hasProfile` flag covers at the rim dams.

    Returns the number of RISE series in the basin (`n_series`), the number
    with the flag set (`n_flag`), the dams with a RISE station within
    RISE_AT_DAM_KM (`at`), and the dams among those with a flagged series
    (`profiled`). RISE is the one harvested source with a profile flag.
    """
    rise = sp[sp["source"] == "usbr_rise"]
    flag = (rise["has_profile"].fillna(False).astype(bool)
            if "has_profile" in rise.columns
            else pd.Series(False, index=rise.index))
    out = {"n_series": len(rise), "n_flag": int(flag.sum()), "at": [], "profiled": []}
    locs = st[(st["source"] == "usbr_rise")
              & st["station_uid"].isin(set(rise["station_uid"]))]
    if locs.empty:
        return out
    locs = locs.to_crs(crs_area)
    flagged = set(rise.loc[flag, "station_uid"])
    for name, (lat, lon) in RESERVOIRS.items():
        dam = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]),
                            crs="EPSG:4326").to_crs(crs_area).iloc[0]
        near = locs[locs.distance(dam) <= RISE_AT_DAM_KM * 1000.0]
        if near.empty:
            continue
        out["at"].append(name)
        if set(near["station_uid"]) & flagged:
            out["profiled"].append(name)
    return out


def _load():
    st = gpd.read_file(PROCESSED / "stations.gpkg", layer="stations")
    sp = pd.read_parquet(PROCESSED / "station_parameter.parquet")
    ms = gpd.read_file(PROCESSED / "domain.gpkg", layer="mainstem")
    for c in ("por_start", "por_end"):
        sp[c] = pd.to_datetime(sp[c], errors="coerce")
    return st, sp, ms


def _reach_lines(crs_area: str = "EPSG:3310") -> gpd.GeoDataFrame | None:
    """Mainstem and mapped tributaries, dissolved to one line per river."""
    parts = []
    for layer in ("mainstem", "tributaries"):
        try:
            parts.append(gpd.read_file(PROCESSED / "domain.gpkg", layer=layer))
        except Exception:  # noqa: BLE001 - a missing layer is not fatal here
            log.warning("domain.gpkg has no %s layer", layer)
    if not parts:
        return None
    both = pd.concat(parts, ignore_index=True).to_crs(crs_area)
    return both.dissolve(by="river").reset_index()[["river", "geometry"]]


def mainstem_gaps(st, sp, ms, parameter, continuous_only=True, min_gap_km=15.0,
                  river_only=True, since=None):
    """Stretches of the San Joaquin with no station measuring `parameter`.

    Two filters beyond the obvious one, both of which make the gaps longer and
    both of which are the point of the exercise.

    `river_only` drops canals, drains, and monitoring wells. See RIVER_CLASSES.

    `since` drops records that ended before a date. A gap is a claim about
    whether the river is observed, and a gage that ran from 1955 to 1970 does
    not observe it now. Passed the coverage window start, this turns the table
    from "was this reach ever gaged" into "is it gaged over the period the
    model is aimed at", which are different questions with different answers.
    """
    line = merged_mainstem_line(ms)
    total_km = line.length / 1000.0
    sel = _mainstem_stations(st, sp, line, parameter, continuous_only,
                             river_only, since)
    # The length comes from the rounded ends, so each row subtracts and one
    # pair of ends has one length in every table.
    ends = [(round(a, 1), round(b, 1))
            for a, b in _open_runs(sel["mainstem_km"], total_km, min_gap_km)]
    gaps = [(a, b, round(b - a, 1)) for a, b in ends]
    return len(sel), round(total_km, 1), gaps


def _mainstem_stations(st, sp, line, parameter, continuous_only=True,
                       river_only=True, since=None):
    """Primary stations on the mainstem axis that `mainstem_gaps` counts."""
    prim = st[st["is_primary"]].copy()
    if "mainstem_km" not in prim.columns:
        prim["mainstem_km"] = river_distance_km(prim, line)
    if river_only and "channel_class" in prim.columns:
        prim = prim[prim["channel_class"].isin(RIVER_CLASSES)]

    # Continuity belongs to the record rather than to the site. A gage can log
    # discharge every 15 minutes and take its chloride from four samples, so
    # this filter uses the record type of the parameter requested.
    rows = sp[sp["parameter"] == parameter]
    if continuous_only:
        rows = rows[rows["record_type"] == "continuous"]
    if since is not None:
        rows = rows[rows["por_end"] >= pd.Timestamp(since)]
    ids = set(rows["station_uid"])
    return prim[prim["station_uid"].isin(ids)].dropna(subset=["mainstem_km"])


def _open_runs(km, total_km, min_gap_km=15.0):
    """Unrounded (start, end) of each station-free run of `min_gap_km` or more."""
    edges = [0.0, *sorted(km.tolist()), total_km]
    return [(a, b) for a, b in pairwise(edges) if b - a >= min_gap_km]


def nearest_station(st, sp, lat, lon, parameter=None, continuous_only=False,
                    crs_area="EPSG:3310", river_only=True, since=None,
                    river=None, huc8_name=None):
    """Closest station measuring `parameter`, and how far away it is.

    `river_only` and `since` mean the same as in `mainstem_gaps` and exist for
    the same reason. `huc8_name` confines the search to one subbasin, which the
    reservoir table needs: on distance alone a dam can match a gage on the next
    river over, below a different dam. `river` is the better of the two where
    it exists, because a subbasin boundary can run right at a dam: Friant is in
    Upper San Joaquin and the release gage 200 m downstream of it is in Middle
    San Joaquin, so a subbasin match throws away the release gage at Friant.
    Without these constraints the function can return a canal or a powerhouse
    tailrace as a release-temperature proxy, or a gage that stopped years ago,
    with no sign that it is other than a river station reporting today. The
    report's reservoir section lists what each constraint changes.
    """
    prim = st[st["is_primary"]].copy()
    if river_only and "channel_class" in prim.columns:
        prim = prim[prim["channel_class"].isin(RIVER_CLASSES)]
    if river is not None:
        prim = prim[prim["river"] == river]
    elif huc8_name is not None:
        prim = prim[prim["huc8_name"] == huc8_name]
    rows = sp[sp["parameter"] == parameter] if parameter else sp
    if continuous_only:
        rows = rows[rows["record_type"] == "continuous"]
    if since is not None:
        rows = rows[rows["por_end"] >= pd.Timestamp(since)]
    if parameter or continuous_only or since is not None:
        prim = prim[prim["station_uid"].isin(set(rows["station_uid"]))]
    if prim.empty:
        return None, None
    target = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs="EPSG:4326").to_crs(crs_area)
    d = prim.to_crs(crs_area).distance(target.iloc[0])
    i = d.idxmin()
    return prim.loc[i], round(d.loc[i] / 1000.0, 1)


def _below_dam(reaches, river, dam, station, crs_area="EPSG:3310") -> bool | None:
    """Whether `station` sits downstream of `dam` along `river`'s traced line.

    None where `river` has no traced line. Each traced river here drains
    toward Vernalis, so the end of its line nearer Vernalis is the mouth, and
    distance along the line from that end grows upstream. `dam` is (lat, lon)
    and `station` is a GeoSeries of one point.
    """
    if reaches is None or river is None or river not in set(reaches["river"]):
        return None
    line = reaches.loc[reaches["river"] == river, "geometry"].iloc[0]
    if line.geom_type == "MultiLineString":
        line = linemerge(line)
    if line.geom_type == "MultiLineString":
        line = max(line.geoms, key=lambda g: g.length)
    v_lat, v_lon = FIXED_POINTS["Vernalis (downstream boundary)"]
    dam_pt, vernalis = gpd.GeoSeries(
        gpd.points_from_xy([dam[1], v_lon], [dam[0], v_lat]),
        crs="EPSG:4326").to_crs(crs_area)
    stn = station.to_crs(crs_area).iloc[0]
    mouth_first = (line.interpolate(0).distance(vernalis)
                   <= line.interpolate(line.length).distance(vernalis))

    def upstream_m(pt):
        d = line.project(pt)
        return d if mouth_first else line.length - d

    return upstream_m(stn) < upstream_m(dam_pt)


def _last_continuous(sp, uid, parameter) -> pd.Timestamp:
    """End of one station's continuous record of `parameter`."""
    rows = sp[(sp["station_uid"] == uid) & (sp["parameter"] == parameter)
              & (sp["record_type"] == "continuous")]
    return rows["por_end"].max()


def _join(items) -> str:
    """A list as prose: "a", "a and b", "a, b, and c"."""
    items = list(items)
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _dam_labels(names) -> str:
    """Rim dams by their short map labels, as prose."""
    return _join(RESERVOIR_LABEL.get(n, n) for n in names)


# eSMR station types that describe a permitted discharge point, plus the
# channel classes that describe one. No source publishes `drain` as a station
# type, so the agricultural drains come through the channel classifier alone.
DISCHARGE_TYPES = {"effluent", "receiving_water", "internal", "influent",
                   "land_discharge", "stormwater"}
DISCHARGE_CHANNELS = {"effluent", "receiving_water", "drain"}

# How far from a modeled reach a discharge point can sit and still reach the
# return-flow table. See `point_discharges` for why the cutoff is loose.
DISCHARGE_KM = 25.0

# Source codes as the report names them in prose.
SOURCE_PROSE = {"esmr": "eSMR", "wqp": "the Water Quality Portal", "ceden": "CEDEN",
                "usgs": "USGS", "dwr_wdl": "DWR's Water Data Library", "cdec": "CDEC",
                "edi": "EDI", "usbr_rise": "RISE"}

def _interval_days(row) -> float | None:
    """Reporting interval for one station-parameter row.

    Prefers the median gap between sample dates where a source computed one.
    Falls back to the span divided by the observation count, which is what
    eSMR and CEDEN allow: they publish a count and a period but no interval.
    """
    med = row.get("median_interval_days")
    if pd.notna(med):
        return float(med)
    n, a, b = row.get("n_obs"), row.get("por_start"), row.get("por_end")
    if pd.isna(n) or n <= 1 or pd.isna(a) or pd.isna(b):
        return None
    days = (b - a).days
    return round(days / float(n), 1) if days > 0 else None


def _frequency_mark(interval: float | None, frequency: object) -> str:
    """How often a parameter is reported here, as one word for a table cell.

    The stated frequency wins where a source publishes one, because it says
    what the operator intended. Where none is stated the measured interval
    stands in, through the same bands the harvest uses, so one vocabulary runs
    across the tables and the columns.
    """
    if isinstance(frequency, str) and frequency not in ("unknown", ""):
        return frequency
    # pd.isna, not `is None`: the interval arrives through a DataFrame column,
    # which turns a None into a float nan, and nan fails every band comparison
    # silently.
    if interval is None or pd.isna(interval):
        return "reported"
    return classify.frequency_from_interval(interval)


def _best_series(sp: pd.DataFrame) -> dict:
    """The most frequent series per station and parameter.

    A station reported by both a CDEC sonde and the portal's discrete samples
    has two rows for the same parameter. Taking whichever comes last labels
    Check 20's hourly temperature record `irregular`, because the portal row
    holds fourteen samples over twenty years. The fastest series is the right
    answer to "how often is this measured here".
    """
    core = sp[sp["parameter"].isin(CORE)].copy()
    core["interval"] = [_interval_days(r) for _, r in core.iterrows()]
    core["_rank"] = core["frequency"].map(classify.FREQUENCY_RANK).fillna(7)
    # A missing interval on a continuous source is not a slow series, it is a
    # source that publishes no count, so rank on the stated frequency first.
    core = core.sort_values(["_rank", "interval"], na_position="last")
    return {k: r for k, r in
            core.set_index(["station_uid", "parameter"])[::-1].iterrows()}


def point_discharges(st, sp, max_km: float = DISCHARGE_KM, crs_area: str = "EPSG:3310"):
    """Return-flow and drain monitoring points near a modeled reach.

    An inventory of what exists rather than a list of what is missing. The
    `type` and `station_type` columns split the rows. A drain or an effluent
    point is a boundary term for a water or salt balance, an `influent` row
    measures the water entering a plant, and a receiving-water point samples
    the river and counts as a river station under RIVER_CLASSES.

    Each parameter cell holds a reporting frequency and the `n` column holds
    the observation count, so a single 1987 sample and a sixteen-year monthly
    series differ in the table: the difference between a located pipe and a
    monthly salt-load term.

    The distance cutoff is loose on purpose. An effluent record holds the
    coordinate of the treatment plant rather than of the pipe outlet, and the
    two can be far apart: Manteca's plant is 11 km from the San Joaquin and
    discharges to it through a pipeline. Cutting at a few kilometers drops the
    facilities that matter most.
    """
    reaches = _reach_lines(crs_area)
    is_discharge = (st["station_type"].isin(DISCHARGE_TYPES)
                    | st["channel_class"].isin(DISCHARGE_CHANNELS))
    sel = st[st["is_primary"] & is_discharge].copy()
    if sel.empty or reaches is None:
        return pd.DataFrame()

    proj = sel.to_crs(crs_area)
    dists = pd.DataFrame(
        {row["river"]: proj.distance(row.geometry) for _, row in reaches.iterrows()},
        index=sel.index)
    sel["reach"] = dists.idxmin(axis=1).str.replace(" River", "", regex=False)
    sel["km_to_reach"] = (dists.min(axis=1) / 1000.0).round(1)
    sel = sel[sel["km_to_reach"] <= max_km]

    per_param = _best_series(sp)
    have = sp.groupby("station_uid")["parameter"].agg(set)
    span = sp.groupby("station_uid").agg(por_start=("por_start", "min"),
                                         por_end=("por_end", "max"))

    # A discharge point reporting 0 of the three balance terms is a located
    # pipe. Two kinds go out, and they are worth separating: most report no
    # target parameter, which leaves a position and a name; a handful
    # report chemistry without flow, temperature, or conductance.
    useful = {u for u, ps in have.items() if ps & set(CORE)}
    unlisted = sel[~sel["station_uid"].isin(useful)]
    no_data = int((~unlisted["station_uid"].isin(have.index)).sum())
    other_only = len(unlisted) - no_data
    sel = sel[sel["station_uid"].isin(useful)]
    if len(unlisted):
        log.info("point discharges: %d within %g km stay out of the table "
                 "(%d report no target parameter, %d report only "
                 "parameters outside flow, temperature, and EC)",
                 len(unlisted), max_km, no_data, other_only)

    rows = []
    for _, r in sel.iterrows():
        uid = r["station_uid"]
        ps = have.get(uid, set())
        por = span.loc[uid] if uid in span.index else None
        row = {
            "name": str(r["name"]),
            "type": r["channel_class"],
            "station_type": r["station_type"],
            "reach": r["reach"],
            "km_to_reach": r["km_to_reach"],
            "source": r["source"],
        }
        n_total = 0
        for param, short in (("discharge", "flow"), ("water_temperature", "temp"),
                           ("specific_conductance", "ec")):
            rec = per_param.get((uid, param))
            if param not in ps or rec is None:
                row[short] = "-"
                continue
            row[short] = _frequency_mark(rec["interval"], rec.get("frequency"))
            if pd.notna(rec.get("n_obs")):
                n_total += int(rec["n_obs"])
        row["n"] = f"{n_total:,}" if n_total else "-"
        row["por"] = (f"{por['por_start']:%Y}-{por['por_end']:%Y}"
                      if por is not None and pd.notna(por["por_start"])
                      and pd.notna(por["por_end"]) else "")
        rows.append(row)
    out = pd.DataFrame(rows).sort_values(["km_to_reach", "name"])
    out.attrs["dropped_without_data"] = len(unlisted)
    out.attrs["dropped_no_measurement"] = no_data
    out.attrs["dropped_other_variables"] = other_only
    return out


def _discharge_kinds(tbl: pd.DataFrame) -> str:
    """The sentences splitting the return-flow table by class and station type."""
    influent = tbl["station_type"] == "influent"
    kinds = tbl.loc[~influent, "type"].value_counts()
    n_drain, n_eff = int(kinds.get("drain", 0)), int(kinds.get("effluent", 0))
    n_rw, n_inf = int(kinds.get("receiving_water", 0)), int(influent.sum())
    n_other = len(tbl) - n_drain - n_eff - n_rw - n_inf
    text = (f"By channel class and station type, {n_drain} of the points are drains and "
            f"{n_eff} are effluent points, the discharges a water or salt balance takes "
            "as boundary terms. ")
    if n_inf:
        listed = sorted(tbl.loc[influent, "type"].dropna().unique())
        text += (f"{n_inf} have the station type `influent` and sample the water "
                 "entering a plant"
                 + (f"; the `type` column lists them as {_join(listed)}" if listed else "")
                 + ". ")
    if n_rw:
        text += (f"{n_rw} are receiving-water points, river samples taken under a "
                 "discharge permit"
                 + (", and count as observing the river under the convention at the "
                    "top of this report" if "receiving_water" in RIVER_CLASSES else "")
                 + ". ")
    if n_other:
        text += f"The other {n_other} have another channel class. "
    return text


# A record has to average at least this many observations per decade before a
# decade its span crosses counts as covered. Without it, two samples thirty
# years apart count as four decades of monitoring, because the span is the whole
# of the published metadata and covers the years between the two samples by
# assumption. Six per decade is roughly one sample every twenty months, the
# loosest reading under which "this station was operating" holds.
MIN_OBS_PER_DECADE = 6


# The four things a canal can be doing, in the order a balance cares about.
CONVEYANCE_ROLES = ["import", "export", "diversion", "return"]

ROLE_BLURB = {
    "import": "Delta water pumped into the basin. Adds water and salt from "
              "outside the basin.",
    "export": "Basin water leaving. Removes water and salt.",
    "diversion": "Basin water moved within the basin. Turlock Canal is TID's "
                 "diversion from the Tuolumne at La Grange and moves Tuolumne "
                 "water, not Delta water.",
    "return": "Irrigation water going back to a river, concentrated by use. "
              "These belong beside the agricultural drains.",
}


def conveyance_links(st, sp, crs_area: str = "EPSG:3310"):
    """Canals and pipelines, and where each one meets the modeled river.

    Canals go wrong at both ends of a gap analysis. Counted as river stations
    they make the river look better observed than it is, and written off as
    imported water they hide the Delta-Mendota Canal's delivery to Mendota
    Pool, which is the largest inflow to the modeled reach and enters the San
    Joaquin at the pool.

    This table covers the question the balance asks: what enters and leaves
    the river through a pipe, where, and whether the flux is measured.
    """
    reaches = _reach_lines(crs_area)
    sel = st[st["is_primary"] & (st["channel_class"] == "conveyance")].copy()
    if sel.empty or reaches is None:
        return pd.DataFrame()
    sel = sel[sel["n_parameters"].fillna(0) > 0]

    proj = sel.to_crs(crs_area)
    dists = pd.DataFrame(
        {row["river"]: proj.distance(row.geometry) for _, row in reaches.iterrows()},
        index=sel.index)
    sel["reach"] = dists.idxmin(axis=1).str.replace(" River", "", regex=False)
    sel["km_to_reach"] = (dists.min(axis=1) / 1000.0).round(1)

    per_param = _best_series(sp)
    have = sp.groupby("station_uid")["parameter"].agg(set)

    rows = []
    for _, r in sel.iterrows():
        uid = r["station_uid"]
        ps = have.get(uid, set())
        if not (ps & set(CORE)):
            continue
        row = {"name": str(r["name"]),
               "role": r.get("conveyance_role") or "diversion",
               "reach": r["reach"],
               "km_to_reach": r["km_to_reach"],
               "mainstem_km": r.get("mainstem_km")}
        for param, short in (("discharge", "flow"), ("water_temperature", "temp"),
                           ("specific_conductance", "ec")):
            rec = per_param.get((uid, param))
            row[short] = ("-" if param not in ps or rec is None
                          else _frequency_mark(rec["interval"], rec.get("frequency")))
        row["last"] = (f"{pd.Timestamp(r['last_obs']):%Y}"
                       if pd.notna(r.get("last_obs")) else "")
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_r"] = out["role"].map({v: i for i, v in enumerate(CONVEYANCE_ROLES)})
    return out.sort_values(["_r", "km_to_reach", "name"]).drop(columns="_r")


# The Delta-Mendota Canal's delivery to Mendota Pool: the station gaging its
# flow, and CHECK 20 and CHECK 21, which log the quality of the water arriving.
MENDOTA_POOL_FLOW = "dwr_wdl:B00770"
MENDOTA_POOL_QUALITY = ("cdec:DM2", "cdec:DM3")


def _mendota_pool(st, sp, line, crs_area: str = "EPSG:3310") -> str:
    """The Mendota Pool paragraph, from the flow and quality records there.

    Empty when one of the three stations or its record drops out of the
    inventory, so the report prints the paragraph only from records it has.
    """
    prim = st[st["is_primary"]].drop_duplicates("station_uid").set_index("station_uid")
    if not {MENDOTA_POOL_FLOW, *MENDOTA_POOL_QUALITY} <= set(prim.index):
        return ""
    cont = sp[sp["record_type"] == "continuous"]
    flow = sp[(sp["station_uid"] == MENDOTA_POOL_FLOW) & (sp["parameter"] == "discharge")]
    qual = cont[cont["station_uid"].isin(MENDOTA_POOL_QUALITY)]
    temp = qual[qual["parameter"] == "water_temperature"]
    ec = qual[qual["parameter"] == "specific_conductance"]
    if flow.empty or temp.empty or ec.empty:
        return ""
    f = prim.loc[MENDOTA_POOL_FLOW]
    names = [str(prim.loc[u, "name"]) for u in MENDOTA_POOL_QUALITY]
    f_start, f_end = flow["por_start"].min(), flow["por_end"].max()
    t_start, t_end = temp["por_start"].min(), temp["por_end"].max()
    e_start, e_end = ec["por_start"].min(), ec["por_end"].max()
    off_m = gpd.GeoSeries([f.geometry], crs=st.crs).to_crs(crs_area).distance(line).iloc[0]

    text = ("\nMendota Pool is worth reading closely, because the flux and the quality "
            "come from different places in different decades. "
            f"`{f['name']}` gages the flow of the delivery, with a "
            f"discharge record from {f_start:%Y} to {f_end:%Y}. ")
    # The table's `last` column is the station's last observation of any
    # parameter, which can belong to a record other than the flow.
    last_obs = pd.Timestamp(f["last_obs"]) if pd.notna(f.get("last_obs")) else None
    last = sp[sp["station_uid"] == MENDOTA_POOL_FLOW].sort_values("por_end").iloc[-1]
    if (last_obs is not None and last_obs.year > f_end.year
            and last["parameter"] != "discharge"
            and pd.notna(last["por_end"]) and last["por_end"].year == last_obs.year):
        text += (f"Its `last` year in the table below, {last_obs:%Y}, comes from its "
                 f"{last['parameter'].replace('_', ' ')} record. ")
    if pd.isna(f.get("mainstem_km")):
        text += (f"It sits {off_m / 1000:.1f} km from the San Joaquin, farther than the "
                 f"{ON_CHANNEL_M:.0f} m that puts a station on the mainstem axis, so its "
                 "`mainstem km` cell is blank. ")
    flow_q = sp[sp["station_uid"].isin(MENDOTA_POOL_QUALITY)
                & (sp["parameter"] == "discharge")]
    ends = (f"both to {t_end:%Y}" if t_end.year == e_end.year
            else f"to {t_end:%Y} and {e_end:%Y}")
    text += (f"{_join(names)} log the temperature and conductance of the arriving "
             f"water with a continuous monitor, temperature from {t_start:%Y} and "
             f"conductance from {e_start:%Y}, {ends}")
    text += ", and measure no flow. " if flow_q.empty else ". "
    f_quality = cont[(cont["station_uid"] == MENDOTA_POOL_FLOW)
                     & cont["parameter"].isin(["water_temperature", "specific_conductance"])]
    if flow_q.empty and f_quality.empty:
        text += (f"0 of the {1 + len(MENDOTA_POOL_QUALITY)} stations record both the "
                 "flow and the quality continuously. ")
    if f_end < t_start:
        text += (f"The discharge record ends in {f_end:%Y}, before the continuous "
                 f"temperature record at {_join(names)} starts in {t_start:%Y}, so an "
                 f"inflow boundary after {f_end:%Y} needs the flow from Reclamation's "
                 "CVO reports.\n")
    else:
        text += (f"The discharge record and the continuous temperature record at "
                 f"{_join(names)} overlap from {t_start:%Y} to {f_end:%Y}, and an "
                 f"inflow boundary after {f_end:%Y} needs the flow from Reclamation's "
                 "CVO reports.\n")
    return text


# How much earlier a source's record has to start than every other record of
# the same station and parameter before `record_extension` lists it.
EXTENSION_MIN_YEARS = 2.0


def record_extension(st: pd.DataFrame, sp: pd.DataFrame, source: str,
                     parameters: tuple[str, ...] = ("water_temperature",
                                                   "specific_conductance"),
                     min_years: float = EXTENSION_MIN_YEARS) -> pd.DataFrame:
    """Stations where one source starts a record before the others.

    A source that reports the same station as three others has still earned its
    place if it reaches further back. This finds those cases: the single
    earliest series the named source publishes for a station and parameter,
    against the earliest start the other sources publish.

    One series, not an aggregate of the source's series. Rolling a source up to
    its own minimum start date splices records with a break between them:
    Paradise Cut has a discrete sample record from 1950 in one EDI package and
    a fifteen-minute sonde from 1999 in another, and the aggregate comes out as
    a fifteen-minute record running from 1950.

    The comparison runs continuous against continuous and discrete against
    discrete, for the same reason. A 1950 sample does not extend a sonde
    record, and reporting one buries the useful row here, a continuous
    conductance record reaching fifteen years further back than the gage that
    replaced it.

    Station-scope records only; see `coverage.station_level`. A dataset-scope
    row holds its package's period of record, so it reports an extension the
    station may not have.
    """
    d = station_level(sp[sp["parameter"].isin(parameters)]).dropna(subset=["por_start"]).copy()
    if d.empty or source not in set(d["source"]):
        return pd.DataFrame()
    d["continuous"] = d["record_type"] == "continuous"
    key = ["station_uid", "parameter", "continuous"]
    mine = d[d["source"] == source]
    others = (d[d["source"] != source].groupby(key)["por_start"]
              .min().rename("other_start"))
    earliest = mine.loc[mine.groupby(key)["por_start"].idxmin()]
    joined = earliest.merge(others, on=key, how="inner")
    joined["years_earlier"] = (
        (joined["other_start"] - joined["por_start"]).dt.days / 365.25).round(1)
    joined = joined[joined["years_earlier"] >= min_years]
    if joined.empty:
        return pd.DataFrame()

    names = (st[st["is_primary"]][["station_uid", "name"]]
             .drop_duplicates("station_uid"))
    out = joined.merge(names, on="station_uid", how="left")
    out["record"] = (out["por_start"].dt.strftime("%Y") + "-"
                     + out["por_end"].dt.strftime("%Y"))
    out["previously_from"] = out["other_start"].dt.strftime("%Y")
    out["kind"] = out["continuous"].map({True: "continuous", False: "discrete"})
    cols = ["name", "parameter", "kind", "frequency", "record",
            "previously_from", "years_earlier"]
    if "package" in out.columns:
        out = out.rename(columns={"package": "dataset"})
        cols.insert(3, "dataset")
    return (out[cols].sort_values("years_earlier", ascending=False)
            .reset_index(drop=True))


def coverage_by_decade(sp, min_per_decade: int = MIN_OBS_PER_DECADE):
    """Stations reporting each parameter in each decade.

    Station-scope records only; see `coverage.station_level`. A dataset-scope
    row has its package's period and no count, so it would put a station in
    every decade the package spans.

    A row with no observation count covers every decade its span crosses.
    Most of those rows come off a continuous monitor, whose span is the
    record; `_decade_blank_counts` counts them for the report. The count filter
    matters for the portal and CEDEN, where a span can be two samples and a
    long wait.
    """
    rows = []
    for param in CORE:
        s = station_level(sp[sp["parameter"] == param]).dropna(
            subset=["por_start", "por_end"]).copy()
        years = ((s["por_end"] - s["por_start"]).dt.days / 365.25).clip(lower=0.5)
        per_decade = s["n_obs"].astype("Float64") / (years / 10.0)
        s = s[per_decade.isna() | (per_decade >= min_per_decade)]
        for dec in range(1950, 2030, 10):
            lo = pd.Timestamp(f"{dec}-01-01")
            hi = pd.Timestamp(f"{dec + 9}-12-31")
            active = s[(s["por_start"] <= hi) & (s["por_end"] >= lo)]
            rows.append({"parameter": param, "decade": f"{dec}s",
                         "stations": active["station_uid"].nunique()})
    return pd.DataFrame(rows).pivot(index="decade", columns="parameter", values="stations")


def _decade_blank_counts(sp) -> tuple[int, int, int, list[str]]:
    """The rows `coverage_by_decade` reads, and the ones with no count.

    Returns (rows read, rows with no count, of those the continuous ones, the
    sources they come from).
    """
    s = station_level(sp[sp["parameter"].isin(CORE)]).dropna(
        subset=["por_start", "por_end"])
    blank = s[s["n_obs"].isna()]
    return (len(s), len(blank), int((blank["record_type"] == "continuous").sum()),
            sorted(blank["source"].dropna().unique()))


def _measured_sources(sp) -> list[str]:
    """Sources whose frequency the harvest measures rather than reads off a stated interval.

    Takes the rows `coverage_by_decade` takes. A source qualifies when each of
    its rows has a sample count in `n_obs` and a `frequency` equal to the band
    its `median_interval_days` falls in, under either band list in `classify`.
    Those rows are the ones the per-decade floor tests.
    """
    s = station_level(sp[sp["parameter"].isin(CORE)]).dropna(
        subset=["por_start", "por_end"])
    if s.empty or not {"n_obs", "median_interval_days"} <= set(s.columns):
        return []
    banded = pd.Series(
        [f in (classify.frequency_from_interval(v),
               classify.frequency_from_interval(v, classify.SAMPLE_COUNT_BANDS))
         for f, v in zip(s["frequency"], s["median_interval_days"])], index=s.index)
    ok = (s["n_obs"].notna() & banded).groupby(s["source"]).all()
    return sorted(ok.index[ok])


def source_overlap(st) -> pd.DataFrame:
    """How much each source adds that the others do not already hold.

    The Water Quality Portal aggregates CEDEN, SWAMP, the irrigated lands
    coalitions, and USGS discrete samples, so harvesting those separately
    should mostly return records the portal already has. Mostly is not a
    number, which is why this table exists.
    """
    by_uid = st.groupby("station_uid")["source"].agg(set)
    rows = []
    for src in sorted(st["source"].unique()):
        uids = set(st.loc[st["source"] == src, "station_uid"])
        alone = sum(1 for u in uids if by_uid[u] == {src})
        rows.append({
            "source": src,
            "records": int((st["source"] == src).sum()),
            "stations": len(uids),
            "unique to this source": alone,
            "also in another source": len(uids) - alone,
        })
    return pd.DataFrame(rows).sort_values("stations", ascending=False)


def unit_problems(sp) -> pd.DataFrame:
    """Station-parameter rows whose unit is not the quantity the name implies."""
    if "unit_convertible" not in sp.columns:
        return pd.DataFrame()
    bad = sp[~sp["unit_convertible"].astype(bool)]
    if bad.empty:
        return bad
    # A null unit and an empty one are the same "no unit" reason.
    bad = bad.assign(unit=bad["unit"].fillna(""))
    return (bad.groupby(["parameter", "unit", "unit_note"], dropna=False)
               .size().rename("rows").reset_index()
               .sort_values("rows", ascending=False))


# The kinds of reason `sjrwq.units` gives for a unit with no conversion, as
# (plural, singular) prose. `_unit_kind` sorts each `unit_note` into one.
UNIT_KIND_PROSE = {
    "quantity": ("are a different quantity filed under the parameter's name",
                 "is a different quantity filed under the parameter's name"),
    "none": ("have no unit", "has no unit"),
    "unrecognized": ("have a unit string outside the tables in `sjrwq/units.py`",
                     "has a unit string outside the tables in `sjrwq/units.py`"),
    "basis": ("join more than one reporting basis in one row",
              "joins more than one reporting basis in one row"),
    "other": ("have another reason, listed in `unit_note`",
              "has another reason, listed in `unit_note`"),
}


def _unit_kind(note: object) -> str:
    """Which kind in UNIT_KIND_PROSE one `unit_note` belongs to.

    Matches the wording `units.resolve` and `units.restate_basis` write.
    """
    n = str(note)
    if (n in set(units.WRONG_QUANTITY.values()) or " unit reported for a " in n
            or n.startswith("pool elevation")):
        return "quantity"
    if n == "source reported no unit":
        return "none"
    if n.startswith("unrecognized unit"):
        return "unrecognized"
    if "bases joined" in n or "mix of bases" in n:
        return "basis"
    return "other"


def _unit_kinds(sp) -> tuple[pd.Series, str]:
    """Unconvertible rows by kind, and the counts as a prose list, largest first."""
    if "unit_convertible" not in sp.columns:
        return pd.Series(dtype=int), ""
    notes = sp.loc[~sp["unit_convertible"].astype(bool), "unit_note"]
    kinds = notes.map(_unit_kind).value_counts()
    order = [k for k in kinds.index if k != "other"] + (
        ["other"] if "other" in kinds.index else [])
    text = _join(f"{int(kinds[k])} {UNIT_KIND_PROSE[k][int(kinds[k]) == 1]}"
                 for k in order)
    return kinds, text


def _edi_731_note(st, sp, e731, n_dataset) -> str:
    """The `edi.731` sentences in the EDI paragraph, from that package's rows.

    Empty when the harvest has no `edi.731` rows. The parameter count, the
    period, and the Vernalis dates all come from `station_parameter.parquet`,
    because they differ between the revision the anonymous route harvests and
    the one an EDI token reaches.
    """
    if e731.empty:
        return ""
    n_par = e731.groupby("station_uid")["parameter"].nunique()
    per = (f"{n_par.iloc[0]} parameters" if n_par.nunique() == 1
           else f"{n_par.min()} to {n_par.max()} parameters")
    n_rows = (int((e731["period_scope"] == "dataset").sum())
              if "period_scope" in e731.columns else len(e731))
    same = e731.groupby("station_uid")["parameter"].agg(frozenset).nunique() == 1
    text = (f"Six decades of Delta water quality, `edi.731`, has {n_rows} of the "
            f"{n_dataset} dataset rows, and lists {'the same ' if same else ''}{per} "
            f"at each of its {n_par.size} stations")
    fish = e731["station_uid"].astype(str).str.contains("DJFMP|EDSM")
    if "chloride" in set(e731.loc[fish, "parameter"]):
        text += (", chloride among them at fish-survey stations, where the monitoring "
                 "program behind the database takes no chloride sample")
    text += (f". The package's period of record, {e731['por_start'].min():%Y} to "
             f"{e731['por_end'].max():%Y}, covers the whole database")
    lat, lon = FIXED_POINTS["Vernalis (downstream boundary)"]
    v, _ = nearest_station(st, sp, lat, lon, "specific_conductance", True)
    if v is not None and v["station_uid"] in set(e731["station_uid"]):
        rows = sp[(sp["station_uid"] == v["station_uid"])
                  & (sp["parameter"] == "specific_conductance")]
        kept = station_level(rows)
        cont = kept.loc[kept["record_type"] == "continuous", "por_start"].min()
        if rows["por_start"].min() < kept["por_start"].min() and pd.notna(cont):
            text += (", so without the filter the conductance record at Vernalis "
                     f"would date from {rows['por_start'].min():%Y} to "
                     f"{rows['por_end'].max():%Y}, where the continuous record "
                     f"starts in {cont:%Y}")
    return text + ". "


README_START = "<!-- harvest-summary:start -->"
README_END = "<!-- harvest-summary:end -->"


def _mainstem_km_of(point_name: str) -> float | None:
    """River km of one named control point, or None if it is off the line."""
    if point_name not in FIXED_POINTS:
        return None
    lat, lon = FIXED_POINTS[point_name]
    line = merged_mainstem_line(gpd.read_file(PROCESSED / "domain.gpkg",
                                              layer="mainstem"))
    pt = gpd.GeoDataFrame(geometry=gpd.points_from_xy([lon], [lat]),
                          crs="EPSG:4326")
    km = river_distance_km(pt, line, max_offset_m=10000.0)
    return None if pd.isna(km.iloc[0]) else float(km.iloc[0])


def _headwater_ec(st, sp, line) -> str:
    """The README sentence on EC above the uppermost continuous EC station.

    `line` is the merged mainstem. The search takes every channel class and
    every period, so the 0 in the sentence covers the whole inventory rather
    than the reporting rule. Empty when the mainstem has no continuous EC
    station.
    """
    cont = _mainstem_stations(st, sp, line, "specific_conductance",
                              continuous_only=True, river_only=False)
    if cont.empty:
        return ""
    top = cont.loc[cont["mainstem_km"].idxmax()]
    any_ec = _mainstem_stations(st, sp, line, "specific_conductance",
                                continuous_only=False, river_only=False)
    n_discrete = int((any_ec["mainstem_km"] > top["mainstem_km"]).sum())
    top_km = round(top["mainstem_km"])
    return (f" Above `{top['name']}` at {top_km} km, 0 stations have recorded EC "
            f"continuously along the {round(line.length / 1000.0) - top_km} km to the "
            "headwaters, across all channel classes and all years, and "
            f"{n_discrete} have discrete EC samples.")


def _outside_filtered(sp, cat, cal) -> tuple[int, int, list[str]]:
    """Catalog stations with a temperature or EC row that `cal` leaves out.

    `inventory.calibration_catalog` keeps a station with a station-scope
    temperature or conductance record that has a start date. Returns the
    number of catalog stations outside it whose station-scope rows have no
    start date, the number whose rows are all dataset-scope, and the sources
    of those dataset-scope rows as prose.
    """
    rows = sp[sp["parameter"].isin(["water_temperature", "specific_conductance"])
              & sp["station_uid"].isin(set(cat["station_uid"]))
              & ~sp["station_uid"].isin(set(cal["station_uid"]))]
    kept = station_level(rows)
    package = rows[~rows["station_uid"].isin(set(kept["station_uid"]))]
    sources = [SOURCE_PROSE.get(s, s) for s in sorted(package["source"].dropna().unique())]
    return (int(kept["station_uid"].nunique()), int(package["station_uid"].nunique()),
            sources)


def harvest_summary(st, sp, cat, cal) -> str:
    """The bullets under "What the harvest found" in the README.

    Written from the data every time `sjrwq.gaps` runs, because this is the
    block readers quote and a hand-typed number goes stale within one harvest.
    """
    usable = cal["usable"].astype(bool)
    cont = usable & cal["continuous"].astype(bool)
    gauged = usable & cal["has_flow"].astype(bool)
    ms = merged_mainstem_line(gpd.read_file(PROCESSED / "domain.gpkg",
                                            layer="mainstem"))

    # Friant Dam is the upstream boundary, so a gap above it is in the Sierra
    # headwaters and outside the reach the model routes. The EC gap above the
    # dam, quoted without that context, is true and misleading.
    friant = _mainstem_km_of("Friant Dam (upstream boundary)")

    def worst(param):
        # mainstem_gaps returns gaps in river order, not by size. The report
        # sorts them for display and this needs the same longest-first pick.
        _, _, gaps = mainstem_gaps(st, sp, gpd.read_file(
            PROCESSED / "domain.gpkg", layer="mainstem"), param,
            since=reporting_since())
        if friant is not None:
            # A gap that straddles the dam keeps only its part below it, and
            # that part has to clear the same 15 km floor as mainstem_gaps.
            ends = [(a, round(min(b, friant), 1)) for a, b, _ in gaps if a < friant]
            gaps = [(a, b, round(b - a, 1)) for a, b in ends]
            gaps = [g for g in gaps if g[2] >= 15.0]
        if not gaps:
            return None
        # Whole kilometers for the bullet, with the length taken from the
        # rounded ends so the three numbers subtract.
        a, b, _ = max(gaps, key=lambda g: g[2])
        return round(a), round(b), round(b) - round(a)

    t_gap, e_gap = worst("water_temperature"), worst("specific_conductance")
    multi = int((cat["n_sources"] > 1).sum())
    unconvertible = (int((~sp["unit_convertible"].astype(bool)).sum())
                     if "unit_convertible" in sp.columns else 0)

    bullet_records = (
        f"- {len(st):,} source records from eight APIs, collapsing to "
        f"{len(cat):,} distinct stations, {multi:,} of which appear in more "
        "than one source.")
    n_well = int((cat["channel_class"] == "groundwater").sum())
    wq = wqp_endpoint_counts()
    bullet_thin = (
        "- Each station in the catalog reports at least one target "
        f"parameter, and {n_well:,} of them are groundwater wells. The Water "
        "Quality Portal's station endpoint returns every monitoring location in "
        "a HUC regardless of what it measures"
        + (f", {int(wq['locations_from_station_endpoint']):,} in this run. "
           + _wqp_removed(wq) if wq else
           ", and the harvest drops the ones reporting 0 of its characteristics."))
    boundary = cal["off_channel"].astype(bool)
    classes = cal["channel_class"]
    n_river = int((usable & classes.isin(RIVER_CLASSES | {"reservoir"})).sum())
    n_unknown = int((usable & (classes == "unknown")).sum())
    n_boundary = int((usable & boundary).sum())
    period = domain_config()["calibration_period"]
    least = period["min_record_years"]
    pair = usable & cal["has_pair"].astype(bool)
    undated, package_only, package_src = _outside_filtered(sp, cat, cal)
    rest = []
    if undated:
        rest.append(f"with no start date ({undated})")
    if package_only:
        rest.append(f"with only a package-wide period from {_join(package_src)} "
                    f"({package_only})")
    bullet_core = (
        f"- {len(cal):,} stations have a dated station-scope record of water "
        "temperature or specific conductance, and "
        f"`data/processed/catalog_filtered.csv` lists those {len(cal):,}. "
        + (f"{undated + package_only} more have a temperature or conductance "
           f"record {' or '.join(rest)}, and stay out of that file. " if rest else "")
        + f"{int(usable.sum())} of the {len(cal):,} hold {least} years or "
        f"more of one since {pd.Timestamp(period['start']):%Y}. "
        f"{int(pair.sum())} measure both parameters and "
        f"{int(cont.sum())} measure both with a continuous monitor. "
        f"{int(gauged.sum())} of the {int(usable.sum())} also gage discharge. "
        "None of those three is a "
        "requirement: a thermograph calibrates the temperature field with no "
        "conductance beside it, and a monthly conductance sample against a "
        f"gaged flow constrains a seasonal signal and a load. {n_river} sit on "
        f"rivers, sloughs or reservoirs, {n_unknown} have a name that matches "
        f"no channel class (`unknown`), and {n_boundary} sit on canals, drains, "
        "permitted outfalls, or monitoring wells: real records, and boundary "
        "terms rather than river calibration sites. "
        "`figures/10_current_stations.png` maps all "
        f"{int(usable.sum())} and `data/processed/catalog_filtered.csv` lists "
        "them.")
    # Computed rather than fixed, with the same river and subbasin constraint
    # as the report's reservoir table, so a gage on the next river over does
    # not count for a dam.
    n_dams = len(RESERVOIRS)
    near = 0
    for name, (lat, lon) in RESERVOIRS.items():
        _, d = nearest_station(st, sp, lat, lon, "water_temperature", True,
                               since=reporting_since(),
                               river=RESERVOIR_RIVER.get(name),
                               huc8_name=RESERVOIR_HUC8.get(name))
        if d is not None and d <= 15.0:
            near += 1
    rp = _rise_profiles(st, sp)
    n_at = len(rp["at"])
    bullet_profiles = (
        f"- {near} of the {n_dams} rim dams have a station within 15 km on the "
        f"same river, or in the same subbasin for the {len(RESERVOIR_HUC8)} on "
        "rivers with no traced centerline, measuring temperature with a "
        f"continuous monitor that still reports. {len(rp['profiled'])} of the "
        f"{n_dams} have a flagged depth profile in the eight harvested sources. "
        "RISE flags profiles explicitly and sets that flag on "
        f"{rp['n_flag']} of its {rp['n_series']} series in this basin. RISE "
        f"publishes series within {RISE_AT_DAM_KM:g} km of {n_at} of the "
        f"{n_dams} dams" + (f" ({_dam_labels(rp['at'])})" if n_at else "")
        + f", so the absence is a checked finding at {n_at} and unchecked at "
        f"the other {n_dams - n_at}. {PROFILE_HOLDERS}.")
    # The examples are the three commonest reasons in this run, not a fixed
    # list: fixing a crosswalk entry retires a reason, and a typed example
    # outlives the problem it describes.
    reasons = (sp.loc[~sp["unit_convertible"].astype(bool), "unit_note"]
                 .value_counts().head(3) if unconvertible else [])
    _, kinds_text = _unit_kinds(sp)
    bullet_units = (
        f"- {unconvertible:,} of the {len(sp):,} station-parameter rows have a "
        "unit that does not convert to the canonical one in "
        "`config/parameters.yml`"
        + (f": {kinds_text}" if kinds_text else "")
        + (". The three commonest reasons: "
           + "; ".join(f"{r} ({n})" for r, n in reasons.items())
           if len(reasons) else "")
        + ". `station_parameter.parquet` lists each row's reason in `unit_note`.")

    lines = [bullet_records, bullet_thin, bullet_core]
    if t_gap:
        lines.append(
            f"- Below Friant Dam, the upstream boundary at "
            f"{friant:.0f} km, the longest run of mainstem with no continuous "
            f"temperature station is {t_gap[2]:.0f} km, from {t_gap[0]:.0f} to "
            f"{t_gap[1]:.0f} km above Vernalis. The river is "
            f"{ms.length / 1000:.0f} km end to end.")
    if e_gap:
        lines.append(
            f"- For specific conductance the longest such run is "
            f"{e_gap[2]:.0f} km, from {e_gap[0]:.0f} to {e_gap[1]:.0f} km."
            + _headwater_ec(st, sp, ms))
    lines += [bullet_profiles, bullet_units]
    return "\n".join(lines)


def update_readme(text: str) -> bool:
    """Rewrite the generated block in the README, leaving the prose alone."""
    path = ROOT / "README.md"
    if not path.exists():
        return False
    body = path.read_text()
    if README_START not in body or README_END not in body:
        log.warning("README has no harvest-summary markers; leaving it alone")
        return False
    head, rest = body.split(README_START, 1)
    _, tail = rest.split(README_END, 1)
    path.write_text(f"{head}{README_START}\n{text}\n{README_END}{tail}")
    log.info("rewrote the harvest summary in README.md")
    return True


# Stockton's treatment plant, as eSMR names the facility. Its monitoring
# locations sit in the San Joaquin Delta subbasin, outside `huc8_in_scope`.
OUTSIDE_FACILITY = "Wastewater Recovery Center"


def _outside_facility(st) -> str:
    """The Known holes paragraph on Stockton's plant, from the eSMR interim tables.

    Record counts are eSMR's `n_obs` for the parameters this inventory
    harvests, summed per facility. Empty when the interim tables are absent,
    have no rows for the facility, or place one of its locations inside the
    domain.
    """
    s_path = INTERIM / "esmr_stations.gpkg"
    p_path = INTERIM / "esmr_station_parameter.parquet"
    if not (s_path.exists() and p_path.exists()):
        return ""
    es = gpd.read_file(s_path)
    ep = pd.read_parquet(p_path)
    es["source_station_id"] = es["source_station_id"].astype(str)
    ep["source_station_id"] = ep["source_station_id"].astype(str)
    inside = set(st.loc[st["source"] == "esmr", "source_station_id"].astype(str))
    rows = ep.merge(es[["source_station_id", "facility_name"]], on="source_station_id")
    locs = es[es["facility_name"] == OUTSIDE_FACILITY]
    mine = rows[rows["facility_name"] == OUTSIDE_FACILITY]
    if mine.empty or locs["source_station_id"].isin(inside).any():
        return ""
    n_rec, n_loc = int(mine["n_obs"].sum()), len(locs)
    first = pd.to_datetime(mine["por_start"]).min()
    text = (f"\n**Stockton's plant sits outside the domain.** eSMR lists it under the "
            f"facility name \"{OUTSIDE_FACILITY}\", with {n_rec:,} records of this "
            f"inventory's parameters from {n_loc} monitoring locations since {first:%Y}. ")
    dom = (rows[rows["source_station_id"].isin(inside)]
           .groupby("facility_name")["n_obs"].sum())
    if not dom.empty:
        text += (f"That is {n_rec / dom.max():.1f} times the {int(dom.max()):,} of "
                 f"\"{dom.idxmax()}\", the facility with the most records inside the "
                 "domain. ")
    h8 = (gpd.read_file(PROCESSED / "domain.gpkg", layer="huc8")[["huc8", "name", "geometry"]]
          .rename(columns={"name": "subbasin"}))
    hit = gpd.sjoin(locs[["geometry"]].to_crs(h8.crs), h8, predicate="within")
    if not hit.empty:
        code = hit["huc8"].mode().iloc[0]
        n_in = int((hit["huc8"] == code).sum())
        where = f"All {n_loc}" if n_in == n_loc else f"{n_in} of its {n_loc}"
        text += (f"{where} locations fall in the "
                 f"{hit.loc[hit['huc8'] == code, 'subbasin'].iloc[0]} subbasin "
                 f"({code}), outside `huc8_in_scope` in `config/domain.yml`, so the "
                 "study-area clip removes the plant. The records are already in "
                 f"`data/interim`: move {code} into `huc8_in_scope` and re-run.")
    return text + "\n"


def _cdec_bad_dates(st) -> str:
    """The CDEC sentences under "Source data errors handled here".

    Lists the CDEC records in the interim table with a start before
    EARLIEST_PLAUSIBLE or an end before the start. The plausibility guard in
    `sjrwq.inventory` runs after the study-area clip, so it nulls the ones at
    stations in `st`. Empty when the table is absent or has neither.
    """
    path = INTERIM / "cdec_station_parameter.parquet"
    if not path.exists():
        return ""
    c = pd.read_parquet(path)
    if not pd.api.types.is_datetime64_any_dtype(c["por_start"]):
        return ""
    start, end = c["por_start"], c["por_end"]
    items = []
    early = c[start.notna() & (start < EARLIEST_PLAUSIBLE)]
    for _, r in early.drop_duplicates(["station_id", "parameter"]).iterrows():
        d = r["por_start"]
        items.append((r["station_id"],
                      (f"a {r['parameter'].replace('_', ' ')} record at station "
                       f"{r['station_id']} beginning "
                       f"{d.month:02d}/{d.day:02d}/{d.year:04d}")))
    rev = c[start.notna() & end.notna() & (end < start)]
    for _, r in rev.drop_duplicates(["station_id", "parameter"]).iterrows():
        items.append((r["station_id"],
                      (f"a {r['parameter'].replace('_', ' ')} record at station "
                       f"{r['station_id']} ending before it starts")))
    if not items:
        return ""
    listed = set(st.loc[st["source"] == "cdec", "source_station_id"].astype(str))
    ids_out = sorted({s for s, _ in items if str(s) not in listed})
    ids_in = sorted({s for s, _ in items if str(s) in listed})
    n_in = sum(str(s) in listed for s, _ in items)
    text = f"CDEC lists {_join(p for _, p in items)}. "
    if ids_out:
        text += (("Station " if len(ids_out) == 1 else "Stations ") + _join(ids_out)
                 + (" is" if len(ids_out) == 1 else " are") + " outside `catalog.csv`. ")
    if ids_in:
        text += ("The plausibility guard in `sjrwq.inventory` nulls the start date of "
                 + ("the record" if n_in == 1 else "the records") + f" at {_join(ids_in)} "
                 "and logs each date it nulls. It keeps "
                 + ("the row. " if n_in == 1 else "both rows. " if n_in == 2
                    else f"all {n_in} rows. "))
    return text


def _state_well_note(sp, cat, cal) -> str:
    """The State Well Number bullet, from the catalog and the portal's interim table."""
    swn = cat["name"].astype(str).str.strip().str.match(classify.STATE_WELL_NUMBER)
    if not swn.any():
        return ""
    names = set(cat.loc[swn, "name"].astype(str).str.strip())
    uids = sorted(set(cat.loc[swn, "station_uid"]))
    ec = (sp[sp["station_uid"].isin(uids) & (sp["parameter"] == "specific_conductance")]
          .groupby("station_uid")["n_obs"].sum().reindex(uids, fill_value=0).astype(int))
    text = ("- DWR publishes stations whose names are California State Well Numbers, "
            f"such as `{cat.loc[swn, 'name'].iloc[0]}`")
    path = INTERIM / "wqp_stations.gpkg"
    if path.exists():
        ws = gpd.read_file(path, columns=["name", "location_type"], ignore_geometry=True)
        types = ws.loc[ws["name"].astype(str).str.strip().isin(names),
                       "location_type"].value_counts()
        if len(types) == 1:
            text += f", and gives them a portal `location_type` of \"{types.index[0]}\""
        elif len(types):
            text += (", and gives them a portal `location_type` of "
                     + _join(f"\"{t}\" ({n})" for t, n in types.items()))
        if len(types):
            text += ", which stays as published in `data/interim/wqp_stations.gpkg`"
    counts = (f"{ec.min()}" if ec.min() == ec.max() else f"{ec.min()} to {ec.max()}")
    text += (f". {len(uids)} reach `catalog.csv`, with {counts} specific conductance "
             "samples each")
    ranks = cal.loc[cal["station_uid"].isin(uids) & cal["rank"].notna(), "rank"]
    if len(ranks):
        text += f", and {len(ranks)} reach the shortlist in `catalog_filtered.csv`"
    text += (". `sjrwq.classify` matches the name pattern and sets their "
             "`channel_class` to `groundwater`")
    if len(ranks):
        text += (f", so `_rank_shortlist` numbers the {len(ranks)} among the off-channel "
                 "stations at the end of the shortlist, at ranks "
                 f"{int(ranks.min())} to {int(ranks.max())} of "
                 f"{int(cal['rank'].notna().sum())}")
    return text + ".\n"


def _huc8_crosscheck() -> tuple[int, int] | None:
    """Portal stations with a published HUC12, and how many sit in another HUC8.

    Reads `data/interim/wqp_stations.gpkg`, the portal stations the harvest
    keeps, and the HUC8 polygons in `domain.gpkg`. Counts the stations whose
    coordinate falls inside a mapped HUC8, and of those, the ones whose
    published HUC12 starts with a different HUC8 code. None when the file or
    the column is absent.
    """
    path = INTERIM / "wqp_stations.gpkg"
    if not path.exists():
        return None
    ws = gpd.read_file(path)
    if "huc12_wqp" not in ws.columns:
        return None
    pub = ws[ws["huc12_wqp"].notna()]
    layers = [gpd.read_file(PROCESSED / "domain.gpkg", layer=layer)[["huc8", "geometry"]]
              for layer in ("huc8", "surround")]
    polys = gpd.GeoDataFrame(pd.concat(layers, ignore_index=True),
                             crs=layers[0].crs).drop_duplicates("huc8")
    hit = gpd.sjoin(pub.to_crs(polys.crs), polys, predicate="within")
    hit = hit[~hit.index.duplicated()]
    return len(hit), int((hit["huc12_wqp"].astype(str).str[:8]
                          != hit["huc8"].astype(str)).sum())


def build_report() -> str:
    st, sp, ms = _load()
    prim = st[st["is_primary"]]
    cal = pd.read_csv(PROCESSED / "catalog_filtered.csv")
    least = domain_config()["calibration_period"]["min_record_years"]
    rp = _rise_profiles(st, sp)
    out: list[str] = []
    w = out.append

    win_start = reporting_since()
    win_label = window_label()
    # The window midpoint as a calendar day, for the sentences that state the
    # reporting rule.
    mid_day = f"{win_start:%B} {win_start.day}, {win_start.year}"

    w("# Gap analysis\n")
    w("Where the observational record runs out, measured from `data/processed`. ")
    w("Regenerate with `python -m sjrwq.gaps` after a harvest.\n")
    w(f"Inventory as built: **{len(st):,} source records** collapsing to "
      f"**{prim['station_uid'].nunique():,} unique stations**, "
      f"{int((prim['n_sources'] > 1).sum()):,} of them appearing in more than one source.\n")
    w("\nTwo conventions apply throughout. A station counts as observing the river "
      "only where it sits on a natural channel or serves as a permitted "
      "receiving-water point. The inventory still lists and maps canals, agricultural "
      "drains, and monitoring wells, and none of the three closes a river gap however "
      f"close to the centerline it falls. The coverage tables run over {win_label}, the "
      "window in `config/domain.yml`, because a gage that ran from 1955 to 1970 is "
      "history rather than coverage, and a record has to reach the second half of the "
      f"window, which begins {mid_day}, to count. The mainstem section gives the "
      "all-time station count beside the windowed one, and the boundary and reservoir "
      "sections name each match that changes without that rule.\n")

    w("\n## Mainstem continuous coverage\n")
    w("Gaps of 15 km or more along the San Joaquin between Vernalis (0 km) and the ")
    w("headwaters. Only continuous records count, because quarterly samples cannot ")
    w("calibrate a temperature model, and only stations on a natural channel or a ")
    w("permitted receiving-water point count, because a monitoring well two hundred ")
    w("meters from the bank does not observe the river. A station also needs a record "
      f"reaching the second half of {win_label}; see `coverage.reporting_since`.\n")
    line = merged_mainstem_line(ms)
    for param in CORE:
        n, total, gaps = mainstem_gaps(st, sp, ms, param, since=win_start)
        n_all, _, _ = mainstem_gaps(st, sp, ms, param)
        # The same window and the same continuity rule, with the channel-class
        # filter off, so the comparison isolates the one thing that changes.
        n_loose, _, gaps_loose = mainstem_gaps(st, sp, ms, param, river_only=False,
                                               since=win_start)
        longest = max((g[2] for g in gaps), default=0.0)
        longest_loose = max((g[2] for g in gaps_loose), default=0.0)
        strict = _mainstem_stations(st, sp, line, param, since=win_start)
        loose = _mainstem_stations(st, sp, line, param, river_only=False,
                                   since=win_start)
        extra = loose[~loose["station_uid"].isin(set(strict["station_uid"]))]
        # Which listed gaps the extra stations fall inside, from the same
        # unrounded runs the table below lists.
        runs = _open_runs(strict["mainstem_km"], line.length / 1000.0)
        hit = [next((r for r in runs if r[0] < km < r[1]), None)
               for km in extra["mainstem_km"]]
        n_inside = sum(r is not None for r in hit)
        n_split = len({r for r in hit if r is not None})
        w(f"\n**{param.replace('_', ' ')}.** Along {total} km of mainstem, {n} "
          "stations on the river hold a continuous record reaching the second half "
          f"of {win_label}, which begins {mid_day}. ")
        w(f"{n_all} have held one at some point. ")
        if extra.empty:
            w("Counting every channel class over the same window adds 0 stations.\n")
        else:
            kinds = extra["channel_class"].fillna("unknown").value_counts()
            w(f"Counting every channel class over the same window gives {n_loose}, "
              f"and the {len(extra)} extra are "
              + _join(f"{c} {k}" for k, c in kinds.items()) + " stations. ")
            if n_inside:
                w(f"{n_inside} of those {len(extra)} fall inside {n_split} of the "
                  "gaps listed below and split "
                  + ("it, " if n_split == 1 else "each one, "))
            else:
                w(f"0 of those {len(extra)} fall inside a gap listed below, ")
            if longest_loose == longest:
                w(f"and the longest gap stays {longest} km.\n")
            else:
                w(f"and counting them moves the longest gap from {longest} km to "
                  f"{longest_loose} km.\n")
        if not gaps:
            w("\nNo gaps over 15 km.\n")
            continue
        w("\n| from (km) | to (km) | length (km) |\n| ---: | ---: | ---: |\n")
        for a, b, g in sorted(gaps, key=lambda x: -x[2]):
            w(f"| {a} | {b} | {g} |\n")

    key_points = {**confluences(), **FIXED_POINTS}
    w("\n## Boundary and confluence coverage\n")
    w("Distance from each control point to the nearest continuous station measuring ")
    w("the named parameter. Beyond a few kilometers, the boundary condition needs ")
    w("interpolation or a request.\n")
    w("\n| control point | nearest continuous temperature | km | nearest continuous EC | km |\n")
    w("| --- | --- | ---: | --- | ---: |\n")
    # Each cell searched twice: with the reporting rule for the table, and
    # without it for the sentence under the table.
    all_time = []
    for name, (lat, lon) in key_points.items():
        cells = []
        for param, short in (("water_temperature", "temperature"),
                             ("specific_conductance", "EC")):
            s_row, d = nearest_station(st, sp, lat, lon, param, True, since=win_start)
            cells += [str(s_row["name"]) if s_row is not None else "none", d]
            a_row, a_d = nearest_station(st, sp, lat, lon, param, True)
            if a_row is not None and (s_row is None
                                      or a_row["station_uid"] != s_row["station_uid"]):
                end = _last_continuous(sp, a_row["station_uid"], param)
                all_time.append(
                    f"{name} takes `{a_row['name']}` for {short}, {a_d} km away"
                    + (f", with a continuous {short} record ending {end:%B %Y}"
                       if pd.notna(end) else ""))
        w(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |\n")
    n_cells = 2 * len(key_points)
    w(f"\nWithout the rule that a record reach the second half of {win_label}, "
      f"{len(all_time)} of the {n_cells} matches "
      + ("changes" if len(all_time) == 1 else "change")
      + (": " + "; ".join(all_time) if all_time else "") + ".\n")

    # The table's search, then the same search with the two filters dropped
    # and with the river constraint dropped, so the sentences above the table
    # list what each one changes in this run.
    def dam_search(name, **kw):
        lat, lon = RESERVOIRS[name]
        return nearest_station(st, sp, lat, lon, "water_temperature", True, **kw)

    matches, unfiltered, anywhere = {}, {}, {}
    for name in RESERVOIRS:
        own = {"river": RESERVOIR_RIVER.get(name),
               "huc8_name": RESERVOIR_HUC8.get(name)}
        matches[name] = dam_search(name, since=win_start, **own)
        unfiltered[name] = dam_search(name, river_only=False, **own)
        anywhere[name] = dam_search(name, since=win_start)

    def differs(name, alt):
        s_row = matches[name][0]
        return alt[0] is not None and (s_row is None
                                       or alt[0]["station_uid"] != s_row["station_uid"])

    def unfiltered_match(name):
        s_row, d = unfiltered[name]
        end = _last_continuous(sp, s_row["station_uid"], "water_temperature")
        return (f"{name} takes `{s_row['name']}` ({s_row['channel_class']}), "
                f"{d} km away"
                + (f", with a continuous temperature record ending {end:%B %Y}"
                   if pd.notna(end) else ""))

    w("\n## Reservoir release temperature\n")
    w("Release temperature sets the upstream boundary for every reach below a rim dam. ")
    w("Distance to the nearest continuous temperature station is a weak proxy for ")
    w("whether a station observes that release, and the eight harvested sources hold ")
    w(f"a flagged depth profile at {len(rp['profiled'])} of the {len(RESERVOIRS)} dams. ")
    w("A reservoir release model needs the profiles.\n")
    skipped = sorted(set(prim["channel_class"].dropna()) - RIVER_CLASSES)
    w("\nTwo filters shape this table. Only stations classed "
      f"{' or '.join(sorted(RIVER_CLASSES))} count, so the search skips the "
      f"{_join(skipped)} classes. Records that stopped are out, because a gage "
      "that went quiet looks like an observed boundary condition without being one. ")
    changed = [n for n in RESERVOIRS if differs(n, unfiltered[n])]
    if changed:
        w(f"With both filters off, {len(changed)} of the {len(RESERVOIRS)} dams "
          + ("takes" if len(changed) == 1 else "take") + " a different station: "
          + "; ".join(unfiltered_match(n) for n in changed) + ".\n")
    else:
        w(f"With both filters off, the same {len(RESERVOIRS)} stations come back.\n")
    w("\nThis table matches each dam against stations on its own river, or in its own "
      "subbasin where the river has no traced centerline. ")
    crossed = [n for n in RESERVOIRS if differs(n, anywhere[n])]
    if crossed:
        w(f"On distance alone, {len(crossed)} of the {len(RESERVOIRS)} dams "
          + ("takes" if len(crossed) == 1 else "take") + " a different station: "
          + "; ".join(
              f"{n} takes `{anywhere[n][0]['name']}`"
              + (f" on the {anywhere[n][0]['river']}"
                 if pd.notna(anywhere[n][0].get("river")) else "")
              + f", {anywhere[n][1]} km away" for n in crossed) + ".\n")
    else:
        w(f"On distance alone, the same {len(RESERVOIRS)} stations come back.\n")
    reaches = _reach_lines()
    below, above, unplaced = [], [], []
    for name, (s_row, _) in matches.items():
        if s_row is None:
            continue
        pos = _below_dam(reaches, RESERVOIR_RIVER.get(name), RESERVOIRS[name],
                         gpd.GeoSeries([s_row.geometry], crs=st.crs))
        (unplaced if pos is None else below if pos else above).append(name)
    w("\nRead the distances as an upper bound on how well the boundary is known, not a "
      "lower one. ")
    if below:
        w(f"On a traced river, the match sits below the dam for {_join(below)}. ")
    if above:
        w(f"The match sits above the dam for {_join(above)}, so that station "
          "measures the inflow side of the dam rather than the release. ")
    if unplaced:
        w(f"{_join(unplaced)} sit on rivers with no traced centerline, so this report "
          "does not place their matches above or below the dam.")
    w("\n")
    w("\n| reservoir | nearest continuous station on the river, with a record reaching "
      f"the second half of {win_label} (from {mid_day}) | km |"
      "\n| --- | --- | ---: |\n")
    for name, (s_row, d) in matches.items():
        w(f"| {name} | {str(s_row['name']) if s_row is not None else 'none'} | {d} |\n")
    n_at = len(rp["at"])
    w(f"\nRISE publishes series within {RISE_AT_DAM_KM:g} km of {n_at} of the "
      f"{len(RESERVOIRS)} dams and flags a depth profile at {len(rp['profiled'])} of "
      f"those {n_at}. {PROFILE_HOLDERS}.\n")

    pd_tbl = point_discharges(st, sp)
    w("\n## Return-flow and drain monitoring points\n")
    w("An inventory of what exists, not a list of what is missing. Municipal and ")
    w(f"industrial monitoring points and agricultural drains within {DISCHARGE_KM:g} km ")
    w("of the San Joaquin or one of the mapped tributaries. ")
    if not pd_tbl.empty:
        by_source = pd_tbl["source"].value_counts()
        w(f"By primary source record, the {len(pd_tbl)} rows in the table come from "
          + _join(f"{SOURCE_PROSE.get(s, s)} ({n})" for s, n in by_source.items())
          + ". ")
        w(_discharge_kinds(pd_tbl).rstrip())
    w("\n")
    period_start = pd.Timestamp(domain_config()["calibration_period"]["start"])
    w("\nThe shortlist ")
    w(f"in `catalog_filtered.csv` includes the points holding {least} years or more of ")
    w(f"temperature or conductance since {period_start:%Y}, because reporting frequency ")
    w("does not decide membership: a permit obliging monthly conductance, against a ")
    w("gaged flow, still gives a monthly salt load. The columns below hold the measured ")
    w("reporting frequency so the difference stays visible, in the vocabulary the ")
    w("harvest uses. `daily` on a flow column is an average daily discharge, a real ")
    w("load term; `irregular` is a series whose measured interval between samples "
      f"exceeds {classify.FREQUENCY_BANDS[-1][0]:g} days; and `reported` marks a "
      "row with no stated frequency and no interval to measure, such as a single "
      "sample.\n")
    manteca = (pd_tbl[pd_tbl["name"].str.contains("Manteca", na=False)]
               if not pd_tbl.empty else pd_tbl)
    w("\nOne caution on position. An effluent row holds the coordinate of the ")
    w("treatment plant, not of the pipe outlet, and the two can be kilometers apart")
    if not manteca.empty:
        w(f", so Manteca appears {manteca['km_to_reach'].iloc[0]:g} km from the river "
          "and still discharges to it")
    w(".\n")
    if not pd_tbl.empty:
        n_none = pd_tbl.attrs.get("dropped_no_measurement", 0)
        n_other = pd_tbl.attrs.get("dropped_other_variables", 0)
        if n_none or n_other:
            w(f"\nA further {n_none + n_other} discharge points and drains fall inside the ")
            w(f"same {DISCHARGE_KM:g} km, and `point_discharges` leaves them out of the ")
            w(f"table: {n_none} report no target parameter, and {n_other} report only ")
            w("chemistry outside flow, temperature, and EC. `catalog.csv` still lists them ")
            w("under a `channel_class` of drain, effluent, or receiving_water.\n")
    if pd_tbl.empty:
        w("\nNone found.\n")
    else:
        w("\n| monitoring point | type | reach | km | flow | temp | EC | samples | record |\n")
        w("| --- | --- | --- | ---: | :-: | :-: | :-: | ---: | --- |\n")
        for _, r in pd_tbl.iterrows():
            w(f"| {r['name']} | {r['type']} | {r['reach']} | {r['km_to_reach']} | "
              f"{r['flow']} | {r['temp']} | {r['ec']} | {r['n']} | {r['por']} |\n")

    w("\n## Imports, exports, and returns\n")
    w("Imported water describes the Delta-Mendota Canal and not the Turlock Canal, ")
    w("which moves Tuolumne water that stayed in the basin. Four roles, each a ")
    w("different term in a balance.\n")
    for role in CONVEYANCE_ROLES:
        w(f"\n- **{role}.** {ROLE_BLURB[role]}")
    w("\n\nCanals do not close a gap in the river tables above, because a canal beside ")
    w("the river does not measure the river's temperature there. They appear ")
    w("here because the flux through them is a term in the balance: the ")
    w("Delta-Mendota Canal's delivery to Mendota Pool enters the San Joaquin on the ")
    w("mainstem. `mainstem km` holds a value where the link sits on the San Joaquin "
      "itself.\n")
    w(_mendota_pool(st, sp, line))
    cv = conveyance_links(st, sp)
    if cv.empty:
        w("\nNone found.\n")
    else:
        w("\n| conveyance | role | reach | km | mainstem km | flow | temp | EC | last |\n")
        w("| --- | --- | --- | ---: | ---: | :-: | :-: | :-: | ---: |\n")
        for _, r in cv.iterrows():
            mk = "" if pd.isna(r["mainstem_km"]) else f"{r['mainstem_km']:.1f}"
            w(f"| {r['name']} | {r['role']} | {r['reach']} | {r['km_to_reach']} | "
              f"{mk} | {r['flow']} | {r['temp']} | {r['ec']} | {r['last']} |\n")

    w("\n## Coverage through time\n")
    w("Stations with an active record in each decade. Coverage is far denser after 1990, ")
    w("so a calibration period before then will be thin whichever sites you choose.\n")
    w("\nA station counts for a decade its record spans only if it averaged at least ")
    f_note = MIN_OBS_PER_DECADE
    measured = [SOURCE_PROSE.get(s, s) for s in _measured_sources(sp)]
    w(f"{f_note} observations per decade. ")
    if measured:
        w(f"{_join(measured)} publish a sample count and a period of record with no "
          "stated interval, and the harvest measures the frequency instead. Without "
          "that floor, two of their samples thirty years apart would count as four "
          "decades of monitoring. ")
    w("Only station-scope records count; see `coverage.station_level`. A row with no "
      "observation count ")
    n_read, n_blank, n_blank_cont, blank_src = _decade_blank_counts(sp)
    w(f"covers every decade its span crosses. {n_blank:,} of the {n_read:,} rows "
      f"here have no count, from {_join(SOURCE_PROSE.get(s, s) for s in blank_src)}, "
      f"and {n_blank_cont:,} of those {n_blank:,} come off a continuous monitor, "
      "whose span is the record.\n\n")
    dec = coverage_by_decade(sp)
    w(dec.to_markdown())
    w("\n")

    w("\n## Co-located stations\n")
    full, cont_all = colocated(sp)
    # Three groups: all three parameters continuous, one or two of them
    # continuous with the rest discrete, and all three discrete. A site that
    # logs discharge continuously and takes its EC by hand is in the middle.
    cont_rows = sp[sp["record_type"] == "continuous"]
    cont_any = set(cont_rows.loc[cont_rows["parameter"].isin(CORE), "station_uid"])
    cont_temp = set(cont_rows.loc[cont_rows["parameter"] == "water_temperature",
                                  "station_uid"])
    mixed = (full & cont_any) - cont_all
    all_discrete = full - cont_any
    # Presence above, dates here: a site can hold all three records in
    # different decades.
    shared, shared_cont = colocated_overlap(sp)
    shared, shared_cont = shared & full, shared_cont & cont_all
    w(f"\n{len(full)} stations measure temperature, specific conductance, and "
      "discharge at one site. A temperature balance needs temperature and flow at one "
      "place, and a salt balance needs conductance and flow, so a site with all three "
      f"serves both. {len(shared)} of the {len(full)} have a period when the three "
      "records overlap, from the latest of the three starts to the earliest of the "
      f"three ends, and the other {len(full) - len(shared)} have none.\n")
    w(f"\n{len(cont_all)} of the {len(full)} record all three with a continuous "
      f"monitor, and {len(shared_cont)} of those {len(cont_all)} have a period when "
      "the three continuous records overlap. "
      f"Of the other {len(full) - len(cont_all)}, {len(mixed)} record one or two of "
      "the three continuously and the rest as discrete samples, and "
      f"{len(mixed & cont_temp)} of those {len(mixed)} have a continuous temperature "
      f"record. The remaining {len(all_discrete)} measure all "
      "three through discrete samples only, which supports a mass balance but cannot "
      "calibrate a temperature model against a diurnal cycle. "
      f"`figures/08_colocation.png` draws the {len(cont_all)} continuous sites apart "
      f"from the other {len(full) - len(cont_all)}.\n")
    in_cal = cal[cal["station_uid"].isin(full)]
    n_ranked = int(in_cal["rank"].notna().sum())
    w("\n`data/processed/catalog_filtered.csv` lists "
      + (f"all {len(full)}" if len(in_cal) == len(full)
         else f"{len(in_cal)} of the {len(full)}")
      + " with the period of each record and the overlap of the temperature and "
      "conductance records. "
      f"The {n_ranked} on the shortlist have the `rank` that numbers the markers on "
      f"`figures/08_colocation.png`, and the other {len(in_cal) - n_ranked} fall "
      f"short of the {least}-year rule and have no rank.\n")

    w("\n## What each source adds\n")
    w("The harvest runs eight APIs and they overlap heavily by design. The portal "
      "republishes CEDEN, SWAMP, the irrigated lands coalitions, and USGS discrete "
      "samples, so the question worth answering is how much a separate harvest of "
      "those sources still finds. `unique to this source` is the number of stations "
      "that no other harvested source reports.\n\n")
    wq = wqp_endpoint_counts()
    n_char = _wqp_characteristics(wq)
    w("A station reaches this table only if it reports one of the target "
      "parameters. The Water Quality Portal's two endpoints return different things: "
      "`Station/search` returns every monitoring location in a HUC regardless of "
      "what it measures, while `Result/search` returns only this inventory's "
      f"{n_char} characteristics. "
      + (f"`Station/search` returned {int(wq['locations_from_station_endpoint']):,} "
         f"locations in this run. {_wqp_removed(wq)} Keeping those "
         f"{int(wq['locations_dropped']):,} would put them into the catalog with no "
         "parameter attached, "
         if wq else "Keeping the difference would put those locations into the "
                    "catalog with no parameter attached, ")
      + "and the figures would then show them as stations with no data, so "
      "`sources/wqp.py` drops them at the harvest and logs the count.\n\n")
    overlap = source_overlap(st)
    w(overlap.to_markdown(index=False))
    w("\n")

    ext = record_extension(st, sp, "edi", min_years=EXTENSION_MIN_YEARS)
    if not ext.empty:
        edi_row = overlap[overlap["source"] == "edi"]
        n_new = int(edi_row["unique to this source"].iloc[0]) if not edi_row.empty else 0
        w("\nA source can add almost no new locations and still be worth harvesting. "
          f"EDI adds {n_new}, because the Interagency Ecological Program's continuous "
          "stations are the DWR and USGS gages CDEC already publishes. What it adds is "
          "length. These are the stations where EDI's earliest series starts "
          f"{EXTENSION_MIN_YEARS:g} years or more before every other record this "
          "inventory holds for the same parameter. The comparison runs continuous "
          "against continuous and discrete against discrete, so a discrete sample "
          "does not extend a sonde record. Station-scope records only; see "
          "`coverage.station_level`.\n\n")
        w(ext.to_markdown(index=False))
        w("\n")

    w("\n## Units that do not match the quantity\n")
    up = unit_problems(sp)
    if up.empty:
        w(f"\nAll {len(sp):,} station-parameter rows have a unit that converts to the "
          "canonical one. This is unusual and worth re-checking.\n")
    else:
        total = int(up["rows"].sum())
        kinds, kinds_text = _unit_kinds(sp)
        n_quantity = int(kinds.get("quantity", 0))
        w(f"\n{total:,} of {len(sp):,} station-parameter rows use a unit that does not "
          "convert to the canonical unit in `config/parameters.yml`. "
          f"{kinds_text[:1].upper()}{kinds_text[1:]}. ")
        if n_quantity:
            w(f"A time-series pull keyed on the parameter name would mix the {n_quantity} "
              + ("row" if n_quantity == 1 else "rows")
              + " of a different quantity in with the rest. ")
        w("`station_parameter.parquet` has each row's conversion factor, or the reason "
          "it has none, in `unit_factor` and `unit_note`.\n\n")
        w(up.head(20).to_markdown(index=False))
        w("\n")
        if len(up) > 20:
            w(f"\n{len(up) - 20} further combinations are in "
              "`station_parameter.parquet` under `unit_convertible` and `unit_note`.\n")

    w("\n## Known holes in this inventory\n")
    w("\nThese are gaps in the harvest itself, not in the underlying monitoring ")
    w("network. This section lists them so a retrieval failure does not pass for an ")
    w("absence of data.\n")
    dropped = INTERIM / "wqp_dropped_characteristics.csv"
    if dropped.exists():
        d = pd.read_csv(dropped)
        w(f"\n**Water Quality Portal losses.** The last run failed to retrieve "
          f"{len(d)} HUC-characteristic combinations.\n\n")
        for huc, grp in d.groupby("huc8"):
            w(f"- HUC {huc}: {len(grp)} characteristics, including "
              f"{', '.join(sorted(grp['characteristic'])[:6])}\n")
        w("\nMost of these are recoverable. Re-run `sjrwq.harvest wqp`; the cached raw "
          "files mean only the failed HUCs go back to the network. If the failures "
          "persist, check that the result queries are still sending "
          "`Accept-Encoding: identity`, because the portal truncates its own gzip and "
          "the resulting error looks like a refusal.\n")
    _edi = sp[sp["source"] == "edi"] if "source" in sp.columns else sp.iloc[0:0]
    _scope = (_edi["period_scope"] if "period_scope" in _edi.columns
              else pd.Series(index=_edi.index, dtype=object))
    _n_inf = int((_scope == "dataset").sum())
    _pkg = (_edi["package"].astype(str).str.rsplit(".", n=1).str[0]
            if "package" in _edi.columns else pd.Series(index=_edi.index, dtype=object))
    per_station = sorted(set(_pkg[_scope == "station"]))
    n_pkg = int(_pkg.nunique())
    w(f"\n**The harvest reaches EDI, and {_n_inf} of its {len(_edi)} EDI rows have a "
      "dataset period rather than a station one.** "
      "PASTA returns HTTP 403 for an anonymous request to its search, revision-list, "
      "and metadata endpoints. Two routes get past that: DataONE indexes EDI as a "
      "member node and serves the same metadata without an account, and PASTA itself "
      "accepts an account token sent as an `edi-token` cookie. `sources/edi.py` "
      "supports both. "
      f"Of the {n_pkg} EDI packages with rows in this inventory, "
      f"{len(per_station)} " + ("publishes" if len(per_station) == 1 else "publish")
      + " one table per station"
      + (f", so only in rows from {_join(f'`{p}`' for p in per_station)} does the "
         "publisher pair each station with its parameters" if per_station else "")
      + f". The other {n_pkg - len(per_station)} "
      + ("publishes a wide table keyed by a station column and lists its"
         if n_pkg - len(per_station) == 1 else
         "publish a wide table keyed by a station column and list their")
      + " stations separately, so ")
    w("the pairing is an inference in `sources/edi.py`, the sample count stays null, and the "
      "reporting interval is a table's row count spread evenly over its stations. "
      "Those rows have `period_scope = dataset` in `station_parameter.parquet`, and "
      "`coverage.station_level` keeps them out of the calibration catalog, the "
      "availability timeline, the decade table, the record-extension table, and the "
      "co-located overlap counts above. The mainstem, boundary, and reservoir searches "
      "test the end date of every row, dataset rows included. ")
    w(_edi_731_note(st, sp, _edi[_pkg == "edi.731"], _n_inf))
    w("Settling the pairing needs the station column from the data file, which is a "
      "download this inventory does not do.\n")
    n_at = len(rp["at"])
    w(f"\n**The harvest holds a flagged depth profile at {len(rp['profiled'])} of the "
      f"{len(RESERVOIRS)} rim dams.** RISE publishes a `hasProfile` flag on each of its "
      f"{rp['n_series']} series in the basin and sets it on {rp['n_flag']}. RISE "
      f"publishes series within {RISE_AT_DAM_KM:g} km of {n_at} of the "
      f"{len(RESERVOIRS)} dams" + (f" ({_dam_labels(rp['at'])})" if n_at else "")
      + f". At those {n_at} the absence is a checked finding, and at the other "
      f"{len(RESERVOIRS) - n_at} the harvest has no RISE series to check. The "
      f"profiles exist, and they move by email: {PROFILE_HOLDERS}.\n")
    w(_outside_facility(st))
    w("\n**Source data errors handled here.** " + _cdec_bad_dates(st)
      + "USGS publishes synthetic records through the portal under invented site "
      "numbers like 123123123123123, named \"ITT test site N\"; they hold no "
      "results, and the harvest drops them.\n")
    cat = pd.read_csv(PROCESSED / "catalog.csv")
    bullets = [
        ("- DWR publishes station `CALWR_WQX-DES WQ 40`, \"Sacramento River @ "
         "Martinez\", at 121.14 W. Martinez is at 122.14 W, so a digit is wrong and the "
         "coordinate lands in the Calaveras subbasin instead of on the Carquinez Strait. "
         "The station is outside `catalog.csv`, because the Calaveras is outside the "
         "study area.\n"),
        _state_well_note(sp, cat, cal),
    ]
    bullets = [b for b in bullets if b]
    w("\n**Source data errors left as published.** This inventory does not silently "
      "correct an upstream coordinate or classification, and these "
      f"{len(bullets)} are worth knowing about.\n\n")
    w("".join(bullets))
    check = _huc8_crosscheck()
    if check and check[0]:
        w(f"\nA cross-check bounds how common a misplaced coordinate is. Of the "
          f"{check[0]:,} portal stations in `data/interim/wqp_stations.gpkg` that "
          "publish their own HUC12 code and fall inside a HUC8 polygon in "
          "`domain.gpkg`, the coordinate falls in a different HUC8 than the published "
          f"one for {check[1]:,} of them.\n")
    return "".join(out)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    DOCS.mkdir(parents=True, exist_ok=True)
    text = build_report()
    (DOCS / "gap_analysis.md").write_text(text)
    log.info("wrote %s", DOCS / "gap_analysis.md")

    st, sp, _ = _load()
    cat = pd.read_csv(PROCESSED / "catalog.csv")
    cal = pd.read_csv(PROCESSED / "catalog_filtered.csv")
    update_readme(harvest_summary(st, sp, cat, cal))


if __name__ == "__main__":
    main()

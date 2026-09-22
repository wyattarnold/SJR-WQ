"""Which stations hold a concurrent record across a stated window.

Coverage runs two questions together, and this module keeps them apart.

The first is whether a parameter has been measured somewhere. The station
inventory settles that one, and it flatters the record: the stations within
500 m of the mainstem that have measured temperature with a continuous monitor
at some point since 1950 sound like a well-observed river.

The second is whether a set of parameters was measured *at the same place over
the same years*, which is what a calibration uses. A gage that ran 1955 to 1970
and a sonde installed in 2019 do not add up to a record, however close together
they sit on the map. `coverage_sets.csv` lists the stations that pass each
rule below.

The window comes from `coverage_window` in config/domain.yml. Two membership
rules, because the two questions need different ones:

    spanning   one merged span covers the entire window, so the record has no
               break longer than `JOIN_DAYS` anywhere inside it. This is the
               calibration rule: a model run over the window needs
               observations from its first day to its last.
    within     one merged span overlaps the window. This is the load rule: a
               facility reporting 2017 to 2026 still supplies nine years of
               measured salt load, and refusing it for starting late throws
               away the return-flow term.

`merged_spans` joins periods of record across sources for the same station and
parameter. A site whose USGS series ends in 2018 and whose CDEC series starts in
2016 has one continuous record across the join, and assembling the series means
splicing the two. Figures 6 and 7 draw their bars from the same function.
"""

from __future__ import annotations

import pandas as pd

from .config import domain_config


def station_level(sp: pd.DataFrame) -> pd.DataFrame:
    """Rows whose period of record belongs to the station rather than a dataset.

    `period_scope` records which one a row holds. EDI is the one source here
    that publishes a wide table keyed by a station column and lists its
    stations separately from its parameters, so a row from one of those tables
    has the package's period of record and `dataset` in that column. `edi.731` has one period
    of record for the whole Delta Integrated Water Quality database, so without
    this filter the conductance record at Vernalis dates from the database's
    first year, decades before its continuous record starts. The gap report
    computes both years in its EDI paragraph.

    The rows stay in the inventory, and out of every table ranked or dated on
    record length.
    """
    if "period_scope" not in sp.columns:
        return sp
    return sp[sp["period_scope"].fillna("station") != "dataset"]


# The three parameters the two balances need between them: a temperature
# balance needs temperature and flow at one place, and a salt balance needs
# conductance and flow.
CORE_PARAMETERS = ("water_temperature", "specific_conductance", "discharge")


def colocated(sp: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Stations measuring all three core parameters, and the continuous subset.

    Returns the station uids with water temperature, specific conductance, and
    discharge in any record type, then the subset recording all three from a
    continuous monitor. Continuous means all three parameters are recorded,
    not that the site is a gage: a site that logs discharge every 15 minutes
    and takes its conductance by hand is in the first set and out of the
    second.
    """
    want = set(CORE_PARAMETERS)
    core = sp[sp["parameter"].isin(want)]
    have = core.groupby("station_uid")["parameter"].agg(set)
    full = {u for u, s in have.items() if s == want}
    trio = (core[core["record_type"] == "continuous"]
            .groupby("station_uid")["parameter"].agg(set))
    return full, {u for u in full if trio.get(u, set()) == want}


def colocated_overlap(sp: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Stations whose three core records share a period, and the continuous subset.

    `colocated` tests presence, and a site that gaged flow until 1990 and
    started measuring temperature in 1998 passes it. The test here compares
    the dates. Each record runs from its earliest start to its latest end at the
    station, and the three share a period where the latest of the three starts
    falls before the earliest of the three ends. The second set applies the
    same test to the rows from a continuous monitor alone.

    Station-scope rows only, through `station_level`, because a dataset-scope
    row holds its package's period of record.
    """
    def shared(rows: pd.DataFrame) -> set[str]:
        d = station_level(rows[rows["parameter"].isin(CORE_PARAMETERS)]).dropna(
            subset=["por_start", "por_end"])
        if d.empty:
            return set()
        g = d.groupby(["station_uid", "parameter"])
        start = g["por_start"].min().unstack().reindex(columns=list(CORE_PARAMETERS))
        end = g["por_end"].max().unstack().reindex(columns=list(CORE_PARAMETERS))
        dated = start.notna().all(axis=1) & end.notna().all(axis=1)
        return set(start.index[dated & (start.max(axis=1) < end.min(axis=1))])

    return shared(sp), shared(sp[sp["record_type"] == "continuous"])


def window(cfg: dict | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The coverage window as timestamps."""
    w = (cfg or domain_config())["coverage_window"]
    return pd.Timestamp(w["start"]), pd.Timestamp(w["end"])


def reporting_since(cfg: dict | None = None) -> pd.Timestamp:
    """The date a record has to reach before it counts as current coverage.

    The window midpoint. "Still reporting" cannot mean "the record touches the
    first day of the window": Merced River at Exchequer stopped in July 2011
    and passed that test, so the reservoir table went on naming it as Lake
    McClure's release-temperature station fifteen years after it went quiet.
    Requiring the record to reach the second half of the window is a low bar
    that the genuinely dead records fail.
    """
    start, end = window(cfg)
    return start + (end - start) / 2


# Two periods of record at one station and parameter closer together than this
# join into one span. Half a year between one agency's last value and the next
# agency's first is a reporting seam rather than a break in the record.
JOIN_DAYS = 180


def merged_spans(starts, ends, join_days: int = JOIN_DAYS) -> list[tuple]:
    """Collapse a station's per-source periods into non-overlapping spans.

    A stretch between two spans has no source reporting a record. Spans less
    than `join_days` apart (180 by default) join into one.
    """
    out: list[list] = []
    for s, e in sorted(zip(starts, ends)):
        if out and s - out[-1][1] <= pd.Timedelta(days=join_days):
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def spans(sp: pd.DataFrame, continuous_only: bool = True) -> pd.DataFrame:
    """One row per merged span, indexed by station and parameter.

    Station-scope rows only, through `station_level`, and merged by
    `merged_spans`, so a station and parameter with a break longer than
    `JOIN_DAYS` has one row on each side of it.

    `continuous_only` keeps the rows from a continuous monitor, which is one
    source reporting that parameter at that station that way rather than
    every source doing so: a station whose temperature comes from a USGS sonde
    and also appears as four portal samples has a continuous temperature
    record, and the union of the two periods is what a modeller can assemble.
    """
    d = station_level(sp).dropna(subset=["por_start", "por_end"])
    if continuous_only:
        d = d[d["record_type"] == "continuous"]
    rows = [(uid, param, s, e)
            for (uid, param), g in d.groupby(["station_uid", "parameter"])
            for s, e in merged_spans(g["por_start"], g["por_end"])]
    return (pd.DataFrame(rows, columns=["station_uid", "parameter", "start", "end"])
              .set_index(["station_uid", "parameter"]))


def stations_with(sp: pd.DataFrame, parameters, start=None, end=None,
                  continuous: bool = True, rule: str = "spanning",
                  windowed: pd.DataFrame | None = None) -> set[str]:
    """Station uids holding every named parameter across the window.

    `rule` is "spanning" or "within"; see the module docstring. `continuous`
    restricts to what a continuous monitor produced. `windowed` takes the
    output of `spans` for a caller testing many windows against one `sp`.
    """
    if start is None or end is None:
        w_start, w_end = window()
        start = w_start if start is None else start
        end = w_end if end is None else end
    if windowed is None:
        windowed = spans(sp, continuous_only=continuous)
    if rule == "spanning":
        keep = windowed[(windowed["start"] <= start)
                        & (windowed["end"] >= end)]
    elif rule == "within":
        keep = windowed[(windowed["start"] < end)
                        & (windowed["end"] > start)]
    else:
        raise ValueError(f"rule must be 'spanning' or 'within', got {rule!r}")

    want = set(parameters)
    have: dict[str, set] = {}
    for uid, param in keep.index:
        have.setdefault(uid, set()).add(param)
    return {uid for uid, ps in have.items() if want <= ps}


# The concurrent-coverage rules, defined once. Figures 12 to 14 draw them and
# inventory.py writes coverage_sets.csv from them, so a changed rule reaches
# the maps and the table in the same run. `salt_load_recorded` is the strict
# subset drawn as circles inside the salt-load figure rather than a figure of
# its own.
COVERAGE_SETS: dict[str, dict] = {
    "temp_flow": {
        "figure": 12,
        "parameters": ("water_temperature", "discharge"),
        "rule": "spanning",
        "continuous": True,
    },
    "temp_ec_flow": {
        "figure": 13,
        "parameters": ("water_temperature", "specific_conductance", "discharge"),
        "rule": "spanning",
        "continuous": True,
    },
    "salt_load": {
        "figure": 14,
        "parameters": ("specific_conductance", "discharge"),
        "rule": "within",
        "continuous": False,
    },
    "salt_load_recorded": {
        "figure": 14,
        "parameters": ("specific_conductance", "discharge"),
        "rule": "spanning",
        "continuous": True,
    },
}


def members(sp: pd.DataFrame, key: str) -> set[str]:
    """Station uids meeting one named rule from `COVERAGE_SETS`."""
    spec = COVERAGE_SETS[key]
    return stations_with(sp, spec["parameters"], rule=spec["rule"],
                         continuous=spec["continuous"])


# Years the tradeoff curve runs over. It starts before most concurrent records
# begin, so the curve flattens at its left end and a reader sees where reaching
# further back stops removing stations.
TRADEOFF_START_YEARS = range(1990, 2024)


def window_tradeoff(sp: pd.DataFrame, end: pd.Timestamp | None = None,
                    years=None) -> pd.DataFrame:
    """Stations against window length, for every start year in `years`.

    The coverage window trades one against the other. A window has to be short
    enough that stations hold a continuous record across all of it and long
    enough to cover the flow regime the model is calibrated over, and the two
    pull opposite ways: each year added at the start removes the stations
    whose continuous record begins or breaks inside that year.

    `station_years` is the product, which is the concurrent record the window
    makes available. It peaks where the two effects balance, and that peak is
    the argument for a window rather than a preference for one.

    The spanning rules only. A "within" rule counts a record that touches the
    window, so lengthening the window adds stations instead of removing them
    and the tradeoff the figure draws runs backwards.

    One row per start year and rule, with the end date fixed at the configured
    window end.
    """
    end = pd.Timestamp(end) if end is not None else window()[1]
    merged = {c: spans(sp, continuous_only=c)
              for c in {s["continuous"] for s in COVERAGE_SETS.values()}}
    rows = []
    for year in (years or TRADEOFF_START_YEARS):
        start = pd.Timestamp(f"{year}-01-01")
        length = (end - start).days / 365.25
        for key, spec in COVERAGE_SETS.items():
            if spec["rule"] != "spanning":
                continue
            n = len(stations_with(sp, spec["parameters"], start, end,
                                  continuous=spec["continuous"],
                                  rule=spec["rule"],
                                  windowed=merged[spec["continuous"]]))
            rows.append({"rule": key, "start_year": year,
                         "window_years": round(length, 1), "n_stations": n,
                         "station_years": round(n * length, 1)})
    return pd.DataFrame(rows)


def coverage_table(cat: pd.DataFrame, sp: pd.DataFrame) -> pd.DataFrame:
    """One row per station meeting at least one rule, a column per rule.

    Figures 12 to 14 draw these sets and name none of the stations in them.
    A modeller deciding what to download needs the names, so the same sets go
    to `data/processed/coverage_sets.csv`.
    """
    sets = {k: members(sp, k) for k in COVERAGE_SETS}
    keep = set().union(*sets.values())
    cols = ["station_uid", "name", "sources", "operator", "channel_class",
            "record_type", "frequency", "lat", "lon", "river", "mainstem_km",
            "huc8_name", "por_start", "por_end", "active", "url"]
    out = cat[cat["station_uid"].isin(keep)][
        [c for c in cols if c in cat.columns]].copy()
    for key, ids in sets.items():
        out[key] = out["station_uid"].isin(ids)

    named = [k for k, v in COVERAGE_SETS.items() if k != "salt_load_recorded"]
    out["n_sets"] = out[named].sum(axis=1)
    out["figures"] = out.apply(
        lambda r: ", ".join(str(COVERAGE_SETS[k]["figure"])
                            for k in named if r[k]), axis=1)
    out["window"] = label()
    out["off_mainstem"] = out["mainstem_km"].isna()
    return (out.sort_values(["off_mainstem", "mainstem_km", "name"])
               .drop(columns="off_mainstem")
               .reset_index(drop=True))


def label(start=None, end=None) -> str:
    """The window as the years it covers.

    The window ends 2026-01-01, which is the first instant outside it, so
    naming the end year would claim a year of coverage the window does not
    have. The last covered year is the day before.
    """
    if start is None or end is None:
        start, end = window()
    last = pd.Timestamp(end) - pd.Timedelta(days=1)
    return f"{start:%Y}-{last:%Y}"

"""Decide what kind of water body a station sits on.

The inventory already records a station type (streamgage, grab site, well),
but that records the instrument rather than what it measures. For a salt
balance the second question matters more. Cal Aqueduct Check 13 and San Joaquin at
Vernalis are both continuous stations with a long record; one measures Delta
water pumped south into the basin and the other measures the basin draining
north. Averaged together they are meaningless.

Seven classes come out of this:

    natural          river, creek, slough, flood bypass
    conveyance       aqueduct, canal, pumping plant, forebay, powerhouse
    drain            tile drain, agricultural drain, sump
    effluent         permitted outfall, influent, in-plant
    receiving_water  the river immediately up or downstream of an outfall,
                     sampled under the discharger's own permit
    reservoir        lake or impoundment
    groundwater      well

`receiving_water` is a natural channel and stays a separate class, because the
sampling is a permit obligation rather than a monitoring program: it runs at
the interval the permit sets, usually monthly, at a point chosen to bracket an
outfall rather than to represent a reach.

Conveyance and drain matter most, and no other step in the pipeline separates
them. Conveyance is an import term: the Delta-Mendota Canal and the California
Aqueduct bring water and salt into the basin from outside. Drains are the
return term, and on the west side they move the selenium and boron signal the
Grassland Bypass Project exists to track. This module keeps and labels both,
and neither is a river observation.
"""

from __future__ import annotations

import re

import pandas as pd

# Agency names put the water body first and the location second, separated by a
# preposition. Splitting there keeps "SAN CARLOS C BL NEW IDRIA MINE DRAIN" out
# of the drain class: the feature is a creek, and the drain is where the creek
# is measured from.
_LOCATOR = re.compile(
    r"\s(?:a|at|ab|abv|above|bl|blo|blw|bel|below|nr|near|@|d/s|u/s"
    r"|upstream\s+of|downstream\s+of|upstream|upstrm|downstream|dnstrm)\s",
    re.IGNORECASE)

# A Public Land Survey well number, such as 05N08E26P001M. DWR publishes 108
# stations named this way and types them "Other-Surface Water", which the gap
# analysis records as a source error left as published. Classifying them here
# does not rewrite what DWR published; it adds this inventory's own reading.
STATE_WELL_NUMBER = re.compile(r"^\d{2}[NS]\d{2}[EW]\d{2}[A-Z]\d{3}[A-Z]?$")

_PATTERNS: list[tuple[str, str]] = [
    ("groundwater", r"\bwells?\b|\bmw[-\s]|\bpz[-\s]|piezomet"),
    # USGS abbreviates Drain as DR, which is how the San Luis Drain sites are
    # named. Nothing in this basin is a "Drive".
    ("drain", r"\bdrains?\b|\bsumps?\b|\btile\b|\bdrn\b|\bdr\b"),
    # A spill is the point where irrigation water leaves a canal for a river.
    # MID and TID name several by that word alone (Spenker Spill, Waterford
    # L.M. Spill), so the conveyance pattern matches it without a canal or
    # lateral beside it. The word boundary keeps "spillway" a separate term,
    # and the lookahead keeps out CDEC's "FARMINGTON (SPILL ELEV 156.5 FT)",
    # where the word names a dam's spill elevation.
    ("conveyance", (
        r"\baqueducts?\b|\bcanals?\b|\bcn\b|\bdmc\b|delta[\s-]*mendota"
        r"|\bchecks?\s*\d|pumping\s*plant|\bforebay\b|\bafterbay\b"
        r"|\bconduits?\b|\bpipelines?\b|\bflumes?\b|\bditch\b"
        r"|\blaterals?\b|\bsiphons?\b|\btunnels?\b|\bpenstocks?\b"
        r"|powerhouse|\bp\.?h\.?\b|\bintakes?\b|headworks|wasteway"
        r"|\bturnouts?\b|\bspills?\b(?!\s+elev)|\bspillway\b|\bdiversions?\b"
        r"|\bdiv\b"
        r"|\bpp\b|\btu\b|\bpump\b")),
    ("reservoir", (
        r"\breservoirs?\b|\bres\b|\blakes?\b|\blk\b|\bpool\b|\bdam\b"
        r"|\bimpoundment\b")),
    ("effluent", (
        r"\boutfalls?\b|\beffluent\b|\binfluent\b|\bwwtp\b|\bwwtf\b"
        r"|\bwqcf\b|\bwrf\b|\bwrp\b|\bwrcf\b|treatment\s*plant"
        r"|\bpotw\b|quality\s*control\s*facility")),
    # "cut" is a flood-relief channel rather than an excavation: Paradise Cut
    # and Doughty Cut both move San Joaquin water, and without this word they
    # fall through to unknown and out of the mainstem gap tables.
    ("natural", (
        r"\brivers?\b|\br\b|\bsjr\b|\bcreeks?\b|\bc\b|\bck\b|\bcr\b|\bsloughs?\b"
        r"|\bsl\b|\bforks?\b|\bbypass\b|\bbranch\b|\bbrook\b|\bstream\b"
        r"|\bgulch\b|\bwash\b|\bbayou\b|\bcut\b")),
]

_COMPILED = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in _PATTERNS]

# An eSMR monitoring location code: EFF-001, RSW-002, M-INF, R-001D2, INF-001.
# These sit at the end of a facility name and are the only thing distinguishing
# one monitoring point at a plant from another.
#
# They have to be recognized before the name patterns run, because the natural
# pattern below matches a bare "r" as an abbreviation for River and the "R" in
# "R-001" is not one. That is how "El Portal WWTF R-001" came out as a natural
# channel and "RSW-001" as unknown, when both are receiving-water stations at a
# wastewater plant.
ESMR_CODE = re.compile(
    r"\b(?P<kind>EFF|INF|RSW|REC|M|R|D|LND|SW|GW|INT)"
    r"-?(?:\d{2,3}[A-Z]?\d?|INF)\b", re.IGNORECASE)

# What each code means as a channel. RSW and R are the receiving water itself,
# so the plant discharges to them rather than through them; they are the class
# a load model treats as a river station immediately up or downstream of an
# outfall. Everything else is inside the plant.
_ESMR_CODE_CLASS = {
    "EFF": "effluent", "INF": "effluent", "INT": "effluent",
    "M": "effluent", "LND": "effluent",
    "RSW": "receiving_water", "R": "receiving_water", "SW": "receiving_water",
    "REC": "effluent", "D": "effluent",
    "GW": "groundwater",
}

# Station types that settle the question on their own, regardless of the name.
_TYPE_CLASS = {
    "groundwater_well": "groundwater",
    "spring": "natural",
    "estuary": "natural",
    "effluent": "effluent",
    "influent": "effluent",
    "internal": "effluent",
    "receiving_water": "receiving_water",
    "land_discharge": "effluent",
    "stormwater": "effluent",
    "reservoir": "reservoir",
    "streamgage": None,
}

# WQP publishes its own location type, which is more reliable than a name for
# the canal cases because it is a controlled vocabulary. A type maps to None
# where agencies apply it to canals and drains as well as rivers, so the name
# decides. USGS files a test hole and a hyporheic-zone well under groundwater,
# and WQP records the hyporheic zone as the sample medium at the 13 test holes
# on the San Joaquin, so both map there despite names such as "HYPORHEIC X-SEC
# IN SAN JOAQUIN R". The harvest returns five more types that have no entry:
# Atmosphere, Unsaturated zone, and Wetland Palustrine-Shrub-Scrub fit none of
# the seven classes, a Collector or Ranney type well can be a drainage sump,
# and an effluent-dominated stream can be a drain or a receiving water.
_LOCATION_TYPE_CLASS = {
    "Canal Drainage": "drain",
    "Canal Irrigation": "conveyance",
    "Canal Transport": "conveyance",
    "Groundwater drain": "drain",
    "Well": "groundwater",
    "Hyporheic-zone well": "groundwater",
    "Test hole not completed as a well": "groundwater",
    "Lake": "reservoir",
    "Reservoir": "reservoir",
    "Lake, Reservoir, Impoundment": "reservoir",
    "Facility Municipal Sewage (POTW)": "effluent",
    "Facility Other": "effluent",
    "River/Stream": None,
    "Stream": None,
    "Other-Surface Water": None,
    "River/Stream Intermittent": "natural",
    "River/Stream Perennial": "natural",
    "Spring": "natural",
    "Estuary": "natural",
}


def _head(name: object) -> str:
    """The part of a station name before the first location preposition."""
    s = str(name or "").strip()
    parts = _LOCATOR.split(s, maxsplit=1)
    return parts[0] if parts and parts[0] else s


def channel_class(name: object, station_type: object = None,
                  location_type: object = None) -> str:
    """Classify one station. Controlled vocabularies win over names."""
    if isinstance(location_type, str):
        forced = _LOCATION_TYPE_CLASS.get(location_type, "unset")
        if forced is not None and forced != "unset":
            return forced
    if isinstance(station_type, str):
        forced = _TYPE_CLASS.get(station_type, "unset")
        if forced is not None and forced != "unset":
            return forced

    if STATE_WELL_NUMBER.match(str(name or "").strip()):
        return "groundwater"

    # An eSMR location code at the end of a facility name settles it, and has
    # to be tested before the word patterns for the reason given at ESMR_CODE.
    m = ESMR_CODE.search(str(name or ""))
    if m:
        cls = _ESMR_CODE_CLASS.get(m.group("kind").upper())
        if cls:
            return cls

    head = _head(name)
    for cls, pat in _COMPILED:
        if pat.search(head):
            return cls
    # No match in the head, so fall back to the whole string before giving up.
    # "O'NEILL FOREBAY @ GIANELLI PUMPING PLANT" splits on the @ and still
    # matches, but plenty of names put the useful word further along.
    whole = str(name or "")
    for cls, pat in _COMPILED:
        if pat.search(whole):
            return cls
    return "unknown"


def add_channel_class(stations: pd.DataFrame) -> pd.DataFrame:
    """Add a `channel_class` column to a station table."""
    out = stations.copy()
    loc = out["location_type"] if "location_type" in out.columns else pd.Series(
        [None] * len(out), index=out.index)
    typ = out["station_type"] if "station_type" in out.columns else pd.Series(
        [None] * len(out), index=out.index)
    out["channel_class"] = [
        channel_class(n, t, lt)
        for n, t, lt in zip(out["name"], typ, loc, strict=True)
    ]
    return out


# --------------------------------------------------------------------------
# How a record was made, and how often
# --------------------------------------------------------------------------
#
# Two questions, two columns, in the words USGS uses for them.
#
#   record_type   `continuous` for a value an instrument logged unattended,
#                 `discrete` for a sample somebody collected. This is what the
#                 source states about the instrument.
#   frequency     how often a value lands, from the interval the source states
#                 or, where it states none, from its own sample count over its
#                 own period of record.
#
# A monthly conductance sample is discrete at a known frequency, which is worth
# more to a salt balance than the schema could say while one column held both
# answers.

# Frequencies a continuous monitor produces. Anything coarser is somebody
# collecting a sample, whatever the record_type column says.
CONTINUOUS_FREQUENCIES = {"sub-daily", "daily"}

# Upper bound in days for each frequency label, coarsest test last. Three
# sources publish a sample count and a period of record but no stated interval,
# so their frequency comes from count over span. One copy of the thresholds,
# here.
#
# The bands stay loose. A daily monitor that lost a fortnight to a failed
# logger still averages under two days, and a monthly program that skipped a
# summer still averages under forty-five.
FREQUENCY_BANDS: list[tuple[float, str]] = [
    (0.9, "sub-daily"), (2.0, "daily"), (10.0, "weekly"),
    (45.0, "monthly"), (120.0, "quarterly"), (400.0, "annual"),
]


# The same bands without the sub-daily one, for a source that publishes a
# record count rather than the sample dates. A count includes the replicates
# and the depth profile taken on one visit, so dividing it into the span puts a
# quarterly program that runs three samples a visit under a day. Daily is the
# finest honest band a bare count supports. CEDEN and eSMR both use these; the
# portal publishes every sample date and collapses duplicates before measuring,
# so it uses the full list.
SAMPLE_COUNT_BANDS: list[tuple[float, str]] = [
    (2.0, "daily"), (10.0, "weekly"), (45.0, "monthly"),
    (120.0, "quarterly"), (400.0, "annual"),
]


# Order of the frequency vocabulary, finest first. Used to pick one label for
# a station from its per-parameter rows, and to pick the fastest series where
# one station is reported by a gage and by the portal's samples both.
FREQUENCY_RANK = {"sub-daily": 0, "daily": 1, "weekly": 2, "monthly": 3,
                  "quarterly": 4, "annual": 5, "irregular": 6, "unknown": 7}


def frequency_from_interval(days: object,
                            bands: list[tuple[float, str]] | None = None) -> str:
    """Label a reporting interval, in days, with a frequency.

    Anything past the last band is `irregular`: a series averaging more than a
    year between samples has a frequency on paper rather than in practice. A
    null interval is `unknown`, because a single sample and an unreported count
    are different things.
    """
    if days is None or pd.isna(days):
        return "unknown"
    for limit, label in (bands or FREQUENCY_BANDS):
        if float(days) <= limit:
            return label
    return "irregular"


def measured_frequency(start: pd.Series, end: pd.Series, n_obs: pd.Series,
                       bands: list[tuple[float, str]] | None = None
                       ) -> tuple[pd.Series, pd.Series]:
    """Reporting interval and frequency from a count over a span.

    CEDEN and eSMR publish an observation count and a period of record and no
    interval, so the ratio of the two is what they know about frequency. A
    series with one observation has no interval, and one whose start and end
    fall on the same day has no span, so both come out `unknown`, which is the
    honest answer for a single sample.
    """
    span = (pd.to_datetime(end) - pd.to_datetime(start)).dt.days
    n = pd.Series(n_obs).astype("Float64")
    interval = (span.where(span > 0) / n.where(n > 1)).astype("Float64").round(2)
    freq = pd.Series([frequency_from_interval(v, bands) for v in interval],
                     index=interval.index, dtype=object)
    return interval, freq


def station_type_from_frequency(record_type: object, frequency: object,
                                channel: object) -> str | None:
    """Backfill a station type for sources that publish none.

    CDEC publishes none. All 251 of its stations arrive with the field empty,
    which drops 48 hourly-reporting stations from the continuous shortlist. The
    duration code CDEC does publish settles the type.
    """
    if record_type == "continuous":
        if channel == "reservoir":
            return "reservoir"
        return "continuous_sonde" if frequency == "sub-daily" else "daily_summary"
    if frequency in ("monthly", "quarterly", "annual"):
        return f"{frequency}_summary"
    if frequency in ("weekly", "irregular", "unknown"):
        return "discrete_grab"
    return None


def refine_with_parameters(stations: pd.DataFrame,
                          sp: pd.DataFrame) -> pd.DataFrame:
    """Settle a few unknowns using what the station actually measures.

    CDEC names its rim-dam stations after the reservoir alone, so a pattern
    finds no word to match in Strawberry, Donnells, or Hetch Hetchy. Each one
    does report a reservoir storage sensor, which a river station does not.
    """
    out = stations.copy()
    storage = set(sp.loc[sp["parameter"] == "reservoir_storage", "station_uid"])
    fix = (out["channel_class"] == "unknown") & out["station_uid"].isin(storage)
    out.loc[fix, "channel_class"] = "reservoir"
    return out


# --------------------------------------------------------------------------
# What a conveyance is for
# --------------------------------------------------------------------------
#
# The `conveyance` class covers four roles, and one of them brings foreign
# water into the basin:
#
#   import     Delta water pumped south. The Delta-Mendota Canal and the
#              California Aqueduct, with San Luis, O'Neill Forebay, and the
#              Pacheco and Gianelli plants that move water between them. The
#              DMC's delivery to Mendota Pool is the largest single inflow to
#              the modeled reach and sits on the mainstem at km 213.
#   export     basin water leaving. The Friant-Kern Canal takes San Joaquin
#              water south out of the basin at Millerton.
#   diversion  basin water moved within the basin. Turlock Canal is TID's
#              diversion from the Tuolumne at La Grange; it is Tuolumne water
#              and stays in the Tuolumne's own subbasin. So are the MID and
#              TID laterals and the Madera Canal.
#   return     irrigation water going back to a river. The spills, wasteways,
#              and lateral outlets. These are return-flow terms and belong
#              beside the agricultural drains, not beside the aqueduct.
#
# A salt balance treats the four differently: an import adds salt from
# outside, an export removes it, a diversion moves it within, and a return
# brings it back concentrated.

_IMPORT = re.compile(
    r"delta[\s-]*mendota|\bdmc\b|california\s+aqueduct|\bcal\s+aqueduct\b"
    r"|\bca\s+aqueduct\b|san\s+luis\s+(?:canal|reservoir)|o.?neill"
    r"|pacheco\s+pumping|gianelli|\bcheck\s*\d+", re.IGNORECASE)
_EXPORT = re.compile(r"friant[\s-]*kern", re.IGNORECASE)
# A dam's spillway and a power tunnel's outlet release reservoir water rather
# than irrigation water. The return pattern matches "spill" but not
# "spillway", and matches "outlet" only after "lateral", so TULLOCH DAM
# SPILLWAY, Spillway Outlet, and STANISLAUS TU A OUTLET CA fall through to
# diversion.
_RETURN = re.compile(
    r"\bspills?\b|\bwasteway\b|\blateral\b.*\boutlet\b|\btailwater\b"
    r"|\breturn\b",
    re.IGNORECASE)


def conveyance_role(name: object, channel: object = None) -> str | None:
    """Import, export, diversion, or return. None for a feature that is not a canal.

    Order matters. "Delta Mendota Canal to Mendota Pool" is a delivery rather
    than a spill, so imports test before returns; "Lateral 6 Spill" has no
    import word and falls through to return.
    """
    if channel is not None and channel != "conveyance":
        return None
    text = str(name or "")
    if _EXPORT.search(text):
        return "export"
    if _IMPORT.search(text):
        return "import"
    if _RETURN.search(text):
        return "return"
    return "diversion"


def add_conveyance_role(stations: pd.DataFrame) -> pd.DataFrame:
    """Add a `conveyance_role` column, null for everything but a conveyance."""
    out = stations.copy()
    out["conveyance_role"] = [
        conveyance_role(n, c)
        for n, c in zip(out["name"], out["channel_class"], strict=True)
    ]
    return out

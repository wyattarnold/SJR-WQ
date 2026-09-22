"""Resolve source unit strings against the canonical unit in parameters.yml.

This module converts no measurement, because the inventory stores periods of
record and counts rather than values. It decides, for each station-parameter
row, whether the source unit converts to the canonical one and by what factor.
A later time-series pull takes `unit_factor` and `unit_offset` straight off the
row rather than rediscovering that CDEC sensor 25 is Fahrenheit.

Three kinds of answer come out of this:

  convertible      a scale and offset get you to the canonical unit
  wrong quantity   the unit measures something else and no factor exists
  unknown          the string is not in the table

The middle case is the interesting one. Suspended solids and ammonia arrive in
`lb/day` at permitted outfalls, which is a mass per unit time rather than a
concentration. Total dissolved solids arrives in `tons/ac ft`, which is a load
per unit volume. Water temperature arrives in `degree`, which names no scale.
Each looks like an ordinary number to a pull that trusted the parameter name.
`docs/gap_analysis.md` counts every one of them under "Units that do not match
the quantity", computed on each run.

Percent is the one string that means two things. It is the quantity for
dissolved oxygen saturation and a missing concentration everywhere else, so the
dimension of the canonical unit decides which reading applies.

A reporting basis can hide behind one unit string too. eSMR publishes nitrate
in mg/L both as N and as NO3, a factor of 4.4 apart, and puts the basis in the
parameter name, so for those rows the factor comes from the name.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd

# Conversion is (value * factor) + offset. Only Fahrenheit needs the offset,
# and leaving it out makes temperature a special case in every caller.
F_FACTOR = 5.0 / 9.0
F_OFFSET = -160.0 / 9.0

# Short ton per acre-foot to mg/L. 907,184.74 g over 1,233,481.84 L. This is the
# traditional California unit for total dissolved solids and it is a genuine
# concentration, unlike tons/day on the next line down.
TONS_PER_ACRE_FT = 735.47

# Conversions grouped by the dimension of the canonical unit. The key is a
# normalized unit string; see normalize().
BY_DIMENSION: dict[str, dict[str, tuple[float, float]]] = {
    "temperature": {
        "deg c": (1.0, 0.0), "degc": (1.0, 0.0), "degrees c": (1.0, 0.0),
        "c": (1.0, 0.0), "celsius": (1.0, 0.0), "deg cel": (1.0, 0.0),
        "deg f": (F_FACTOR, F_OFFSET), "degf": (F_FACTOR, F_OFFSET),
        "degrees f": (F_FACTOR, F_OFFSET), "f": (F_FACTOR, F_OFFSET),
        "fahrenheit": (F_FACTOR, F_OFFSET),
        # EML spells units out in full, one word, from the STMML dictionary.
        "degreecelsius": (1.0, 0.0), "degreec": (1.0, 0.0),
        "degreefahrenheit": (F_FACTOR, F_OFFSET),
        "degreesc": (1.0, 0.0), "degreesf": (F_FACTOR, F_OFFSET),
    },
    "conductivity": {
        "us/cm": (1.0, 0.0), "umho/cm": (1.0, 0.0), "umhos/cm": (1.0, 0.0),
        "us/cm @25c": (1.0, 0.0), "umho/cm @25c": (1.0, 0.0),
        "ms/cm": (1000.0, 0.0), "mmho/cm": (1000.0, 0.0), "mmhos/cm": (1000.0, 0.0),
        "microsiemenspercentimeter": (1.0, 0.0),
        "millisiemenspercentimeter": (1000.0, 0.0),
        "microsiemenspercentimeterat25degreescelsius": (1.0, 0.0),
        # Two EDI packages spell it "Seimen". Left as published and matched
        # here, because correcting a source string is not this module's job.
        "microseimenpercentimeter": (1.0, 0.0),
        "microseimenspercentimeter": (1.0, 0.0),
    },
    "mass_conc": {
        "mg/l": (1.0, 0.0), "ppm": (1.0, 0.0), "g/m3": (1.0, 0.0),
        "mg/l caco3": (1.0, 0.0), "mg/l as caco3": (1.0, 0.0),
        "ug/l": (1e-3, 0.0), "ppb": (1e-3, 0.0),
        "ng/l": (1e-6, 0.0),
        "tons/ac ft": (TONS_PER_ACRE_FT, 0.0),
        "tons/acre ft": (TONS_PER_ACRE_FT, 0.0),
        "ton/acre-ft": (TONS_PER_ACRE_FT, 0.0),
        "milligramperliter": (1.0, 0.0), "milligramsperliter": (1.0, 0.0),
        "microgramperliter": (1e-3, 0.0), "microgramsperliter": (1e-3, 0.0),
        "gramperliter": (1e3, 0.0), "partspermillion": (1.0, 0.0),
        "partsperbillion": (1e-3, 0.0),
    },
    "trace_conc": {
        "ug/l": (1.0, 0.0), "ppb": (1.0, 0.0),
        "mg/l": (1e3, 0.0), "ppm": (1e3, 0.0),
        "ng/l": (1e-3, 0.0),
        "microgramperliter": (1.0, 0.0), "microgramsperliter": (1.0, 0.0),
        "milligramperliter": (1e3, 0.0), "milligramsperliter": (1e3, 0.0),
        "partsperbillion": (1.0, 0.0), "partspermillion": (1e3, 0.0),
    },
    "flow": {
        "ft^3/s": (1.0, 0.0), "ft3/sec": (1.0, 0.0), "ft3/s": (1.0, 0.0),
        "cfs": (1.0, 0.0), "cfs.xx": (1.0, 0.0), "cu ft/sec": (1.0, 0.0),
        "m3/sec": (35.3147, 0.0), "m^3/s": (35.3147, 0.0), "cms": (35.3147, 0.0),
        "mgd": (1.54723, 0.0), "gpd": (1.54723e-6, 0.0), "gpm": (2.22801e-3, 0.0),
        "cubicfeetpersecond": (1.0, 0.0), "cubicfootpersecond": (1.0, 0.0),
        "cubic feet per second": (1.0, 0.0),
        "cubicmeterpersecond": (35.3147, 0.0), "cubicmeterspersecond": (35.3147, 0.0),
    },
    "length": {
        "ft": (1.0, 0.0), "feet": (1.0, 0.0),
        "m": (3.28084, 0.0), "meters": (3.28084, 0.0),
        "in": (1.0 / 12.0, 0.0), "inches": (1.0 / 12.0, 0.0),
        "meter": (3.28084, 0.0), "foot": (1.0, 0.0),
    },
    "volume": {
        "af": (1.0, 0.0), "acre-ft": (1.0, 0.0), "acre-feet": (1.0, 0.0),
        "ac-ft": (1.0, 0.0), "taf": (1000.0, 0.0),
    },
    "turbidity": {
        "ntu": (1.0, 0.0), "ntru": (1.0, 0.0), "fnu": (1.0, 0.0),
        "nephelometricturbidityunit": (1.0, 0.0),
        "nephelometricturbidityunits": (1.0, 0.0),
        "nephelometric turbidity units": (1.0, 0.0),
        "formazinnephelometricunit": (1.0, 0.0),
        "formazinnephelometricunits": (1.0, 0.0),
    },
    # pH has no unit, so every spelling of "no unit" belongs here. CEDEN
    # writes the string "none" on 505 rows and "units" on six more.
    "ph": {
        "su": (1.0, 0.0), "standard units": (1.0, 0.0), "ph": (1.0, 0.0),
        "ph units": (1.0, 0.0), "": (1.0, 0.0), "none": (1.0, 0.0),
        "units": (1.0, 0.0), "unitless": (1.0, 0.0), "na": (1.0, 0.0),
        "n/a": (1.0, 0.0), "std units": (1.0, 0.0), "pH units": (1.0, 0.0),
        "dimensionless": (1.0, 0.0), "number": (1.0, 0.0),
        "phunit": (1.0, 0.0), "phunits": (1.0, 0.0),
    },
    "psu": {
        "psu": (1.0, 0.0), "ppt": (1.0, 0.0), "practical salinity units": (1.0, 0.0),
        # Practical salinity is a ratio, so EML records it as dimensionless.
        "dimensionless": (1.0, 0.0),
    },
    # Percent of saturation. The one parameter with this dimension is dissolved
    # oxygen saturation, where percent is the quantity rather than a sign that
    # a concentration went missing.
    "fraction": {
        "%": (1.0, 0.0), "percent": (1.0, 0.0), "pct": (1.0, 0.0),
        "% saturation": (1.0, 0.0), "percent saturation": (1.0, 0.0),
        "%saturatn": (1.0, 0.0),
    },
}

# Units that measure a different quantity than the parameter claims. Checked
# before the dimension table so the reason survives into the output rather than
# collapsing into "unknown".
WRONG_QUANTITY: dict[str, str] = {
    "%": "percent of total or of saturation, not a concentration",
    "percent": "percent of total or of saturation, not a concentration",
    "lb/day": "mass load per unit time, not a concentration",
    "kg/day": "mass load per unit time, not a concentration",
    "tons/day": "mass load per unit time, not a concentration",
    "ug/cm2": "areal, a benthic measurement rather than a water column one",
    "fluoro": "relative fluorescence, uncalibrated",
    "ng/g": "dry-weight basis, a tissue or sediment sample rather than water",
    "mg/kg": "dry-weight basis, a tissue or sediment sample rather than water",
    "mv": "electrode potential, not a pH reading",
}

# Molar mass used to convert umol/L, ueq/L, and meq/L into the canonical mass
# unit, and the ionic charge used for the equivalent forms. Keyed by canonical
# parameter, because the reporting basis differs: nitrate arrives as N, so the
# mass to use is nitrogen's rather than the whole nitrate ion's. Total nitrogen
# is a sum of several species and phosphate's charge varies with pH, so neither
# has a charge here, and an equivalent unit on either stays unconverted.
MOLAR: dict[str, tuple[float, int | None]] = {
    "chloride": (35.453, 1),
    "sulfate": (96.06, 2),
    "sodium": (22.9898, 1),
    "potassium": (39.0983, 1),
    "calcium": (40.078, 2),
    "magnesium": (24.305, 2),
    "bromide": (79.904, 1),
    "nitrate": (14.007, 1),          # canonical is mg/L as N
    "ammonia": (14.007, 1),          # canonical is mg/L as N
    "total_nitrogen": (14.007, None),
    "phosphorus": (30.974, None),    # canonical is mg/L as P
    "alkalinity": (50.04, 1),        # canonical is mg/L as CaCO3, 50.04 g/eq
    "hardness": (50.04, 1),
}

# Which conversion table applies to each canonical unit string in parameters.yml.
DIMENSION_OF_CANONICAL: dict[str, str] = {
    "degc": "temperature",
    "us/cm @25c": "conductivity",
    "mg/l": "mass_conc",
    "mg/l as caco3": "mass_conc",
    "mg/l as n": "mass_conc",
    "mg/l as p": "mass_conc",
    "ug/l": "trace_conc",
    "cfs": "flow",
    "ft": "length",
    "acre-ft": "volume",
    "ntu": "turbidity",
    "standard units": "ph",
    "psu": "psu",
    "%": "fraction",
}

# Plain-language name for each dimension, used when a unit turns out to belong
# to a different one than the parameter expects.
DIMENSION_LABEL = {
    "temperature": "temperature", "conductivity": "conductivity",
    "mass_conc": "concentration", "trace_conc": "trace concentration",
    "flow": "flow rate", "length": "length", "volume": "volume",
    "turbidity": "turbidity", "ph": "pH", "psu": "salinity",
    "fraction": "percent",
}

# Nitrogen-basis strings. WQP writes these with trailing asterisks, which
# normalize() already strips, and they match the canonical "mg/L as N" basis, so
# they convert one-to-one rather than counting as a mismatch.
NITROGEN_BASIS = {"mg n/l", "mg n/l as n", "mg/l as n"}

# Unit strings that include a reporting basis. The basis in the unit string
# applies to these rows, and restate_basis() skips them.
UNIT_STATES_BASIS = NITROGEN_BASIS | {"mg/l caco3", "mg/l as caco3"}

# Atomic weights, for moving a mass concentration between reporting bases.
_H, _C, _N, _O, _S, _CA = 1.00794, 12.0107, 14.0067, 15.9994, 32.065, 40.078
_P, _MG = 30.973762, 24.305

# Formula weight of each reporting basis a source names, per unit of what the
# basis counts: a mole of nitrogen, phosphorus, or sulfur, or an equivalent of
# alkalinity or hardness. CaCO3, CO3, Ca, and Mg carry two equivalents a mole. Two bases convert
# only where they count the same thing, and the factor is the ratio of their
# weights: nitrate as NO3 to nitrate as N is 14.0067 / 62.0049.
BASIS_WEIGHT: dict[str, tuple[str, float]] = {
    "N": ("nitrogen", _N),
    "NO3": ("nitrogen", _N + 3 * _O),
    "NH3": ("nitrogen", _N + 3 * _H),
    "NH4": ("nitrogen", _N + 4 * _H),
    "P": ("phosphorus", _P),
    "PO4": ("phosphorus", _P + 4 * _O),
    "S": ("sulfur", _S),
    "SO4": ("sulfur", _S + 4 * _O),
    "CaCO3": ("equivalent", (_CA + _C + 3 * _O) / 2),
    "CO3": ("equivalent", (_C + 3 * _O) / 2),
    "HCO3": ("equivalent", _H + _C + 3 * _O),
    "OH": ("equivalent", _O + _H),
    "Ca": ("equivalent", _CA / 2),
    "Mg": ("equivalent", _MG / 2),
}
_BASIS_KEY = {k.lower(): k for k in BASIS_WEIGHT}

# The basis of a canonical unit that names none. parameters.yml lists sulfate,
# calcium, and magnesium in plain mg/L, which is mg/L of the ion. The NURE
# survey files calcium "as CaCO3" in the portal, which is 0.4004 of that mass
# as calcium.
IMPLIED_BASIS = {"sulfate": "SO4", "calcium": "Ca", "magnesium": "Mg"}

# eSMR and RISE state the basis in the parameter name, as in "Nitrate, Total
# (as NO3)" or "Alkalinity (as HCO3) (bicarbonate)".
_BASIS_IN_NAME = re.compile(r"\(as\s+([^)]+?)\s*\)", re.IGNORECASE)
_BASIS_IN_CANON = re.compile(r"\bas\s+(\S+)", re.IGNORECASE)


def normalize(unit: object) -> str:
    """Fold a source unit string down to a lookup key.

    WQP pads some units with asterisks (`mg/l CaCO3**`, `mg N/l******`) and USGS
    prefixes at least one with an underscore (`_FNU`). Case and spacing vary
    freely across all four sources, hence `deg C`, `degC`, `DEG C`, and
    `Degrees C` all appearing for the same measurement.
    """
    if unit is None or (isinstance(unit, float) and math.isnan(unit)):
        return ""
    s = str(unit).strip().strip("*_").strip()
    s = s.replace("µ", "u").replace("μ", "u")
    s = re.sub(r"\s+", " ", s).lower()
    return s


@dataclass(frozen=True)
class Resolution:
    convertible: bool
    factor: float | None
    offset: float | None
    canonical: str
    note: str = ""


def _canonical_basis(parameter: str, canonical_unit: str) -> str | None:
    """The reporting basis of the canonical unit: N, P, CaCO3, SO4, or None."""
    m = _BASIS_IN_CANON.search(canonical_unit)
    if m:
        return _BASIS_KEY.get(m.group(1).lower(), m.group(1))
    return IMPLIED_BASIS.get(parameter)


def restate_basis(res: Resolution, parameter: str, source_name: object) -> Resolution:
    """Fold the reporting basis in a source parameter name into a mass factor.

    eSMR publishes "Nitrate, Total (as NO3)" in mg/L beside "Nitrate, Total (as
    N)", and the unit string is the same for both. A name part with no stated
    basis counts as the canonical one, as every row without a name does. A row
    with two bases among its joined names, or one name with a mix such as
    HCO3/CO3/OH, has no single factor and stays unconverted.
    """
    target = _canonical_basis(parameter, res.canonical)
    if not res.convertible or target is None or not isinstance(source_name, str):
        return res
    found: set[str] = set()
    for part in source_name.split(";"):
        stated = _BASIS_IN_NAME.findall(part)
        found.update(_BASIS_KEY.get(b.lower(), b) for b in stated)
        if not stated:
            found.add(target)
    if found == {target}:
        return res
    bases = sorted(found)
    if len(bases) > 1:
        joined = ", ".join(bases[:-1]) + " and " + bases[-1]
        return Resolution(False, None, None, res.canonical,
                          f"{joined} bases joined in one row, so no single "
                          "factor applies")
    src = bases[0]
    if "/" in src:
        return Resolution(False, None, None, res.canonical,
                          f"reported as {src}, a mix of bases with no single factor")
    if (src not in BASIS_WEIGHT or target not in BASIS_WEIGHT
            or BASIS_WEIGHT[src][0] != BASIS_WEIGHT[target][0]):
        return Resolution(False, None, None, res.canonical,
                          f"reported as {src}, with no factor to {target}")
    w_src, w_target = BASIS_WEIGHT[src][1], BASIS_WEIGHT[target][1]
    return Resolution(True, res.factor * w_target / w_src, res.offset,
                      res.canonical,
                      f"converted from {src} to {target} using "
                      f"{w_target:.4f}/{w_src:.4f}")


def resolve(parameter: str, canonical_unit: str, raw_unit: object,
            source_name: object = None) -> Resolution:
    """Work out how to get from a source unit to the canonical one.

    `source_name` is the parameter name the source published. eSMR and RISE
    put the reporting basis there rather than in the unit string; see
    restate_basis().
    """
    canon = canonical_unit or ""
    key = normalize(raw_unit)
    dim = DIMENSION_OF_CANONICAL.get(normalize(canon))

    if dim is None:
        return Resolution(False, None, None, canon,
                          f"no conversion table for canonical unit {canon!r}")

    # An absent unit is safe only where the quantity has none. pH is the one
    # such parameter here, and a quarter of its rows arrive without a unit.
    if key == "":
        if dim == "ph":
            return Resolution(True, 1.0, 0.0, canon)
        return Resolution(False, None, None, canon, "source reported no unit")

    # Percent is the quantity for dissolved oxygen saturation and a missing
    # concentration everywhere else, so the dimension decides which it is.
    if key in WRONG_QUANTITY and dim != "fraction":
        return Resolution(False, None, None, canon, WRONG_QUANTITY[key])

    if key in NITROGEN_BASIS and normalize(canon) == "mg/l as n":
        return Resolution(True, 1.0, 0.0, canon)

    table = BY_DIMENSION[dim]
    if key in table:
        factor, offset = table[key]
        res = Resolution(True, factor, offset, canon)
        if key in UNIT_STATES_BASIS:
            return res
        return restate_basis(res, parameter, source_name)

    # Molar and equivalent forms need the species, which the unit string omits.
    # The parameter name has it. A mole or an equivalent is the same amount
    # whatever basis the name gives, so the basis does not enter here.
    if key in ("umol/l", "uequiv/l", "ueq/l", "meq/l"):
        mass = MOLAR.get(parameter)
        if mass is None:
            return Resolution(False, None, None, canon,
                              f"{key} needs a molar mass for {parameter}")
        mw, charge = mass
        if key == "umol/l":
            per_unit = mw / 1000.0
        elif charge is None:
            return Resolution(False, None, None, canon,
                              f"{key} needs a fixed ionic charge, which "
                              f"{parameter} lacks")
        else:
            per_unit = mw / charge / (1.0 if key == "meq/l" else 1000.0)
        return Resolution(True, per_unit, 0.0, canon,
                          f"converted from {key} using {mw} g/mol")

    # Storage reported in feet is a pool elevation, which the storage curve
    # turns into volume and a scale factor cannot.
    if dim == "volume" and key in ("feet", "ft"):
        return Resolution(False, None, None, canon,
                          "pool elevation, needs the reservoir storage curve")

    # Where the unit belongs to another dimension, name it. "unrecognized unit
    # 'af'" on a discharge row is less useful than "the source reported a
    # volume where the parameter is a rate".
    for other, table in BY_DIMENSION.items():
        if other != dim and key in table:
            return Resolution(False, None, None, canon,
                              f"a {DIMENSION_LABEL[other]} unit reported for a "
                              f"{DIMENSION_LABEL[dim]} measurement")

    return Resolution(False, None, None, canon, f"unrecognized unit {key!r}")


def annotate(sp: pd.DataFrame, canonical_units: dict[str, str]) -> pd.DataFrame:
    """Add unit resolution columns to a station-parameter table.

    Adds `unit_canonical`, `unit_factor`, `unit_offset`, `unit_convertible`,
    and `unit_note`. The raw string stays in `unit`, unchanged, because this
    inventory does not rewrite what a source published. The resolution keys on
    `parameter_source_name` as well as the unit, because eSMR and RISE put the
    reporting basis in the name.
    """
    out = sp.copy()
    names = (out["parameter_source_name"] if "parameter_source_name" in out.columns
             else pd.Series([None] * len(out), index=out.index))

    def _text(v: object) -> str | None:
        return v if isinstance(v, str) else None

    keys = [(p, _text(u), _text(n))
            for p, u, n in zip(out["parameter"], out["unit"], names, strict=True)]
    cache = {k: resolve(k[0], canonical_units.get(k[0], ""), k[1], k[2])
             for k in set(keys)}
    res = [cache[k] for k in keys]
    out["unit_canonical"] = [r.canonical for r in res]
    out["unit_factor"] = [r.factor for r in res]
    out["unit_offset"] = [r.offset for r in res]
    out["unit_convertible"] = [r.convertible for r in res]
    out["unit_note"] = [r.note for r in res]
    return out

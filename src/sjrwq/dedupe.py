"""Cross-source station matching.

The same physical gage appears under different identifiers in every source.
Vernalis, for example, is USGS-11303500, CDEC VNS, and several CEDEN and DWR
identifiers. Collapsing them to a single station_uid is what makes the station
count meaningful and what lets the co-location figure work.

This module matches conservatively. Two records join only when they are close
in space AND their names agree, or when they are very close in space and one of
them publishes the other's identifier. Agency coordinates for the same
site routinely disagree by tens of meters, so distance alone is not enough and
name similarity alone is far too loose.
"""

from __future__ import annotations

import re

import geopandas as gpd
import numpy as np
from rapidfuzz import fuzz

from .classify import channel_class
from .config import log

NEAR_M = 600.0      # candidate pairs within this distance are considered
MID_M = 150.0       # normal working radius
TIGHT_M = 30.0      # this close, a weaker name match is accepted
NAME_STRONG = 82    # token_set_ratio needed at MID_M
NAME_WEAK = 60      # token_set_ratio needed at TIGHT_M
NAME_EXACT = 92     # token_set_ratio needed between MID_M and NEAR_M

# Agency coordinates for one physical site disagree by more than 150 m.
# Tuolumne at Modesto is 320 m apart between USGS and CDEC, San Joaquin near
# Washington Road 541 m between CDEC and DWR, and Maze Road Bridge 454 m between
# the portal and DWR. Under a 150 m radius each of the three lands twice in the
# shortlist. The outer radius exists for those, and it demands a near-identical
# name plus the guard below.

# Pairs of words that separate two real sites. Where one name holds a term from
# the left set and the other holds its partner from the right, the two are
# different sites however close together they sit and however similar the rest
# of the name reads. This matters most for eSMR, which puts a receiving-water
# station immediately upstream and downstream of the same outfall.
#
# The pairing is explicit. Comparing every set against every later set makes
# "above" the opposite of "north", which blocks a merge between "SAN JOAQUIN R
# AB MERCED R" and any name holding an N.
_OPPOSING: list[tuple[set[str], set[str]]] = [
    ({"ab", "abv", "above", "us", "upstream", "upstrm", "upper"},
     {"bl", "blw", "below", "ds", "downstream", "dnstrm", "dstrm", "lower"}),
    ({"north", "n", "nf"}, {"south", "s", "sf"}),
    ({"east", "e", "ef"}, {"west", "w", "wf"}),
    ({"inflow", "influent", "in"}, {"outflow", "effluent", "out"}),
]

# Terms that place a station relative to a feature. Two names that place
# themselves differently are two points on that feature: CDEC TLG is
# "TUOLUMNE R-LA GRANGE DAM", the impoundment, and CDEC LGN is "TUOLUMNE R BLW
# LA GRANGE DAM", the release 106 m away, and they score 100 against each
# other because one name is a subset of the other.
_POSITION = {
    "ab": "above", "abv": "above", "above": "above", "us": "above",
    "upstream": "above", "upstrm": "above", "upper": "above",
    "bl": "below", "blw": "below", "below": "below", "ds": "below",
    "downstream": "below", "dnstrm": "below", "dstrm": "below",
    "lower": "below",
    "north": "north", "nf": "north", "south": "south", "sf": "south",
    "east": "east", "ef": "east", "west": "west", "wf": "west",
}

# What kind of feature a name calls this. Lake Eleanor and Eleanor Creek sit
# 565 m apart and score 96 on a string similarity measure; so do Beardsley Lake
# and the Beardsley powerhouse, and Utica Canal and the North Fork Stanislaus
# below it. `normalize_name` has already abbreviated river to r, creek to c,
# and canal to cn by the time this runs.
_FEATURE = {
    "r": "channel", "sjr": "channel", "sl": "slough",
    "c": "creek", "ck": "creek", "cr": "creek",
    "cn": "canal", "aqueduct": "canal", "conduit": "canal", "ditch": "canal",
    "lateral": "canal", "flume": "canal", "siphon": "canal", "tunnel": "canal",
    "dmc": "canal", "penstock": "canal",
    "lk": "lake", "lake": "lake", "res": "lake", "reservoir": "lake",
    "dam": "lake", "forebay": "lake", "afterbay": "lake",
    "ph": "plant", "powerhouse": "plant", "pp": "plant", "pump": "plant",
    "pumping": "plant", "wwtp": "plant", "wwtf": "plant", "wqcf": "plant",
    "drain": "drain", "drn": "drain", "sump": "drain",
    "well": "well", "mw": "well", "pz": "well",
}

# An eSMR monitoring point code: EFF-001, INF-001, RSW-003. A plant files its
# outfall, its influent, and the receiving water above and below it under one
# facility name, so the code is the only thing separating points that sit
# within a couple of hundred meters of each other. Turlock RWQCF files EFF-001
# and EFF-002 31 m apart, and RSW-001 and RSW-002 48 m apart. The group does not
# capture, so `findall` returns the whole code, "eff 001", and not the prefix.
_POINT_CODE = re.compile(r"\b(?:eff|inf|rsw|rec|int|lnd|sw|gw)\s+\d{2,3}[a-z]?\d?\b")

# Irrigation and water district abbreviations that appear in station names.
# Turlock and Modesto Irrigation Districts each run a canal out of La Grange,
# and CDEC files them as TIL "TID CANAL AT LA GRANGE" and MID "MID CANAL AT LA
# GRANGE" at one coordinate. The two names score 94, so the district is the
# only word that separates them. The rule needs a district in both names and
# none in common, because "mid" is also the English word in "mid-deep screen"
# and "Mid Valley", and against a name holding no district it blocks no merge.
_DISTRICT = {"tid", "mid", "ccid", "pid", "wsid", "dpwd"}


def _digit_runs(name: str) -> list[str]:
    return re.findall(r"\d+", name)


def _feature_kinds(name: str) -> set[str]:
    return {_FEATURE[t] for t in name.split() if t in _FEATURE}


def _features_conflict(a: str, b: str) -> bool:
    """Whether the two names call the feature different things.

    DWR B00470 "Salt Slough near Stevinson" sits 36 m from DWR B07400 "San
    Joaquin River near Stevinson" and scores 83 against it, because "near
    Stevinson" is most of each name. The rule requires the two sets to share a
    word rather than to match: "CHOWCHILLA R BLW BUCHANAN DAM" names a river and
    a dam, "CHOWCHILLA R BL BUCHANAN DM NR RY" the river alone, and the two are
    one gage. A name with no feature word, such as CDEC's "RIPON", conflicts
    with neither.
    """
    fa, fb = _feature_kinds(a), _feature_kinds(b)
    return bool(fa and fb and not fa & fb)


def _position_terms(name: str) -> set[str]:
    return {_POSITION[t] for t in name.split() if t in _POSITION}


def _point_codes(name: str) -> set[str]:
    return set(_POINT_CODE.findall(name))


def _districts(name: str) -> set[str]:
    return set(name.split()) & _DISTRICT


def _wide_match_ok(a: str, b: str) -> bool:
    """Whether two names may be merged across the outer radius.

    The outer radius exists for agency coordinates that disagree by hundreds of
    meters on one river station. It has to leave a well field alone. USGS names
    its monitoring wells by State Well Number, so `012S013E31A005M` and
    `012S013E31A006M` are two different wells on one drill pad, thirty meters
    apart, and they score 97 on a string similarity measure. Without a guard,
    seventy-five of them collapse into a single station.

    Three rules stop that, and the last also keeps neighboring river sites
    apart, which the digit rule alone does not: `_digit_runs` returns an empty
    list for both members of every pair of names holding no number, so it
    agrees by default on ordinary river and reservoir names.

      - the names have to be descriptive rather than a single code
      - the numbers in them have to agree, which separates Check 20 from
        Check 21 and one well from the next
      - the feature words have to agree, which separates Merced River at
        Yosemite from Yosemite Creek at Yosemite 500 m away

    A river station survives all three: "SAN JOAQUIN R BL HWY 41" and "San
    Joaquin River below Highway 41 near Pinedale" are three or more words each,
    share the number 41, and both call the feature a river. `_compatible`
    applies two further tests at every radius.
    """
    if len(a.split()) < 3 or len(b.split()) < 3:
        return False
    return (_digit_runs(a) == _digit_runs(b)
            and _feature_kinds(a) == _feature_kinds(b))


def _compatible(a: str, b: str, class_a: str, class_b: str) -> bool:
    """Whether two records close together may be one point.

    `token_set_ratio` scores 100 where one name is a subset of the other, so
    "BEAR C NR LAKE THOMAS A EDISON" and "BEAR C UPSTRM DIV DAM NR LAKE THOMAS
    A EDISON", 70 m apart, score as high as two spellings of one name. Two
    tests on what the names say rather than how similar they read:

      - the position words agree, so a release gage does not join the
        impoundment it releases from. CDEC TLG is the La Grange pool and CDEC
        LGN is the release 106 m below it, and they score 100.
      - the channel class agrees, so a canal does not join the river it diverts
        from. Turlock Canal and the La Grange pool sit 29 m apart.

    An `unknown` class settles the question for neither record, so it blocks
    no merge.
    """
    if _position_terms(a) != _position_terms(b):
        return False
    return class_a == "unknown" or class_b == "unknown" or class_a == class_b


def _opposed(a: str, b: str) -> bool:
    """True when the two names place themselves at different points or hold
    different district abbreviations."""
    ta, tb = set(a.split()), set(b.split())
    for first, second in _OPPOSING:
        if (ta & first and tb & second and not (tb & first)) or \
           (tb & first and ta & second and not (ta & first)):
            return True
    ca, cb = _point_codes(a), _point_codes(b)
    if (ca or cb) and ca != cb:
        return True
    da, db = _districts(a), _districts(b)
    return bool(da and db and not da & db)

_ABBREV = {
    r"\briver\b": "r", r"\bcreek\b": "c", r"\bslough\b": "sl",
    r"\bnear\b": "nr", r"\bbelow\b": "bl", r"\babove\b": "ab",
    r"\bat\b": "", r"\bthe\b": "", r"\bcanal\b": "cn", r"\bbridge\b": "br",
    r"\broad\b": "rd", r"\bhighway\b": "hwy", r"\bhwy\b": "hwy",
    r"\bsan joaquin\b": "sj", r"\bs\.? ?j\.? ?r\b": "sj r",
}


def normalize_name(name: object) -> str:
    s = str(name or "").lower()
    # CEDEN writes upstream and downstream as "u/s" and "d/s". Joining the
    # letters before the punctuation goes gives "us" and "ds", which
    # `_OPPOSING` and `_POSITION` list. Stripping the slash first gives "u s"
    # and "d s": the guards list neither "u" nor "d", and "s" counts as south
    # in both names.
    s = re.sub(r"\b([ud])\s*/\s*s\b", r"\1s", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    for pat, rep in _ABBREV.items():
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


class _Union:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _id_tokens(sid: object, name: object = None) -> set[str]:
    """Digit runs of length >= 6, e.g. the 11303500 inside USGS-11303500.

    The name counts too. CDEC calls Check 13 "CAL AQUEDUCT CHECK 13 (KA007089)"
    and the portal files the same point as CALWR_WQX-KA007089, so the link sits
    in one source's name and the other's identifier. Reading both merges the
    three records at Check 13 that Reclamation, CDEC, and the portal each
    publish 35 m apart. 5 station names in this inventory hold a run that long
    and each one is a stated identifier.
    """
    text = str(sid or "")
    tokens = set(re.findall(r"\d{6,}", text + " " + str(name or "")))
    # The portal republishes a CEDEN station as CEDEN-535XSSJ17 and CEDEN
    # publishes it as 535XSSJ17, so the tail after the provider prefix is the
    # link. Digits and letters both, because a run of digits alone is already
    # covered above and a word alone is a name rather than a code.
    code = text.rsplit("-", 1)[-1].strip().upper()
    if (len(code) >= 6 and " " not in code
            and any(c.isdigit() for c in code) and any(c.isalpha() for c in code)):
        tokens.add(code)
    return tokens


# Distance beyond which a stated identifier is a stale or mistyped link rather
# than the same site. DWR gives station B04105, Tuolumne River at Tuolumne
# City, the CDEC identifier TRT, and the two coordinates are 13 km apart.
STATED_ID_M = NEAR_M


def _link_stated_ids(g: gpd.GeoDataFrame, uf: _Union,
                     crs_area: str = "EPSG:3310") -> int:
    """Merge on an identifier a source publishes for another source's station.

    DWR's Water Data Library publishes a `cdec_id` for 72 of its 212 stations
    in this basin. That is the operator stating the link, which beats a name
    match, and it merges pairs whose coordinates disagree by half a kilometer.
    The distance still has to be plausible: see `STATED_ID_M`.
    """
    if "cdec_id" not in g.columns:
        return 0
    cdec_rows = {}
    for i, (src, sid) in enumerate(zip(g["source"], g["source_station_id"], strict=True)):
        if src == "cdec":
            cdec_rows[str(sid).strip().upper()] = i

    proj = g.to_crs(crs_area)
    xs, ys = proj.geometry.x.values, proj.geometry.y.values
    joined, far = 0, 0
    for i, stated in enumerate(g["cdec_id"]):
        if not isinstance(stated, str) or not stated.strip():
            continue
        target = cdec_rows.get(stated.strip().upper())
        if target is None or target == i:
            continue
        dist = float(np.hypot(xs[i] - xs[target], ys[i] - ys[target]))
        if dist > STATED_ID_M:
            log.info("dedupe: %s states CDEC %s and sits %.0f m away, leaving "
                     "them apart", g.at[i, "source_station_id"], stated.strip(),
                     dist)
            far += 1
            continue
        if uf.find(i) != uf.find(target):
            uf.union(i, target)
            joined += 1
    if joined or far:
        log.info("dedupe: %d pairs merged on a stated CDEC identifier, %d "
                 "refused on distance", joined, far)
    return joined


def _classes(g: gpd.GeoDataFrame) -> list[str]:
    """What kind of water body each record names, for the merge guard.

    A canal and the river it diverts from sit 29 m apart at La Grange, a
    reservoir gage and its release gage 106 m apart, and a lake and the creek
    below it 565 m apart. Each pair scores high enough on name similarity to
    merge, and merging them files a river record under a canal name. This runs
    the same classifier the catalog uses, so the merge rule and the
    `channel_class` column read one set of patterns.
    """
    loc = g["location_type"] if "location_type" in g.columns else None
    typ = g["station_type"] if "station_type" in g.columns else None
    return [channel_class(n,
                          typ.iloc[k] if typ is not None else None,
                          loc.iloc[k] if loc is not None else None)
            for k, n in enumerate(g["name"])]


def assign_station_uid(gdf: gpd.GeoDataFrame, crs_area: str = "EPSG:3310") -> gpd.GeoDataFrame:
    """Add station_uid, is_primary, and n_sources columns."""
    if gdf.empty:
        gdf["station_uid"] = []
        return gdf

    g = gdf.reset_index(drop=True).copy()
    proj = g.to_crs(crs_area)
    g["_norm"] = g["name"].map(normalize_name)
    g["_ids"] = [_id_tokens(sid, name) for sid, name
                 in zip(g["source_station_id"], g["name"], strict=True)]
    g["_class"] = _classes(g)

    # Candidate pairs: self-join of a NEAR_M buffer, which is far cheaper than
    # an all-pairs distance matrix over ~10k points.
    buf = gpd.GeoDataFrame(geometry=proj.buffer(NEAR_M), crs=crs_area)
    pairs = gpd.sjoin(buf, proj[["geometry"]], how="inner", predicate="intersects")
    pairs = pairs[pairs.index < pairs["index_right"]]

    uf = _Union(len(g))
    joined, blocked = 0, 0
    xs, ys = proj.geometry.x.values, proj.geometry.y.values
    for i, j in zip(pairs.index.values, pairs["index_right"].values):
        dist = float(np.hypot(xs[i] - xs[j], ys[i] - ys[j]))
        if dist > NEAR_M:
            continue
        ni, nj = g.at[i, "_norm"], g.at[j, "_norm"]
        score = fuzz.token_set_ratio(ni, nj) if ni and nj else 0
        shares_id = bool(g.at[i, "_ids"] & g.at[j, "_ids"])
        if _opposed(ni, nj):
            continue
        ok = (
            shares_id
            or (dist <= MID_M and score >= NAME_STRONG
                and not _features_conflict(ni, nj))
            or (dist <= TIGHT_M and score >= NAME_WEAK)
            or (score >= NAME_EXACT and _wide_match_ok(ni, nj))
        )
        # A stated identifier outranks both guards: where two sources publish
        # the same code the classes disagreeing means one of them is wrong, not
        # that there are two stations.
        if ok and not shares_id and not _compatible(
                ni, nj, g.at[i, "_class"], g.at[j, "_class"]):
            blocked += 1
            continue
        if ok:
            uf.union(int(i), int(j))
            joined += 1

    if blocked:
        log.info("dedupe: %d name matches refused because the two records "
                 "describe different points", blocked)
    joined += _link_stated_ids(g, uf, crs_area)

    g["_cluster"] = [uf.find(i) for i in range(len(g))]
    log.info("dedupe: %d records, %d merge links, %d clusters",
             len(g), joined, g["_cluster"].nunique())

    # Stable, readable uid from the cluster's preferred record. The order is
    # by how directly the source operates the station: USGS, CDEC, DWR, and
    # Reclamation publish their own gages, while the portal and CEDEN
    # republish other people's, and eSMR is a permit filing rather than a
    # station record.
    priority = {"usgs": 0, "cdec": 1, "dwr_wdl": 2, "usbr_rise": 3,
                "wqp": 4, "ceden": 5, "esmr": 6}
    g["_prio"] = g["source"].map(priority).fillna(9)
    order = g.sort_values(["_cluster", "_prio", "source_station_id"])
    primary = order.groupby("_cluster").head(1)
    uid_map = dict(zip(primary["_cluster"], primary["source"] + ":" + primary["source_station_id"]))

    g["station_uid"] = g["_cluster"].map(uid_map)
    g["is_primary"] = g.index.isin(primary.index)
    g["n_sources"] = g.groupby("station_uid")["source"].transform("nunique")
    return g.drop(columns=["_norm", "_ids", "_class", "_cluster", "_prio"])

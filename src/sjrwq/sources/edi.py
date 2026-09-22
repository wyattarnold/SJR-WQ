"""Environmental Data Initiative (EDI), the repository the IEP publishes to.

This is the one source here that is a repository rather than an agency. EDI
operates no stations of its own; researchers deposit finished datasets, and the
Interagency Ecological Program deposits the long Delta monitoring records that
sit directly on this model's downstream boundary. The prize is `edi.2180`,
fifteen-minute water quality at eighteen South Delta stations running 1999 to
the present. Four of them fall inside the Mossdale boundary buffer, and at
those four EDI's conductance record starts thirteen to sixteen years before
the CDEC sensor that stands there now.

PASTA returns HTTP 403 for an anonymous request to its search, revision-list,
and metadata endpoints, with a message naming the public user. That is a
permission on those three service methods, not on the data. Two routes work:

  - DataONE indexes EDI as a member node and serves both the Solr catalog and
    the EML objects with no credential. This is the default route.
  - PASTA's own search opens to an account token presented as an `edi-token`
    cookie. Set EDI_API_KEY to an API key created in the EDI web UI, or
    EDI_TOKEN to a JWT, and this module uses PASTA instead. It adds revision
    freshness: DataONE lags, and it served edi.731.6 on the day PASTA served
    edi.731.7.

Neither route pulls a time series. This module parses the EML and stops.
`edi.2180`'s per-station CSVs are 35 MB each.

Two layouts come out of that, and the `period_scope` column marks which one a
row came from. `edi.2180` publishes one table per station, so the EML names the
station, the parameter, and the record count, and the scope is `station`. Every
other package publishes one wide table keyed by a station column, so the
metadata names the parameters and names the stations without pairing the two.
Those rows put the package's parameters on every station in the package, the
scope is `dataset`, the sample count stays null rather than divided up, and the
reporting interval is the table's row count spread evenly across every station
the package declares, inside the study bbox or out of it.
A `dataset` row is a claim about the package, so check the data file before
relying on it.

Three filters decide what to keep, and each one logs what it rejected:

  local        the declared envelope is under MAX_ENVELOPE_DEG across. A
               continental dataset overlaps this study area without describing
               it: LAGOS-US covers lakes from Maine to California.
  locatable    at least one geographicCoverage is a point, meaning west equals
               east and north equals south, inside the study bbox. A package
               that declares only a bounding box has no stations to inventory.
  observed     at least one attribute maps to a core or water-balance parameter,
               and the package is not model output. Both matter. Without the
               first, 342 soil-temperature dataloggers 40 m apart in the San
               Joaquin Experimental Range arrive as river stations. Without the
               second, so do 656 lakes whose temperature comes from a model
               rather than from a sensor.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .. import classify
from ..config import ParameterLookup, fetch, log, session

SOURCE = "edi"
PASTA = "https://pasta.lternet.edu/package"
DATAONE = "https://cn.dataone.org/cn/v2"
AUTH_KEY_URL = "https://auth.edirepository.org/auth/v1/key"
PORTAL = "https://portal.edirepository.org/nis/mapbrowse?packageid="

# Widest declared envelope, in degrees, that still counts as a local dataset.
# California is about 10 degrees tall, so six keeps a statewide study and drops
# a continental one. Measured over the 514 EDI packages whose envelope
# overlaps this bbox, it removes 399 of them.
MAX_ENVELOPE_DEG = 6.0

# How close two bounding coordinates have to be before the coverage counts as
# a point rather than a box. EML publishes a station as four identical numbers.
POINT_TOLERANCE_DEG = 1e-6

# Groups that make a package worth keeping.
RELEVANT_GROUPS = {"core", "water_balance"}

# Attribute text that means this column is not a water measurement, tested
# before the parameter patterns. The first line is the wrong medium, the second
# is a flag or a comment rather than a value, and the third is a false hit on a
# parameter pattern: `SalinityPreference` is a fish trait and `flow cytometry`
# counts cells.
NOT_A_MEASUREMENT = re.compile(
    r"\bair\b|\bsoil\b|near[\s-]*surface|dew\s*point|\bat\s*2\s*m\b|\bsediment\b"
    r"|\bflag\b|\bqaqc\b|quality\s*code|\bcomments?\b|\bnotes?\b|\bmethod\b"
    r"|\bsign\b|preference|tolerance|flow\s*cytom|overflow|flowering",
    re.IGNORECASE)

# Column names that identify or describe rather than measure, tested against the
# name alone. These words are ordinary inside a definition and disqualifying in
# a name: "turbidity at the site" is a turbidity column, and `EZStation` is not
# a conductance column just because EZ stations are defined by a conductance.
# Substrings rather than whole words, because EML runs them together.
NOT_A_VALUE = re.compile(r"station|site|species|taxon|stage|habitat|\bsex\b",
                         re.IGNORECASE)

# Text marking values as computed rather than observed. Tested against the
# title, the abstract, and every entity name and description. A package that
# mixes the two goes out whole, which is the conservative direction: the
# inventory records where measurements are, and a modeled station is worse
# than a missing one.
# Word boundaries fail here: EML entity names are file names, and
# `Modeled_thermal_sensitivity_in_1625_lakes.csv` has an underscore where a
# boundary needs whitespace. Bounded on letters instead.
MODELED = re.compile(
    r"(?<![a-z])(?:modell?ed|modell?ing|simulat\w*|predicted|hindcast\w*"
    r"|forecast\w*)(?![a-z])", re.IGNORECASE)

# A five-letter run keeps a pattern from firing on ordinary English, so it is
# safe against an attribute's prose definition as well as its name. `conductiv`
# reaches a sentence only in a conductance context. `\bdo\b` reaches most
# sentences, and matching it against a definition puts dissolved oxygen on
# every column whose description contains the word "do".
PROSE_SAFE = re.compile(r"[A-Za-z]{5,}")


def token() -> str | None:
    """An EDI session token, when the environment provides one.

    Returns None when neither parameter is set, which is the normal case rather
    than an error, because the DataONE route needs no account. This function
    logs and caches none of it, so no credential reaches data/raw or the
    console.
    """
    tok = (os.environ.get("EDI_TOKEN") or "").strip()
    if tok:
        return tok
    key = (os.environ.get("EDI_API_KEY") or "").strip()
    if not key:
        return None
    try:
        r = session().post(AUTH_KEY_URL, data={"key": key}, timeout=60)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - fall back to the anonymous route
        log.warning("edi: EDI_API_KEY did not exchange for a token (%s); "
                    "using the anonymous DataONE route", type(exc).__name__)
        return None
    body = r.text.strip()
    if body.startswith("{"):
        body = json.loads(body).get("edi-token", "")
    return body.strip() or None


def _headers(tok: str | None) -> dict[str, str] | None:
    return {"Cookie": f"edi-token={tok}"} if tok else None


def discover(bbox: list[float]) -> pd.DataFrame:
    """EDI packages whose declared envelope overlaps the study bbox.

    DataONE's Solr index holds one envelope per package, already the union of
    what the EML declares, and filters on it server-side. PASTA's own search
    returns each envelope separately and has no working spatial filter, so this
    function uses DataONE either way and asks PASTA about revisions later.
    """
    west, south, east, north = bbox
    q = (f'datasource:"urn:node:EDI"'
         f" AND eastBoundCoord:[{west} TO *] AND westBoundCoord:[* TO {east}]"
         f" AND northBoundCoord:[{south} TO *] AND southBoundCoord:[* TO {north}]")
    fields = ("id,title,beginDate,endDate,westBoundCoord,eastBoundCoord,"
              "northBoundCoord,southBoundCoord")
    rows, start = [], 0
    while True:
        params = {"q": q, "fl": fields, "rows": 200, "start": start,
                  "wt": "json", "sort": "id asc"}
        payload = json.loads(fetch(f"{DATAONE}/query/solr/", SOURCE, params=params,
                                   label=f"solr_{start:05d}.json", timeout=180))
        batch = payload["response"]["docs"]
        rows.extend(batch)
        start += len(batch)
        if not batch or start >= payload["response"]["numFound"]:
            break

    out = []
    for doc in rows:
        # https://pasta.lternet.edu/package/metadata/eml/edi/2180/2
        parts = str(doc.get("id", "")).rstrip("/").split("/")
        if len(parts) < 3 or not parts[-1].isdigit():
            continue
        scope, ident, rev = parts[-3], parts[-2], int(parts[-1])
        w, e = doc.get("westBoundCoord"), doc.get("eastBoundCoord")
        n, s = doc.get("northBoundCoord"), doc.get("southBoundCoord")
        if None in (w, e, n, s):
            continue
        out.append({"scope": scope, "identifier": ident, "revision": rev,
                    "package": f"{scope}.{ident}", "title": doc.get("title", ""),
                    "width_deg": e - w, "height_deg": n - s})
    df = pd.DataFrame(out)
    if df.empty:
        return df

    wide = df[(df["width_deg"] > MAX_ENVELOPE_DEG) | (df["height_deg"] > MAX_ENVELOPE_DEG)]
    df = df.drop(wide.index)
    # Keep the newest revision DataONE indexes; PASTA may hold a newer one.
    df = (df.sort_values("revision").groupby("package", as_index=False).last())
    log.info("edi: %d packages overlap the study bbox, %d local "
             "(envelope <= %g deg across), %d distinct packages after taking the "
             "newest revision of each", len(out), len(out) - len(wide),
             MAX_ENVELOPE_DEG, len(df))
    return df.reset_index(drop=True)


def newest_revision(scope: str, identifier: str, tok: str) -> int | None:
    """Ask PASTA for the highest revision of a package. Needs a token.

    A failed lookup returns None and the harvest keeps the DataONE revision,
    except when PASTA refuses the token (401 or 403): that exception reaches
    the caller, which drops the token for the rest of the run.
    """
    try:
        txt = fetch(f"{PASTA}/eml/{scope}/{identifier}", SOURCE,
                    label=f"rev_{scope}.{identifier}.txt", headers=_headers(tok))
    except Exception as exc:
        if _refused(exc) is not None:
            raise
        return None  # revision freshness is a nicety
    revs = [int(v) for v in str(txt).split() if v.strip().isdigit()]
    return max(revs) if revs else None


def eml(scope: str, identifier: str, revision: int, tok: str | None = None) -> str:
    """The EML document for one package revision, cached under data/raw/edi.

    Both routes return byte-identical XML, so they share a cache label, each
    names the other as `equivalent`, and a harvest that started anonymously
    does not re-download once a token appears.
    """
    label = f"{scope}.{identifier}.{revision}.xml"
    pid = f"{PASTA}/metadata/eml/{scope}/{identifier}/{revision}"
    url = f"{DATAONE}/object/{urllib.parse.quote(pid, safe='')}"
    if tok:
        return str(fetch(pid, SOURCE, label=label, headers=_headers(tok),
                         equivalent=(url,)))
    return str(fetch(url, SOURCE, label=label, equivalent=(pid,)))


def _refused(exc: BaseException) -> int | None:
    """The HTTP status when PASTA refused the token (401 or 403), else None."""
    response = getattr(exc.__cause__, "response", None)
    status = getattr(response, "status_code", None)
    return status if status in (401, 403) else None


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return re.sub(r"\s+", " ", found.text).strip()


def _float(node: ET.Element, path: str) -> float | None:
    raw = _text(node, path)
    try:
        return float(raw)
    except ValueError:
        return None


def stations_in(root: ET.Element, bbox: list[float] | None) -> list[dict]:
    """Every geographicCoverage that is a point inside the bbox.

    A bbox of None keeps every point the package declares. EML writes a
    monitoring station as a degenerate bounding box, four copies of two
    numbers. A coverage with real width is the extent of a survey rather than
    the position of an instrument, so this function skips it rather than
    reducing it to a centroid that could place a station in the middle of the
    Delta by arithmetic.
    """
    west, south, east, north = bbox or (-180.0, -90.0, 180.0, 90.0)
    out = []
    for cov in root.iter("geographicCoverage"):
        w = _float(cov, "boundingCoordinates/westBoundingCoordinate")
        e = _float(cov, "boundingCoordinates/eastBoundingCoordinate")
        n = _float(cov, "boundingCoordinates/northBoundingCoordinate")
        s = _float(cov, "boundingCoordinates/southBoundingCoordinate")
        if None in (w, e, n, s):
            continue
        if abs(e - w) > POINT_TOLERANCE_DEG or abs(n - s) > POINT_TOLERANCE_DEG:
            continue
        if not (west <= w <= east and south <= n <= north):
            continue
        out.append({"code": _text(cov, "geographicDescription"),
                    "lat": round(n, 6), "lon": round(w, 6)})
    # The same station is redeclared once per survey in the older IEP packages.
    seen, unique = set(), []
    for row in out:
        key = (row["code"], row["lat"], row["lon"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _dates(node: ET.Element) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last calendar date in a coverage block."""
    dates = [d.text for d in node.iter("calendarDate") if d.text]
    parsed = pd.to_datetime(pd.Series(dates), errors="coerce", format="mixed")
    parsed = parsed.dropna()
    if parsed.empty:
        return pd.NaT, pd.NaT
    return parsed.min(), parsed.max()


def entities(root: ET.Element, lookup: ParameterLookup) -> list[dict]:
    """One row per data entity, with the parameters its columns map to.

    The attribute list sits inside the entity, so a table's row count belongs
    to the parameters in that same table. That keeps a fish-catch table of
    557,601 rows from setting the reporting interval for the water quality
    table of 10,325 rows beside it in the same package.
    """
    out = []
    for ent in list(root.iter("dataTable")) + list(root.iter("otherEntity")):
        parameters: dict[str, tuple[str, str, str]] = {}
        for attr in ent.iter("attribute"):
            name = _text(attr, "attributeName")
            definition = _text(attr, "attributeDefinition")
            if NOT_A_MEASUREMENT.search(f"{name} {definition}") or NOT_A_VALUE.search(name):
                continue
            canon, matched_on = _match(name, definition, lookup)
            if not canon:
                continue
            # A column named for its parameter beats one identified from its
            # prose, so `Chlorophyll` wins over a column described as holding a
            # chlorophyll qualifier.
            if canon not in parameters or (matched_on == "name"
                                          and parameters[canon][2] == "definition"):
                parameters[canon] = (name, _unit(attr), matched_on)
        if not parameters:
            continue
        n_records = _text(ent, "numberOfRecords")
        start, end = (_dates(ent.find("coverage")) if ent.find("coverage") is not None
                      else (pd.NaT, pd.NaT))
        out.append({"entity": _text(ent, "entityName"),
                    "description": _text(ent, "entityDescription"),
                    "n_records": int(n_records) if n_records.isdigit() else None,
                    "por_start": start, "por_end": end,
                    "parameters": parameters})
    return out


def _match(name: str, definition: str,
           lookup: ParameterLookup) -> tuple[str | None, str]:
    """Canonical parameter for an EML column, name first and definition second."""
    for pattern, canon in lookup.by_edi_pattern:
        if pattern.search(name):
            return canon, "name"
    for pattern, canon in lookup.by_edi_pattern:
        if PROSE_SAFE.search(pattern.pattern) and pattern.search(definition):
            return canon, "definition"
    return None, ""


def _unit(attr: ET.Element) -> str:
    for path in ("standardUnit", "customUnit"):
        found = next(attr.iter(path), None)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def is_modeled(root: ET.Element) -> str | None:
    """Where the modeled wording appears, or None if it does not appear."""
    title = _text(root, "dataset/title")
    node = root.find("dataset/abstract")
    abstract = " ".join(t.text or "" for t in node.iter()) if node is not None else ""
    names = " ".join((e.text or "") for tag in ("entityName", "entityDescription")
                     for e in root.iter(tag))
    for where, text in (("title", title), ("abstract", abstract), ("entities", names)):
        if MODELED.search(text):
            return where
    return None


def _operator(root: ET.Element) -> str:
    for creator in root.iter("creator"):
        org = _text(creator, "organizationName")
        if org:
            return org
    return "Environmental Data Initiative"


def parse(doc: str, package: str, revision: int, bbox: list[float],
          lookup: ParameterLookup) -> tuple[list[dict], list[dict], str | None]:
    """Stations and station-parameter rows from one package's EML.

    The third return value is the reason the package was skipped, or None if it
    was kept, so the caller can report the rejections instead of losing them.
    """
    root = ET.fromstring(doc)
    sites = stations_in(root, bbox)
    if not sites:
        return [], [], "no point coverage inside the bbox"

    ents = entities(root, lookup)
    found = {v for e in ents for v in e["parameters"]}
    if not any(lookup.groups.get(v) in RELEVANT_GROUPS for v in found):
        return [], [], f"no core or water-balance parameter (found {sorted(found) or 'none'})"

    modeled = is_modeled(root)
    if modeled:
        return [], [], f"values are modeled, said in the {modeled}"

    coverage = root.find("dataset/coverage")
    pkg_start, pkg_end = _dates(coverage) if coverage is not None else (pd.NaT, pd.NaT)
    operator = _operator(root)
    title = _text(root, "dataset/title")
    url = f"{PORTAL}{package}.{revision}"

    by_code = {s["code"]: s for s in sites}
    # A wide table's row count covers every station the package declares, in
    # the study bbox or out of it, so the per-station share divides by all of
    # them. edi.731.6 declares 7,979 points and 1,464 fall in the bbox.
    n_package_stations = len(stations_in(root, None))
    stations = [{"source_station_id": f"{package}:{s['code']}",
                 "name": s["code"], "operator": operator, "station_type": None,
                 "lat": s["lat"], "lon": s["lon"], "url": url,
                 "access": "api", "package": f"{package}.{revision}",
                 "dataset_title": title} for s in sites]
    named = {st["source_station_id"]: st for st in stations}

    rows = []
    for ent in ents:
        start = ent["por_start"] if pd.notna(ent["por_start"]) else pkg_start
        end = ent["por_end"] if pd.notna(ent["por_end"]) else pkg_end
        span = (end - start).days if pd.notna(start) and pd.notna(end) else None

        # An entity named for a station describes that station alone, which is
        # how edi.2180 is laid out: eighteen tables, one per sonde. Anything
        # else is a wide table keyed by a station column, so its columns apply
        # to every station in the package and its row count divides among all
        # of the package's stations.
        per_station = ent["entity"] in by_code
        targets = ([f"{package}:{ent['entity']}"] if per_station
                   else list(named))
        if per_station:
            uid = targets[0]
            station = named.get(uid)
            if station is not None and ent["description"]:
                station["name"] = ent["description"]
            n_obs, share = ent["n_records"], ent["n_records"]
        else:
            n_obs = None
            share = (ent["n_records"] / n_package_stations) if ent["n_records"] else None

        interval = round(span / share, 3) if span and share and share > 1 else None
        frequency = classify.frequency_from_interval(interval)
        record_type = ("continuous" if frequency in classify.CONTINUOUS_FREQUENCIES
                       else "discrete")
        for uid in targets:
            for canon, (src_name, unit, matched_on) in ent["parameters"].items():
                rows.append({
                    "source_station_id": uid, "parameter": canon,
                    "parameter_source_name": src_name, "unit": unit,
                    "matched_on": matched_on,
                    "por_start": start, "por_end": end,
                    "n_obs": n_obs, "median_interval_days": interval,
                    "frequency": frequency, "record_type": record_type,
                    # Whose period of record this row holds. A table named for
                    # one station describes that station; a wide table keyed by
                    # a station column describes the package.
                    "period_scope": "station" if per_station else "dataset",
                    "n_records_table": ent["n_records"], "entity": ent["entity"],
                    "package": f"{package}.{revision}",
                })
    return stations, rows, None


def harvest(bbox: list[float], lookup: ParameterLookup | None = None
            ) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    lookup = lookup or ParameterLookup.build()
    tok = token()
    log.info("edi: %s", "authenticated to PASTA" if tok
             else "no EDI_API_KEY or EDI_TOKEN set, using the anonymous DataONE route")

    candidates = discover(bbox)
    if candidates.empty:
        raise RuntimeError("EDI returned no packages overlapping the study bbox")

    def drop_token(status: int, package: str) -> None:
        # A token PASTA refuses is expired or mistyped, so the harvest drops
        # it for the remaining packages and reads DataONE at the revision
        # DataONE indexes, rather than retrying PASTA on each.
        log.warning("edi: PASTA refused EDI_TOKEN (HTTP %d) on %s; using "
                    "the anonymous DataONE route for the rest of the run",
                    status, package)

    st_rows, sp_rows, skipped = [], [], {}
    for row in candidates.itertuples(index=False):
        revision = row.revision
        if tok:
            try:
                newest = newest_revision(row.scope, row.identifier, tok)
            except Exception as exc:  # noqa: BLE001 - only a refusal reaches here
                drop_token(_refused(exc), row.package)
                tok, newest = None, None
            if newest and newest > revision:
                log.info("edi: %s is at revision %d on PASTA, %d on DataONE",
                         row.package, newest, revision)
                revision = newest
        try:
            try:
                doc = eml(row.scope, row.identifier, revision, tok)
            except Exception as exc:
                status = _refused(exc) if tok else None
                if status is None:
                    raise
                drop_token(status, row.package)
                tok, revision = None, row.revision
                doc = eml(row.scope, row.identifier, revision)
        except Exception as exc:  # noqa: BLE001 - one bad package is not fatal
            log.warning("edi: %s.%d metadata unavailable (%s)",
                        row.package, revision, exc)
            skipped[row.package] = "metadata unavailable"
            continue
        stations, rows, reason = parse(doc, row.package, revision, bbox, lookup)
        if reason:
            skipped[row.package] = reason
            continue
        st_rows.extend(stations)
        sp_rows.extend(rows)
        log.info("edi: %s.%d kept, %d stations, %d station-parameter rows: %s",
                 row.package, revision, len(stations), len(rows), row.title[:60])

    _report_skips(skipped)
    if not st_rows:
        raise RuntimeError("EDI: no package survived the relevance filters")

    stations = pd.DataFrame(st_rows).drop_duplicates("source_station_id")
    sp = pd.DataFrame(sp_rows)
    # One row per station and parameter: the shortest interval wins, and a
    # column matched on its own name beats one matched on its description.
    sp = (sp.assign(_prose=(sp["matched_on"] != "name"))
            .sort_values(["_prose", "median_interval_days"])
            .drop_duplicates(["source_station_id", "parameter"], keep="first")
            .drop(columns="_prose"))
    sp["source"] = SOURCE
    stations["source"] = SOURCE

    gdf = gpd.GeoDataFrame(
        stations,
        geometry=[Point(lon, lat) for lon, lat in zip(stations["lon"],
                                                      stations["lat"], strict=True)],
        crs="EPSG:4326")
    log.info("edi: %d stations, %d station-parameter rows, %d packages",
             len(gdf), len(sp), sp["package"].nunique())
    log.info("edi: frequency %s", sp["frequency"].value_counts().to_dict())
    return gdf, sp


def _report_skips(skipped: dict[str, str]) -> None:
    """Say what was dropped and why, grouped by reason.

    A relevance filter that runs silently is indistinguishable from a bug, and
    this one throws away 95 percent of what the search returns.
    """
    if not skipped:
        return
    by_reason: dict[str, list[str]] = {}
    for package, reason in skipped.items():
        key = reason.split(" (")[0]
        by_reason.setdefault(key, []).append(package)
    log.info("edi: %d packages skipped", len(skipped))
    for reason, packages in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        log.info("  %4d  %s (%s%s)", len(packages), reason,
                 ", ".join(sorted(packages)[:4]),
                 ", ..." if len(packages) > 4 else "")

"""Paths, config loading, and a caching HTTP helper shared by all source modules."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import requests
import truststore
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
FIGURES = ROOT / "figures"
DOCS = ROOT / "docs"

USER_AGENT = "SJR-WQ data inventory (research use; contact wyetto@gmail.com)"

log = logging.getLogger("sjrwq")


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def domain_config() -> dict:
    return _load("domain.yml")


def parameter_config() -> dict:
    return _load("parameters.yml")["parameters"]


@dataclass(frozen=True)
class ParameterLookup:
    """Reverse maps from source-specific codes to canonical parameter names."""

    by_usgs_pcode: dict
    by_cdec_sensor: dict
    by_wqp_name: dict
    by_esmr_parameter: dict
    by_usbr_name: dict
    by_dwr_name: dict
    # EDI has no controlled vocabulary, so its crosswalk is an ordered list of
    # (compiled pattern, canonical name) rather than a name-keyed dict. Order
    # follows parameters.yml, and the first pattern to match wins.
    by_edi_pattern: tuple
    groups: dict
    units: dict

    @classmethod
    def build(cls) -> ParameterLookup:
        cfg = parameter_config()
        usgs, cdec, wqp, esmr, groups, units = {}, {}, {}, {}, {}, {}
        usbr, dwr, edi = {}, {}, []
        for canon, spec in cfg.items():
            groups[canon] = spec.get("group", "other")
            units[canon] = spec.get("unit", "")
            for code in spec.get("usgs_pcode") or []:
                usgs[str(code)] = canon
            for sensor in spec.get("cdec_sensor") or []:
                cdec[int(sensor)] = canon
            for name in spec.get("wqp_characteristic") or []:
                wqp[name.strip().lower()] = canon
            for name in spec.get("esmr_parameter") or []:
                esmr[name.strip().lower()] = canon
            for name in spec.get("usbr_parameter") or []:
                usbr[name.strip().lower()] = canon
            for name in spec.get("dwr_parameter") or []:
                dwr[name.strip().lower()] = canon
            for pattern in spec.get("edi_attribute") or []:
                edi.append((re.compile(pattern, re.IGNORECASE), canon))
        return cls(usgs, cdec, wqp, esmr, usbr, dwr, tuple(edi), groups, units)

    def wqp_characteristic_names(self, groups: list[str] | None = None) -> list[str]:
        cfg = parameter_config()
        out = []
        for canon, spec in cfg.items():
            if groups and spec.get("group") not in groups:
                continue
            out.extend(spec.get("wqp_characteristic") or [])
        return sorted(set(out))


def raw_dir(source: str) -> Path:
    """Raw landing directory for a source. Downloads go here untouched.

    One directory per source, not one per source and acquisition date. The date
    is not lost: every response has a `.meta.json` beside it holding the
    request URL, the status, the byte count, and the retrieval timestamp, so
    the provenance sits with the file rather than in a path segment. Dated
    directories also made a re-run read whichever generation the caller
    happened to name, and a split query that a later run replaced with one
    request left both generations on disk.
    """
    d = RAW / source
    d.mkdir(parents=True, exist_ok=True)
    return d


_SESSION: requests.Session | None = None


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        # truststore checks certificates against the operating system's store
        # in place of certifi's bundle. A network that re-signs TLS with its own
        # root CA installs that root in the system store and not in certifi.
        truststore.inject_into_ssl()
        _SESSION = requests.Session()
        _SESSION.headers.update({"User-Agent": USER_AGENT})
    return _SESSION


def _same_request(a: str, b: str) -> bool:
    """Whether two URLs make the same request.

    The comparison decodes the path and the query and ignores parameter order,
    because a `.meta.json` records a comma in a value as `%2C` or as `,`
    depending on how the query was encoded, and lists the parameters in the
    order the caller built them.
    """
    sa, sb = urlsplit(a), urlsplit(b)
    return ((sa.scheme, sa.netloc.lower(), unquote(sa.path))
            == (sb.scheme, sb.netloc.lower(), unquote(sb.path))
            and sorted(parse_qsl(sa.query, keep_blank_values=True))
            == sorted(parse_qsl(sb.query, keep_blank_values=True)))


def _sidecar(path: Path) -> dict:
    """The `.meta.json` beside a cached file, or an empty dict."""
    try:
        return json.loads(path.with_suffix(path.suffix + ".meta.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fetch(
    url: str,
    source: str,
    params: dict | None = None,
    label: str | None = None,
    timeout: int = 180,
    retries: int = 3,
    backoff: float = 3.0,
    binary: bool = False,
    headers: dict[str, str] | None = None,
    equivalent: tuple[str, ...] = (),
    validate: Callable[[str | bytes], str | None] | None = None,
) -> str | bytes:
    """GET a URL, caching the raw response under data/raw/<source>/.

    Re-running is cheap: a cached file short-circuits the request. Delete the
    file, or the source directory, to force a refresh.

    The cache is keyed on `label`, so a hit also has to match the URL in the
    `.meta.json` beside the file. On a mismatch, such as a changed bbox or
    characteristic list, this function logs a warning and fetches again.
    `equivalent` lists other URLs that return the same bytes, and a file
    fetched from one of them counts as a hit. A cached file with no readable
    `.meta.json` counts as a hit, because it has no URL to compare.

    `validate` reads a response and names what is wrong with it, or returns
    None where it passes. A source whose server reports a partial response in
    the body rather than in the status needs one: the check runs before the
    write, so a rejected response costs a retry and reaches no cache file, and
    it runs again on a hit, so a file cached before the check gets refetched.
    """
    key = label or hashlib.sha1(
        (url + json.dumps(params or {}, sort_keys=True)).encode()
    ).hexdigest()[:16]
    suffix = ".bin" if binary else ""
    path = raw_dir(source) / f"{key}{suffix}"

    # Encode the query ourselves so commas stay literal in values like
    # bBox=west,south,east,north. requests percent-encodes them to %2C, which
    # is legal but harder to read in logs and in the cached .meta.json. Every
    # endpoint used here accepts both forms.
    query = None
    if params:
        pairs = list(params.items()) if isinstance(params, dict) else list(params)
        query = urlencode([(k, v) for k, v in pairs], doseq=True, safe=",")
    requested = requests.Request("GET", url, params=query).prepare().url

    if path.exists() and path.stat().st_size > 0:
        meta = _sidecar(path)
        recorded = meta.get("request_url") or meta.get("url")
        if recorded is None or any(_same_request(recorded, u)
                                   for u in (requested, *equivalent)):
            # The sidecar's encoding is the one the fetch decoded with, so a hit
            # returns the fetch's text on every platform. A sidecar without one
            # falls back to UTF-8 rather than the locale's code page.
            cached = (path.read_bytes() if binary else
                      path.read_bytes().decode(meta.get("encoding") or "utf-8",
                                               errors="replace"))
            wrong = validate(cached) if validate else None
            if wrong is None:
                log.debug("cache hit %s", path.name)
                return cached
            log.warning("cache %s/%s holds %s; fetching it again",
                        source, path.name, wrong)
        else:
            log.warning("cache %s/%s was fetched from %s, not %s; fetching it again",
                        source, path.name, recorded, requested)

    last = None
    for attempt in range(retries):
        try:
            r = session().get(url, params=query, timeout=timeout, headers=headers)
            r.raise_for_status()
            if binary:
                payload = r.content
            else:
                # requests guesses an encoding when the response declares no
                # charset. Fixing the guess here lets the sidecar record it.
                r.encoding = r.encoding or r.apparent_encoding
                payload = r.text
            wrong = validate(payload) if validate else None
            if wrong is not None:
                # Raise before the write, so the retry sees the server again
                # and the cache keeps no file the caller refuses.
                raise RuntimeError(f"server returned {wrong}")
            path.write_bytes(r.content)
            # Record what produced this file so provenance survives the download.
            # `url` is where the response came from after any redirect, and
            # `request_url` is what this call asked for, which the cache check
            # compares against.
            meta = {"url": r.url, "request_url": requested, "status": r.status_code,
                    "bytes": len(r.content),
                    "encoding": None if binary else r.encoding,
                    "retrieved": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            path.with_suffix(path.suffix + ".meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8")
            return payload
        except Exception as exc:  # noqa: BLE001 - report and retry
            last = exc
            log.warning("fetch failed (%s/%s) %s: %s", attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries} attempts: {url}") from last

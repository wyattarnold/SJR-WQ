"""Run the Tier 1 API harvests and write per-source interim files.

    python -m sjrwq.harvest              # all sources
    python -m sjrwq.harvest cdec usgs    # a subset

Each source writes data/interim/<source>_stations.gpkg and
data/interim/<source>_station_parameter.parquet. Raw responses are cached under
data/raw/<source>/, so re-running is nearly free.
"""

from __future__ import annotations

import logging
import math
import sys

import geopandas as gpd
import pandas as pd

from .config import INTERIM, ParameterLookup, domain_config, log
from .domain import boundary_buffers
from .sources import cdec, ceden, dwr_wdl, edi, esmr, usbr_rise, usgs, wqp


def _write(source: str, stations: gpd.GeoDataFrame, sp: pd.DataFrame,
           extra: dict[str, pd.DataFrame] | None = None) -> None:
    INTERIM.mkdir(parents=True, exist_ok=True)
    stations.to_file(INTERIM / f"{source}_stations.gpkg", layer="stations", driver="GPKG")
    sp.to_parquet(INTERIM / f"{source}_station_parameter.parquet", index=False)
    for name, df in (extra or {}).items():
        if df is not None and not df.empty:
            df.to_parquet(INTERIM / f"{source}_{name}.parquet", index=False)
    log.info("wrote interim files for %s (%d stations, %d station-parameter rows)",
             source, len(stations), len(sp))


def run_cdec() -> None:
    bbox = domain_config()["bbox"]
    stations, sp, notes = cdec.harvest(bbox)
    _write("cdec", stations, sp, {"notes": notes})


def run_usgs() -> None:
    bbox = domain_config()["bbox"]
    stations, sp = usgs.harvest(bbox)
    _write("usgs", stations, sp)


def run_ceden() -> None:
    bbox = domain_config()["bbox"]
    stations, sp = ceden.harvest(bbox)
    _write("ceden", stations, sp)


def run_dwr_wdl() -> None:
    bbox = domain_config()["bbox"]
    stations, sp = dwr_wdl.harvest(bbox)
    _write("dwr_wdl", stations, sp)


def run_usbr_rise() -> None:
    bbox = domain_config()["bbox"]
    stations, sp = usbr_rise.harvest(bbox)
    _write("usbr_rise", stations, sp)


def run_esmr() -> None:
    bbox = domain_config()["bbox"]
    stations, sp = esmr.harvest(bbox)
    _write("esmr", stations, sp)


def run_edi() -> None:
    # EDI needs no credential. Setting EDI_API_KEY or EDI_TOKEN switches
    # metadata retrieval from DataONE to PASTA, which is fresher by a revision
    # or two; see sources/edi.py.
    bbox = domain_config()["bbox"]
    stations, sp = edi.harvest(bbox)
    _write("edi", stations, sp)


def run_wqp() -> None:
    cfg = domain_config()
    # The Delta HUC is queried too: Vernalis and Mossdale sit inside it and are
    # the downstream boundary. Only the anchor neighborhood is requested,
    # because the study-area clip would discard the rest of the Delta anyway.
    hucs = sorted(cfg["huc8_in_scope"]) + ["18040003"]
    # The box is the extent of the anchor buffers, rounded outward, so it
    # contains every station `domain.classify_points` can flag boundary=True.
    # A radius converted to degrees on a sphere misses the east and west edges
    # of a buffer drawn in California Albers by a few meters.
    west, south, east, north = boundary_buffers().total_bounds
    delta_bbox = [math.floor(west * 1e4) / 1e4, math.floor(south * 1e4) / 1e4,
                  math.ceil(east * 1e4) / 1e4, math.ceil(north * 1e4) / 1e4]
    log.info("restricting HUC 18040003 to boundary bbox %s", delta_bbox)

    stations, sp = wqp.harvest(hucs, huc_bbox={"18040003": delta_bbox})
    _write("wqp", stations, sp)

    # What the station endpoint returned, and how much of it reports nothing
    # this inventory asks for. Both counts leave with the harvest, so the gap
    # analysis reads them here rather than typing them.
    if wqp.COUNTS:
        pd.DataFrame([wqp.COUNTS]).to_csv(INTERIM / "wqp_endpoint_counts.csv",
                                          index=False)

    # Never let a coverage gap pass silently. Anything the portal refused gets
    # written where the gap analysis will find it. A clean run deletes the file
    # rather than leaving it, because a stale one reports losses that a later
    # run already recovered.
    dropped_path = INTERIM / "wqp_dropped_characteristics.csv"
    if wqp.DROPPED:
        rows = [{"huc8": h, "characteristic": c}
                for h, cs in wqp.DROPPED.items() for c in sorted(set(cs))]
        pd.DataFrame(rows).to_csv(dropped_path, index=False)
        for huc, cs in wqp.DROPPED.items():
            log.warning("wqp %s: %d characteristics could not be retrieved: %s",
                        huc, len(set(cs)), ", ".join(sorted(set(cs))))
    elif dropped_path.exists():
        dropped_path.unlink()
        log.info("wqp: nothing dropped, removed the stale %s", dropped_path.name)


RUNNERS = {"cdec": run_cdec, "usgs": run_usgs, "wqp": run_wqp, "esmr": run_esmr,
           "usbr_rise": run_usbr_rise, "dwr_wdl": run_dwr_wdl,
           "ceden": run_ceden, "edi": run_edi}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    names = argv[1:] or list(RUNNERS)
    ParameterLookup.build()  # fail fast on a malformed parameters.yml
    failed = []
    for name in names:
        if name not in RUNNERS:
            log.error("unknown source %r; known: %s", name, ", ".join(RUNNERS))
            return 2
        log.info("=== harvesting %s ===", name)
        try:
            RUNNERS[name]()
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            log.exception("%s harvest failed: %s", name, exc)
            failed.append(name)
    if failed:
        log.error("failed sources: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

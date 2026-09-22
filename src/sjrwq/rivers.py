"""River centerlines from the NLDI, used for map context and river-mile positions.

    python -m sjrwq.rivers

Writes four layers into data/processed/domain.gpkg:

    mainstem           San Joaquin from Vernalis upstream to the headwaters
    tributaries        Stanislaus, Tuolumne, and Merced, each from its
                       headwaters down to where it meets the San Joaquin
    tributary_mouths   untrimmed downstream traces, used to locate confluences
    streams            background hydrography, third order and up, so the maps
                       look like a river basin rather than dots on a polygon

The mainstem doubles as the axis for the longitudinal coverage figure, which
measures station positions as distance along this line from Vernalis.
"""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import linemerge, substring

from .config import PROCESSED, domain_config, fetch, log
from .places import boundary_anchor

NLDI = "https://api.water.usgs.gov/nldi/linked-data/nwissite"
SOURCE = "nldi"

# Small-scale NHD, the already-generalized product meant for basin-wide context
# at a page-sized scale. The high-resolution service times out on a query this
# size and would draw every irrigation ditch.
NHD = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/4"
NHD_PAGE = 2000

# Third order and up. Order 2 nearly doubles the segment count to 14,771 and
# turns the valley floor into a gray wash; order 4 drops the smaller Sierra
# creeks that shape the headwaters.
MIN_STREAM_ORDER = 3

# This module navigates each river in both directions from a gage: upstream-main
# to the headwaters, and downstream-main far enough to reach the San Joaquin.
#
# Both directions matter. NLDI navigation starts at the gage, and the gages that
# make good anchors sit well inland: the Tuolumne gage is at Modesto, 20 km
# above the confluence, so an upstream-only trace draws a Tuolumne that stops in
# the middle of Modesto, short of the river it flows into.
#
# Values are (site, upstream_km, downstream_km).
NETWORKS = {
    "San Joaquin River": ("USGS-11303500", 560, 0),   # Vernalis
    "Stanislaus River": ("USGS-11303000", 200, 60),   # at Ripon
    "Tuolumne River": ("USGS-11290000", 220, 60),     # at Modesto
    "Merced River": ("USGS-11272500", 200, 60),       # near Stevinson
}

# Every tributary in NETWORKS joins the San Joaquin above Vernalis, so this list
# and NETWORKS minus the mainstem hold the same set. The two stay separate
# because confluences() has no meaning for a river that meets the San Joaquin
# below the modeled outlet.
CONTRIBUTING = ["Stanislaus River", "Tuolumne River", "Merced River"]


def _flowlines(site: str, distance_km: int, name: str,
               direction: str = "UM") -> gpd.GeoDataFrame:
    # The distance is part of the cache key. Without it, changing a navigation
    # distance in NETWORKS reuses the old download and the geometry stays put.
    txt = fetch(f"{NLDI}/{site}/navigation/{direction}/flowlines", SOURCE,
                params={"distance": distance_km},
                label=f"{direction.lower()}_{site}_{distance_km}km.json")
    gj = json.loads(txt)
    feats = gj.get("features") or []
    if not feats:
        raise RuntimeError(f"NLDI returned no {direction} flowlines for {site}")
    geoms = [shape(f["geometry"]) for f in feats]
    comids = [f["properties"].get("nhdplus_comid") for f in feats]
    log.info("nldi %s %s (%s): %d segments", name, direction, site, len(geoms))
    return gpd.GeoDataFrame({"river": name, "comid": comids}, geometry=geoms, crs="EPSG:4326")


def mainstem() -> gpd.GeoDataFrame:
    site, dist, _ = NETWORKS["San Joaquin River"]
    return _flowlines(site, dist, "San Joaquin River")


def tributaries(ms: gpd.GeoDataFrame | None = None,
                crs_area: str = "EPSG:3310", tol_m: float = 40.0) -> gpd.GeoDataFrame:
    """Each tributary from its headwaters down to the San Joaquin.

    Downstream navigation runs past the confluence, along the San Joaquin and,
    past Vernalis, out of the modeled reach. So this function truncates the
    trace at the first segment that lands on the mainstem. Filtering segment by
    segment is not enough: the mainstem line ends at Vernalis, so segments
    below it pass the distance test and the map draws the San Joaquin through
    Lathrop in Stanislaus colors.

    NLDI returns downstream navigation in order, which is what makes the
    truncation valid.
    """
    line = merged_mainstem_line(ms, crs_area) if ms is not None else None
    parts = []
    for name, (site, up_km, down_km) in NETWORKS.items():
        if name == "San Joaquin River":
            continue
        parts.append(_flowlines(site, up_km, name, "UM"))
        if not down_km:
            continue
        dm = _flowlines(site, down_km, name, "DM")
        if line is not None:
            mids = dm.to_crs(crs_area).geometry.map(
                lambda g: g.interpolate(0.5, normalized=True))
            on_mainstem = [i for i, m in enumerate(mids) if m.distance(line) <= tol_m]
            if on_mainstem:
                cut = on_mainstem[0]
                log.info("nldi %s: truncated downstream trace at the confluence, "
                         "dropping %d of %d segments", name, len(dm) - cut, len(dm))
                dm = dm.iloc[:cut]
        parts.append(dm)
    return pd.concat(parts, ignore_index=True).pipe(gpd.GeoDataFrame, crs="EPSG:4326")


def tributary_mouths() -> gpd.GeoDataFrame:
    """Untrimmed downstream reach from each contributing tributary's gage.

    `confluences` uses this. It keeps the segments that `tributaries` trims
    away, because the confluence is the point where a tributary's downstream
    path first coincides with the mainstem.

    This function navigates the contributing tributaries alone. A river that
    meets the San Joaquin below the modeled outlet has no confluence on this
    line, so a downstream trace from it misses the mainstem.
    """
    parts = [_flowlines(NETWORKS[n][0], NETWORKS[n][2], n, "DM") for n in CONTRIBUTING]
    return pd.concat(parts, ignore_index=True).pipe(gpd.GeoDataFrame, crs="EPSG:4326")


def confluences(crs_area: str = "EPSG:3310", tol_m: float = 60.0) -> dict:
    """Confluence coordinates, as the first point on each tributary's downstream
    path that touches the San Joaquin mainstem."""
    ms = gpd.read_file(PROCESSED / "domain.gpkg", layer="mainstem")
    mouths = gpd.read_file(PROCESSED / "domain.gpkg", layer="tributary_mouths")
    line = merged_mainstem_line(ms, crs_area)
    out = {}
    for river, grp in mouths.to_crs(crs_area).groupby("river"):
        best = None
        for geom in grp.geometry:
            for x, y in geom.coords:
                pt = Point(x, y)
                d = pt.distance(line)
                if d <= tol_m:
                    # The downstream path continues along the San Joaquin after
                    # it joins, so every point below the confluence also touches
                    # the mainstem. The confluence is the furthest UPSTREAM
                    # touch, i.e. the largest distance from Vernalis.
                    km = _km_from_vernalis(line, pt)
                    if best is None or km > best[0]:
                        best = (km, pt)
        if best is None:
            log.warning("no confluence found for %s within %.0f m", river, tol_m)
            continue
        wgs = gpd.GeoSeries([best[1]], crs=crs_area).to_crs("EPSG:4326").iloc[0]
        out[f"{river} confluence"] = (round(wgs.y, 5), round(wgs.x, 5))
        log.info("%s confluence at %.1f km upstream of Vernalis", river, best[0])
    return out


def _vernalis(crs_area: str) -> Point:
    """The Vernalis boundary anchor from config/domain.yml, in crs_area."""
    lat, lon = boundary_anchor("Vernalis")
    return gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(crs_area).iloc[0]


def merged_mainstem_line(gdf: gpd.GeoDataFrame, crs_area: str = "EPSG:3310"):
    """Single line for the San Joaquin, starting at Vernalis and running upstream.

    Distance along the returned line is distance upstream from Vernalis. The
    function orients the line by which end lies nearer the Vernalis boundary
    anchor in config/domain.yml, then cuts the line at the point nearest that
    anchor. NLDI upstream navigation returns the whole flowline that holds the
    Vernalis gage, and that flowline continues below the gage, so the uncut
    line starts downstream of Vernalis.
    """
    merged = linemerge(gdf.to_crs(crs_area).geometry.union_all())
    if merged.geom_type == "MultiLineString":
        # Keep the longest continuous run; NHD braiding leaves small orphans.
        merged = max(merged.geoms, key=lambda g: g.length)
    vernalis = _vernalis(crs_area)
    first, last = Point(merged.coords[0]), Point(merged.coords[-1])
    if last.distance(vernalis) < first.distance(vernalis):
        merged = merged.reverse()
    return substring(merged, merged.project(vernalis), merged.length)


def _km_from_vernalis(line, pt) -> float:
    return line.project(pt) / 1000.0


# How close a station has to be to a mapped channel before this inventory names
# it for that channel or puts it on the mainstem axis. Agency coordinates are
# good to a few tens of meters and NLDI flowlines are generalized, so 500 m is
# generous without being loose.
#
# One tolerance for both. Naming a station for the San Joaquin and giving it a
# river kilometer are the same claim, so `inventory` names the river and
# `river_distance_km` places a station on the axis at this one distance. A
# second value would put wells, canals, and outfalls on the mainstem axis with
# a null river name.
ON_CHANNEL_M = 500.0


def river_distance_km(points: gpd.GeoDataFrame, line, crs_area: str = "EPSG:3310",
                      max_offset_m: float = ON_CHANNEL_M) -> pd.Series:
    """Distance along the mainstem, in km upstream from Vernalis.

    `line` comes from `merged_mainstem_line`, which starts at Vernalis. The
    function leaves null each point further than max_offset_m from the line:
    such a point sits on a tributary or a canal rather than the mainstem, and
    a position on this axis would mislead. A point below Vernalis projects onto
    the start of the line, so it takes km 0 when it lies within max_offset_m of
    that start.
    """
    proj = points.to_crs(crs_area)
    out = []
    for geom in proj.geometry:
        if geom is None or geom.is_empty:
            out.append(None)
            continue
        offset = geom.distance(line)
        if offset > max_offset_m:
            out.append(None)
            continue
        out.append(line.project(geom) / 1000.0)
    return pd.Series(out, index=points.index, dtype="float64")


def streams(bbox: list[float], min_order: int = MIN_STREAM_ORDER) -> gpd.GeoDataFrame:
    """Background hydrography for the map base.

    Paged because the service caps a response at 2,000 features and the basin
    holds about 8,500 at third order and up. Each page is cached separately, so
    an interrupted run picks up where it stopped.
    """
    where = f"StreamOrde >= {min_order} AND FTYPE <> 'Coastline'"
    feats: list[dict] = []
    offset = 0
    while True:
        txt = fetch(f"{NHD}/query", "nhd", params={
            "where": where,
            "geometry": ",".join(str(b) for b in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "GNIS_NAME,StreamOrde,LENGTHKM",
            "returnGeometry": "true", "f": "geojson",
            "resultOffset": offset, "resultRecordCount": NHD_PAGE,
        }, label=f"streams_o{min_order}_{offset}.json", timeout=300)
        page = json.loads(txt).get("features", [])
        feats.extend(page)
        if len(page) < NHD_PAGE:
            break
        offset += NHD_PAGE

    gdf = gpd.GeoDataFrame(
        pd.DataFrame({
            "name": [f["properties"].get("GNIS_NAME") for f in feats],
            "stream_order": [f["properties"].get("StreamOrde") for f in feats],
            "length_km": [f["properties"].get("LENGTHKM") for f in feats],
        }),
        geometry=[shape(f["geometry"]) for f in feats], crs="EPSG:4326")
    log.info("streams: %d segments, order %d and up", len(gdf), min_order)
    return gdf


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = PROCESSED / "domain.gpkg"
    ms = mainstem()
    tribs = tributaries(ms)
    ms.to_file(out, layer="mainstem", driver="GPKG")
    tribs.to_file(out, layer="tributaries", driver="GPKG")
    tributary_mouths().to_file(out, layer="tributary_mouths", driver="GPKG")
    streams(domain_config()["bbox"]).to_file(out, layer="streams", driver="GPKG")
    line = merged_mainstem_line(ms)
    log.info("mainstem merged length = %.1f km", line.length / 1000.0)
    for name, (lat, lon) in confluences().items():
        log.info("%s at %.5f, %.5f", name, lat, lon)
    log.info("wrote mainstem, tributaries, tributary_mouths, and streams to %s", out)


if __name__ == "__main__":
    main()

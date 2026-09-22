"""Build the study area from the Watershed Boundary Dataset.

Run directly to write data/processed/domain.gpkg:

    python -m sjrwq.domain
"""

from __future__ import annotations

import json
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

from .config import PROCESSED, domain_config, fetch, log

WGS84 = "EPSG:4326"


def _query_wbd(layer: int, where: str, source: str, label: str) -> gpd.GeoDataFrame:
    cfg = domain_config()
    url = f"{cfg['wbd_service']}/{layer}/query"
    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    txt = fetch(url, source=source, params=params, label=label)
    gj = json.loads(txt)
    feats = gj.get("features", [])
    if not feats:
        raise RuntimeError(f"WBD query returned no features: {where}")
    rows, geoms = [], []
    for f in feats:
        rows.append({k.lower(): v for k, v in (f.get("properties") or {}).items()})
        geoms.append(shape(f["geometry"]))
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs=WGS84)
    return gdf


def load_huc8() -> gpd.GeoDataFrame:
    """All HUC8s in region 1804, tagged with in_scope."""
    cfg = domain_config()
    gdf = _query_wbd(
        cfg["wbd_layers"]["huc8"],
        f"huc8 LIKE '{cfg['region']}%'",
        source="wbd",
        label="huc8_1804.geojson",
    )
    in_scope = set(cfg["huc8_in_scope"])
    gdf["in_scope"] = gdf["huc8"].isin(in_scope)
    return gdf.sort_values("huc8").reset_index(drop=True)


# How far beyond the study area to fetch neighboring subbasins, in degrees.
# The map frame is the AOI padded by 0.06 deg; this is an order of magnitude
# more, so the surround reaches the frame edge on every side with room to spare.
SURROUND_PAD_DEG = 0.6

# Tolerance for the surround geometry. The maps draw it as a pale fill behind
# the data, so detail finer than this is wasted.
SURROUND_TOLERANCE_DEG = 0.002


def load_surround(pad: float = SURROUND_PAD_DEG) -> gpd.GeoDataFrame:
    """HUC8s around the study area, for map context.

    `load_huc8` fetches region 1804 alone, which leaves the map with land to the
    north and east and white space to the south and west: the Kings and the
    Tulare basins are region 1803, the Pajaro is 1806, and the east side of the
    Sierra crest is 1809. Fetched by envelope rather than by region prefix, so
    a neighbor arrives regardless of its region number.
    """
    cfg = domain_config()
    aoi = build_aoi().total_bounds
    box = [round(aoi[0] - pad, 3), round(aoi[1] - pad, 3),
           round(aoi[2] + pad, 3), round(aoi[3] + pad, 3)]
    url = f"{cfg['wbd_service']}/{cfg['wbd_layers']['huc8']}/query"
    txt = fetch(url, source="wbd", label="huc8_surround.geojson", params={
        "where": "1=1",
        "geometry": ",".join(str(b) for b in box),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "huc8,name",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    })
    feats = json.loads(txt).get("features", [])
    if not feats:
        raise RuntimeError("WBD returned no surrounding HUC8s")
    gdf = gpd.GeoDataFrame(
        pd.DataFrame([{k.lower(): v for k, v in (f.get("properties") or {}).items()}
                      for f in feats]),
        geometry=[shape(f["geometry"]) for f in feats], crs=WGS84)
    gdf["in_scope"] = gdf["huc8"].isin(set(cfg["huc8_in_scope"]))
    gdf["geometry"] = gdf.geometry.simplify(SURROUND_TOLERANCE_DEG)
    log.info("surround: %d HUC8s across regions %s", len(gdf),
             ", ".join(sorted({h[:4] for h in gdf["huc8"]})))
    return gdf.sort_values("huc8").reset_index(drop=True)


def load_huc12() -> gpd.GeoDataFrame:
    """HUC12 subwatersheds inside the in-scope HUC8s.

    Fetched one HUC8 at a time; the whole region in one call exceeds the
    service's feature transfer limit.
    """
    cfg = domain_config()
    layer = cfg["wbd_layers"]["huc12"]
    parts = []
    for huc8 in sorted(cfg["huc8_in_scope"]):
        log.info("WBD huc12 for %s", huc8)
        parts.append(
            _query_wbd(layer, f"huc12 LIKE '{huc8}%'", "wbd", f"huc12_{huc8}.geojson")
        )
    return pd.concat(parts, ignore_index=True).pipe(
        gpd.GeoDataFrame, crs=WGS84
    ).sort_values("huc12").reset_index(drop=True)


def boundary_buffers() -> gpd.GeoDataFrame:
    """Circular buffers around the downstream boundary anchors.

    Vernalis and Mossdale both fall inside HUC 18040003, which is otherwise
    excluded. These buffers carve them back in.
    """
    cfg = domain_config()
    anchors = cfg["boundary_anchors"]
    gdf = gpd.GeoDataFrame(
        pd.DataFrame(anchors),
        geometry=gpd.points_from_xy([a["lon"] for a in anchors], [a["lat"] for a in anchors]),
        crs=WGS84,
    ).to_crs(cfg["crs"]["area"])
    gdf["geometry"] = gdf.buffer(gdf["buffer_km"] * 1000.0)
    return gdf.to_crs(WGS84)


# The state outline, for the locator inset on the map figures. TIGERweb is the
# Census Bureau's public boundary service; it needs no key and returns one
# MultiPolygon for California in WGS84.
TIGERWEB = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
            "/State_County/MapServer/0/query")

# Tolerance for the state outline, in degrees. The inset is about 25 mm wide, so
# roughly 500 m of detail; simplifying at that scale takes the file from 494 kB
# to a few tens of kB with no visible difference.
STATE_TOLERANCE_DEG = 0.005


def load_state(name: str = "California") -> gpd.GeoDataFrame:
    """State outline, used only as the locator inset on the map figures."""
    txt = fetch(TIGERWEB, source="census", params={
        "where": f"NAME='{name}'",
        "outFields": "NAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }, label=f"{name.lower()}.geojson")
    feats = json.loads(txt).get("features", [])
    if not feats:
        raise RuntimeError(f"TIGERweb returned no geometry for {name}")
    gdf = gpd.GeoDataFrame(
        {"name": [name]}, geometry=[shape(feats[0]["geometry"])], crs=WGS84)
    gdf["geometry"] = gdf.geometry.simplify(STATE_TOLERANCE_DEG)
    return gdf


def build_aoi(huc8: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Study area polygon: in-scope HUC8s plus the boundary anchor buffers."""
    huc8 = load_huc8() if huc8 is None else huc8
    core = unary_union(huc8.loc[huc8["in_scope"], "geometry"].values)
    anchors = unary_union(boundary_buffers().geometry.values)
    aoi = unary_union([core, anchors])
    return gpd.GeoDataFrame(
        {"name": ["San Joaquin study area"], "note": ["HUC 1804 above Vernalis + boundary buffers"]},
        geometry=[aoi],
        crs=WGS84,
    )


def classify_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Tag point stations with huc8, huc12, in_aoi, and boundary flags.

    A station is `boundary=True` when it falls outside the in-scope HUC8s but
    inside one of the anchor buffers, i.e. it is a downstream boundary site
    rather than an interior observation.
    """
    if gdf.empty:
        for col in ("huc8", "huc12", "in_aoi", "boundary", "anchor"):
            gdf[col] = None
        return gdf

    huc8 = load_huc8()[["huc8", "name", "in_scope", "geometry"]].rename(
        columns={"name": "huc8_name"}
    )
    huc12 = load_huc12()[["huc12", "name", "geometry"]].rename(columns={"name": "huc12_name"})
    anchors = boundary_buffers()[["name", "geometry"]].rename(columns={"name": "anchor"})

    pts = gdf.to_crs(WGS84).copy()
    pts = gpd.sjoin(pts, huc8, how="left", predicate="within").drop(columns="index_right")
    pts = gpd.sjoin(pts, huc12, how="left", predicate="within").drop(columns="index_right")

    # The anchor buffers overlap: Vernalis and Mossdale sit 12.7 km apart with an
    # 8 km radius each, so 5 stations fall inside both. A plain spatial join
    # returns one row per buffer, which puts those 5 into the inventory twice
    # and into the first bar of the funnel twice. One row per station, naming
    # every anchor it falls inside.
    hit = gpd.sjoin(pts[["geometry"]], anchors, how="inner", predicate="within")
    named = hit.groupby(level=0)["anchor"].agg(lambda s: ", ".join(sorted(set(s))))
    pts["anchor"] = named.reindex(pts.index)

    pts["in_scope"] = pts["in_scope"].fillna(False).astype(bool)
    pts["boundary"] = (~pts["in_scope"]) & pts["anchor"].notna()
    pts["in_aoi"] = pts["in_scope"] | pts["boundary"]
    return pts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out = PROCESSED / "domain.gpkg"

    huc8 = load_huc8()
    huc12 = load_huc12()
    aoi = build_aoi(huc8)
    anchors = boundary_buffers()

    huc8.to_file(out, layer="huc8", driver="GPKG")
    huc12.to_file(out, layer="huc12", driver="GPKG")
    aoi.to_file(out, layer="aoi", driver="GPKG")
    anchors.to_file(out, layer="boundary_anchors", driver="GPKG")
    # Context only. A failure in either costs a background layer, so neither
    # stops the domain build.
    for name, load in (("state", load_state), ("surround", load_surround)):
        try:
            load().to_file(out, layer=name, driver="GPKG")
        except Exception as exc:  # noqa: BLE001 - map context is optional
            log.warning("no %s layer, the maps will do without it: %s", name, exc)

    area_km2 = aoi.to_crs(domain_config()["crs"]["area"]).area.iloc[0] / 1e6
    in_scope = huc8[huc8["in_scope"]]
    log.info("HUC8 total=%d in-scope=%d", len(huc8), len(in_scope))
    log.info("HUC12 in-scope=%d", len(huc12))
    log.info("AOI area = %.0f km2 (sum of in-scope HUC8 areasqkm = %.0f)",
             area_km2, in_scope["areasqkm"].astype(float).sum())
    log.info("wrote %s", out)


if __name__ == "__main__":
    main()

"""Static figure set for the inventory.

    python -m sjrwq.maps

Every figure renders from data/processed alone, with no network access, so the
figures regenerate offline and stay put when a basemap tile server changes.

Two conventions run through the map figures. Discrete sampling sites go down
first and faint, continuous records on top and solid, because a temperature
model calibrates against a continuous record rather than against a series of
samples. And on every figure that draws a size legend, marker area scales with
the square root of record length: lengths run from one year to well over a
century, and a linear scale lets a single old gage dominate the map.

Each figure has a legend and axis labels and no title. The title goes on the
slide that shows the figure. The explanatory prose lives in docs/figures.md,
which this module writes from the same run that draws the PNGs, so a caption
and its figure hold the same counts.
"""

from __future__ import annotations

import logging
import textwrap

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.patheffects import withStroke
from shapely.geometry import Point

from . import coverage
from .config import DOCS, FIGURES, PROCESSED, domain_config, log
from .coverage import merged_spans, station_level
from .inventory import OFF_CHANNEL_CLASSES
from .places import RESERVOIR_LABEL, RESERVOIRS, TIMELINE_ANCHORS, TOWNS
from .rivers import ON_CHANNEL_M, merged_mainstem_line, river_distance_km

# Agg draws to a file with no window. The Tk backend, the Windows default,
# loads its Tcl library for each figure, and under uv's managed Python that
# load fails for some figures and not others.
mpl.use("Agg")

# Arial, at 9 pt or smaller. Matplotlib falls back down this list in
# order, so a machine without Arial gets Helvetica and then the shipped default
# rather than a findfont warning and an unpredictable substitute.
MAX_PT = 9.0

mpl.rcParams.update({
    "figure.dpi": 160,
    "savefig.dpi": 320,
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "font.size": 8.0,
    "axes.titlesize": MAX_PT,
    "figure.titlesize": MAX_PT,
    # Arial ships 400 and 700. Asking for "semibold" or "medium" logs a
    # findfont warning and returns 700 regardless, so the weights here are
    # literal.
    "axes.titleweight": "bold",
    "figure.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    # Matplotlib's default greys wash out at print resolution on a white page.
    # Text and axis furniture sit at near-black so the type stays legible at
    # print size.
    "text.color": "#16150F",
    "axes.labelcolor": "#16150F",
    "xtick.color": "#16150F",
    "ytick.color": "#16150F",
    "axes.edgecolor": "#16150F",
})

# Captions live in docs/figures.md, not on the figures. Each figure appends
# one entry here as it renders and `write_captions` writes the file, so a
# figure and its caption cannot drift apart.
CAPTIONS: list[dict] = []

# Hydrography is a lighter, cooler blue than the station markers. Closer than
# this and a USGS dot on the Tuolumne passes for part of the river.
WATER = "#5FA0BE"
STREAM = "#A7C8D8"
# Hydrography outside the study area, drawn so the basin sits in a landscape
# rather than on a white page. Pale enough to stay behind a station marker and
# behind the in-basin streams.
STREAM_OUT = "#CFDDE5"
LAND = "#F3F1EC"
LAND_OUT = "#FAF9F7"
EDGE = "#A79F94"
AOI_EDGE = "#2E2A24"
INK = "#16150F"
MUTED = "#4F493F"

# All eight harvested sources have a color. Without one a source falls through
# to gray, and gray is also the record-type key on the same legend, which
# leaves an unnamed source and a continuous record looking the same.
SOURCE_COLOR = {"usgs": "#123C8C", "cdec": "#D2570A", "wqp": "#12703C",
                "esmr": "#6A2D91", "ceden": "#17A9C4", "dwr_wdl": "#B0184A",
                "usbr_rise": "#8A6D1F", "edi": "#7A4E2D"}
SOURCE_LABEL = {"usgs": "USGS", "cdec": "CDEC", "wqp": "WQP", "esmr": "eSMR",
                "ceden": "CEDEN", "dwr_wdl": "DWR WDL", "usbr_rise": "USBR RISE",
                "edi": "EDI"}
OTHER_COLOR = "#6E6E6E"
# Subbasin labels worth drawing, and what to call them. The WBD names are long
# enough to run into each other on a page-sized map, and the Sierra ones sit
# right where the stations are densest.
LABELED_SUBBASINS = {
    "Lower San Joaquin River": "Lower San Joaquin",
    "Middle San Joaquin-Lower Chowchilla": "Middle San Joaquin",
    "Upper San Joaquin": "Upper San Joaquin",
    "Panoche-San Luis Reservoir": "Panoche-San Luis",
}

# Time series figures stop here rather than at today, so a partial year of
# reporting stays out of a chart of complete ones. Computed, because a figure
# whose right edge is a typed year goes quietly wrong on the next new year.
LAST_COMPLETE_YEAR = pd.Timestamp.today().year - 1

# And they start here. Discharge on this river was gaged from the 1890s, and a
# common floor at the earliest record leaves the temperature and conductance
# panels four fifths empty for the sake of a handful of long flow gages. A
# record that starts earlier is clipped to this year and marked with an arrow
# pointing off the axis.
FIRST_YEAR = 1950

# Legend keys for the record-length size ramp, in years.
SIZE_KEYS = [5, 25, 100]

# Chart fill for figures with no map under them, kept away from WATER so the
# hydrography color stays out of the data palette.
BAR = "#3E6E92"

HALO = [withStroke(linewidth=2.6, foreground="white", alpha=0.95)]
# Subbasin names sit under the station layer, so they need more of a halo than
# a label drawn on empty ground does.
HEAVY_HALO = [withStroke(linewidth=4.0, foreground="white", alpha=0.9)]


def _basin_streams(dom):
    """Background hydrography, split into in-basin and surrounding.

    The streams layer is fetched over a bounding box, so it arrives with the
    Kings, the Mokelumne, and the San Benito. At one weight those look like
    part of the study area and pull the eye off the San Joaquin. At two, the
    surrounding drainage sets the basin in a landscape: over half the layer's
    segments lie outside the in-scope HUC8s and fill the frame.
    """
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    try:
        streams = gpd.read_file(dom, layer="streams")
    except Exception:  # noqa: BLE001 - the layer predates this and may be absent
        log.warning("no streams layer in domain.gpkg; run sjrwq.rivers to add it")
        return empty, empty
    huc8 = gpd.read_file(dom, layer="huc8")
    basin = huc8[huc8["in_scope"].astype(bool)].union_all()
    inside = streams.clip(basin)
    outside = streams[~streams.intersects(basin)]
    return inside, outside


def _optional(dom, layer):
    """A background layer written by sjrwq.domain, or None if it is absent.

    `state` draws the locator inset and `surround` the land around the study
    area. Both are context: a checkout that has not re-run sjrwq.domain still
    renders every figure, with a warning and without them.
    """
    try:
        return gpd.read_file(dom, layer=layer)
    except Exception:  # noqa: BLE001 - optional; written by sjrwq.domain
        log.warning("no %s layer in domain.gpkg; run sjrwq.domain to add it", layer)
        return None


def _load():
    dom = PROCESSED / "domain.gpkg"
    st = gpd.read_file(PROCESSED / "stations.gpkg", layer="stations")
    layers = {
        "huc8": gpd.read_file(dom, layer="huc8"),
        "huc12": gpd.read_file(dom, layer="huc12"),
        "aoi": gpd.read_file(dom, layer="aoi"),
        "mainstem": gpd.read_file(dom, layer="mainstem"),
        "tributaries": gpd.read_file(dom, layer="tributaries"),
        "state": _optional(dom, "state"),
        "surround": _optional(dom, "surround"),
    }
    layers["streams"], layers["streams_out"] = _basin_streams(dom)
    sp = pd.read_parquet(PROCESSED / "station_parameter.parquet")
    for c in ("por_start", "por_end"):
        sp[c] = pd.to_datetime(sp[c], errors="coerce")
    return st, sp, layers


def _marker_size(years) -> np.ndarray:
    return 6 + 4.2 * np.sqrt(np.asarray(years, dtype=float))


def _scalebar(ax, km: int = 50, fx: float = 0.03, fy: float = 0.955):
    """Kilometre bar, anchored in axes fractions.

    Top left by default: the station legends sit bottom left and bottom right
    on every map here, and a scale bar underneath one of them is worse than no
    scale bar. Longitude degrees shrink with latitude, so the bar width is
    computed at the middle of the drawn extent rather than assumed.
    """
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    lat = (y0 + y1) / 2
    deg = km / (111.32 * np.cos(np.deg2rad(lat)))
    bx = x0 + (x1 - x0) * fx
    by = y0 + (y1 - y0) * fy
    h = (y1 - y0) * 0.006
    ax.add_patch(Rectangle((bx, by), deg, h, facecolor=INK, edgecolor="none", zorder=8))
    ax.add_patch(Rectangle((bx, by), deg / 2, h, facecolor="white",
                           edgecolor=INK, linewidth=0.4, zorder=9))
    ax.text(bx + deg / 2, by + h * 2.2, f"{km} km", ha="center", va="bottom",
            fontsize=6, color=INK, zorder=9)


# Where the locator inset goes, in axes fractions, keyed by corner. California
# is tall and narrow, so the box is taller than it is wide.
INSET_BOXES = {
    "upper right": [0.795, 0.700, 0.195, 0.290],
    "upper left": [0.015, 0.700, 0.195, 0.290],
    "lower right": [0.795, 0.020, 0.195, 0.290],
}


def _inset_locator(ax, layers, loc="upper right"):
    """Where the study area sits in California.

    Every map figure here is cropped to the basin, which places only a reader
    who already knows the San Joaquin. The inset costs a corner the data does
    not reach.

    Built with `ax.inset_axes` rather than the axes_grid1 helper, which
    `tight_layout` cannot measure and therefore lays out by chance.
    """
    state = layers.get("state")
    if state is None or state.empty:
        return None
    inset = ax.inset_axes(INSET_BOXES.get(loc, INSET_BOXES["upper right"]),
                          zorder=9)
    state.plot(ax=inset, facecolor=LAND, edgecolor=EDGE, linewidth=0.45)
    layers["aoi"].plot(ax=inset, facecolor="#B01B23", edgecolor="#B01B23",
                       linewidth=0.4, alpha=0.9)
    b = state.total_bounds
    inset.set_xlim(b[0] - 0.15, b[2] + 0.15)
    inset.set_ylim(b[1] - 0.15, b[3] + 0.15)
    inset.set_aspect(MAP_ASPECT)
    inset.set_xticks([]); inset.set_yticks([])
    inset.patch.set_alpha(0.0)
    for side in inset.spines.values():
        side.set_visible(False)
    return inset


def _annotate_places(ax, towns=True, reservoirs=True):
    if towns:
        for name, (lat, lon) in TOWNS.items():
            ax.plot(lon, lat, marker="s", ms=2.8, color=INK, zorder=7)
            ax.annotate(name, (lon, lat), xytext=(3.8, 1.5),
                        textcoords="offset points", fontsize=6.4, color=INK,
                        zorder=7, path_effects=HALO)
    if reservoirs:
        for name, (lat, lon) in RESERVOIRS.items():
            ax.plot(lon, lat, marker="D", ms=3.0, color=WATER,
                    markeredgecolor="white", markeredgewidth=0.4, zorder=7)
            ax.annotate(RESERVOIR_LABEL.get(name, name), (lon, lat),
                        xytext=(4, -5), textcoords="offset points",
                        fontsize=6, color=WATER, style="italic",
                        zorder=7, path_effects=HALO)


# How far inside a subbasin its label has to sit, in degrees. Maximising the
# distance to the nearest station alone pushes every label to a polygon
# extremity, where half of it hangs over the neighboring basin or off the
# frame.
LABEL_INSET_DEG = 0.12


def _vertices(geom, step: int = 4):
    """Every `step`-th vertex of a line or multiline, as (x, y) pairs."""
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    for part in parts:
        coords = list(getattr(part, "coords", []))
        yield from coords[::step]


def _clear_point(poly, lons, lats, n=17, inset=LABEL_INSET_DEG):
    """A point well inside `poly` and as far as possible from the stations.

    The subbasin names had no placement rule and landed wherever the polygon's
    representative point fell, which on this basin is the valley floor, which is
    where the stations are. "Lower San Joaquin" and "Upper San Joaquin" were
    both sitting under markers.
    """
    inner = poly.buffer(-inset)
    if inner.is_empty:
        inner = poly
    minx, miny, maxx, maxy = inner.bounds
    xs = np.linspace(minx, maxx, n + 2)[1:-1]
    ys = np.linspace(miny, maxy, n + 2)[1:-1]
    best, best_d = inner.representative_point(), -1.0
    pts = np.column_stack([np.asarray(lons, float), np.asarray(lats, float)])
    for x in xs:
        for y in ys:
            c = Point(x, y)
            if not inner.contains(c):
                continue
            d = (np.hypot(pts[:, 0] - x, pts[:, 1] - y).min()
                 if len(pts) else np.inf)
            if d > best_d:
                best, best_d = c, d
    return best


def _base(ax, layers, label_huc=False, places=True, inset=True,
          inset_loc="upper right"):
    huc8 = layers["huc8"]
    inside = huc8[huc8["in_scope"].astype(bool)]
    # Land around the study area, so the basin sits in a landscape rather than
    # on a white page. The huc8 layer holds region 1804 alone, which leaves the
    # Kings, the Tulare basins, and the Pajaro as white space on three sides;
    # the surround layer is fetched by envelope and reaches the frame edge.
    around = layers.get("surround")
    if around is None or around.empty:
        around = huc8
    around[~around["in_scope"].astype(bool)].plot(
        ax=ax, facecolor=LAND_OUT, edgecolor=EDGE, linewidth=0.3,
        linestyle=":", zorder=0)
    # Hydrography outside the basin first and faintest, then the study area,
    # then the in-basin streams in two weights so the drainage pattern shows
    # without competing with the modeled rivers.
    out = layers.get("streams_out")
    if out is not None and not out.empty:
        out.plot(ax=ax, color=STREAM_OUT, linewidth=0.18, alpha=0.7, zorder=0.2)
    inside.plot(ax=ax, facecolor=LAND, edgecolor=EDGE, linewidth=0.4, zorder=0.3)
    streams = layers.get("streams")
    if streams is not None and not streams.empty:
        small = streams[streams["stream_order"] < 5]
        large = streams[streams["stream_order"] >= 5]
        small.plot(ax=ax, color=STREAM, linewidth=0.22, alpha=0.55, zorder=0.5)
        large.plot(ax=ax, color=STREAM, linewidth=0.45, alpha=0.8, zorder=0.6)
    layers["aoi"].plot(ax=ax, facecolor="none", edgecolor=AOI_EDGE,
                       linewidth=0.7, zorder=1)
    layers["tributaries"].plot(ax=ax, color=WATER, linewidth=1.0, alpha=0.95, zorder=2)
    layers["mainstem"].plot(ax=ax, color=WATER, linewidth=2.0, zorder=3)
    if label_huc is not False:
        # Only the valley subbasins are labeled. The headwater names have
        # nowhere to go: "Upper Merced" lands on the Don Pedro marker and
        # "Upper Tuolumne" on New Melones, and both sit over the densest band
        # of stations on the map. The reservoir labels already name those
        # basins by their rivers.
        lons, lats = ([], []) if label_huc is True else label_huc
        # The rivers are obstacles too. "Upper San Joaquin" sat on the mainstem
        # and on a station marker, which is the placement `_clear_point` exists
        # to prevent, because only the stations were passed to it.
        lons, lats = list(lons), list(lats)
        for key in ("mainstem", "tributaries"):
            layer = layers.get(key)
            if layer is None or layer.empty:
                continue
            for geom in layer.geometry:
                for x, y in _vertices(geom):
                    lons.append(x)
                    lats.append(y)
        for _, r in inside.iterrows():
            name = str(r["name"]).removesuffix(" California")
            if name not in LABELED_SUBBASINS:
                continue
            c = _clear_point(r.geometry, lons, lats)
            ax.annotate(LABELED_SUBBASINS[name], (c.x, c.y), fontsize=6,
                        ha="center", color=MUTED, zorder=2,
                        path_effects=HEAVY_HALO)
    if places:
        _annotate_places(ax)
    _frame(ax, layers, inset=inset, inset_loc=inset_loc)


# The map frame. `_frame` sets the drawn extent from these two and
# `_map_shape` sizes the figure from the same two, so every map figure has one
# extent and one pixel size. The pad is the margin around the study area in
# degrees. A degree of longitude at 37.4 N, the middle of the study area, is
# cos(37.4) of a degree of latitude, so the y axis stretches by the inverse.
MAP_PAD_DEG = 0.06
MAP_ASPECT = 1 / np.cos(np.deg2rad(37.4))


def _frame(ax, layers, inset=True, inset_loc="upper right"):
    """Extent, aspect, scale bar, and locator inset, shared by every map."""
    aoi = layers["aoi"].total_bounds
    ax.set_xlim(aoi[0] - MAP_PAD_DEG, aoi[2] + MAP_PAD_DEG)
    ax.set_ylim(aoi[1] - MAP_PAD_DEG, aoi[3] + MAP_PAD_DEG)
    ax.set_aspect(MAP_ASPECT)
    ax.set_xticks([]); ax.set_yticks([])
    for side in ax.spines.values():
        side.set_visible(False)
    _scalebar(ax)
    if inset:
        _inset_locator(ax, layers, loc=inset_loc)


def _size_legend(ax, loc="lower right", title="record length", keys=None):
    """Key for the marker-area ramp.

    `keys` covers the range the figure draws. A map of records measured inside
    a 26-year window has no 100-year marker on it, and a key showing one reads
    the whole ramp wrong.

    Called last on purpose. Matplotlib keeps one legend per axes unless a
    caller re-attaches the earlier ones with add_artist, so each caller
    attaches its own legend first and leaves this one as the live `ax.legend_`.
    """
    handles = [Line2D([], [], marker="o", linestyle="", color=MUTED,
                      markersize=np.sqrt(_marker_size(y)), alpha=0.8,
                      label=f"{y} yr") for y in (keys or SIZE_KEYS)]
    return ax.legend(handles=handles, loc=loc, frameon=False, fontsize=6,
                     title=title, title_fontsize=6, labelspacing=0.9,
                     borderpad=0.8, handletextpad=1.0)


# --- figures -----------------------------------------------------------------

def fig_overview(st, sp, layers):
    """Where the stations are, split by what kind of record each one holds.

    Every station drawn here reports at least one target parameter. The Water
    Quality Portal returns every monitoring location in a HUC from its station
    endpoint, regardless of what it measures, and keeping that difference would
    put every one of them on this map with no parameter attached, so
    `sources/wqp.py` drops them at the harvest and logs the count, which
    `docs/gap_analysis.md` reports.

    The legend sits inside the axes rather than below them. An axes legend
    counts inside the axes' tight bounding box, so a wide legend under the map
    makes `tight_layout` squeeze the map to the legend's width.
    """
    fig, ax = _map_figure(layers)
    prim = st[st["is_primary"]].copy()

    # Continuity from the per-parameter record type, matching gaps.py and the
    # other figures. Deciding it from the station type instead disagrees,
    # because a source reports the instrument it installed rather than what
    # each series came off.
    cont_ids = set(sp.loc[sp["record_type"] == "continuous", "station_uid"])
    prim["cont"] = prim["station_uid"].isin(cont_ids)

    _base(ax, layers, label_huc=(prim["lon"], prim["lat"]))

    is_well = prim["channel_class"] == "groundwater"
    well = prim[is_well]
    disc = prim[~is_well & ~prim["cont"]]
    cont = prim[~is_well & prim["cont"]]

    ax.scatter(well["lon"], well["lat"], s=3.0, c="#9A8F7C", alpha=0.6,
               linewidths=0, zorder=4,
               label=f"groundwater well ({len(well):,})")
    ax.scatter(disc["lon"], disc["lat"], s=6, c=MUTED, alpha=0.7,
               linewidths=0, zorder=5,
               label=f"surface water, discrete samples ({len(disc):,})")
    ax.scatter(cont["lon"], cont["lat"], s=17, c="#B01B23", alpha=0.95,
               linewidths=0.3, edgecolor="white", zorder=6.5,
               label=f"surface water, continuous monitor ({len(cont):,})")

    # The boundary stations sit inside two 8 km buffers around Vernalis and
    # Mossdale, outside the in-scope HUC8s. The buffer outline goes on the map
    # so the cluster shows as a boundary condition rather than a dense
    # subbasin.
    anchors = gpd.read_file(PROCESSED / "domain.gpkg", layer="boundary_anchors")
    anchors.plot(ax=ax, facecolor="none", edgecolor="#B01B23", linewidth=1.1,
                 linestyle="--", zorder=6)
    # Below and to the right of the buffers. Stockton sits immediately north
    # of them, so a label above the pair lands on the town name, and the
    # buffers sit against the left edge of the frame, so a label to their left
    # runs off it.
    nb = int(prim["boundary"].astype(bool).sum())
    ab = anchors.total_bounds
    ax.annotate(f"downstream boundary, {nb} stations",
                ((ab[0] + ab[2]) / 2, ab[1]),
                xytext=(16, -26), textcoords="offset points",
                fontsize=6.6, color="#B01B23", ha="left", va="top",
                zorder=7, path_effects=HALO,
                arrowprops={"arrowstyle": "-", "color": "#B01B23",
                            "linewidth": 0.6, "shrinkB": 6})

    # Lower right, not lower left: the Panoche-San Luis panhandle reaches into
    # the bottom-left corner of the AOI extent and the legend landed on it.
    ax.legend(loc="lower right", frameon=False, fontsize=6.5, markerscale=1.3,
              handletextpad=0.6, labelspacing=0.7, borderpad=0.8)
    huc8 = layers["huc8"]
    in_scope = set(huc8.loc[huc8["in_scope"].astype(bool), "huc8"].astype(str))
    n_huc = int(huc8["in_scope"].astype(bool).sum())
    n_in = int(prim["huc8"].astype(str).isin(in_scope).sum())
    # The caption counts the boundary stations apart from the subbasins, as
    # the annotation on the map does.
    edge = sorted(prim.loc[prim["boundary"].astype(bool), "huc8"]
                  .dropna().astype(str).unique())
    at_edge = (f" and {nb} in the downstream boundary buffers, in "
               f"HUC{'s' if len(edge) > 1 else ''} {', '.join(edge)}"
               if nb else "")
    n_well_cont = int((prim["cont"] & is_well).sum())
    _register("01_basin_overview.png", "San Joaquin study area",
              f"{len(prim):,} primary stations, each measuring at least one "
              f"target parameter: {n_in:,} across {n_huc} subbasins{at_edge}. "
              "A station takes the "
              "first of the three classes it fits: a well draws as a well "
              f"whatever parameter the well records, so the {n_well_cont} "
              "wells with a continuous record sit in the "
              "groundwater class rather than the continuous one. Among the "
              "surface stations the class is the finest record type across the "
              "station's parameters, so a gage logging discharge every 15 "
              "minutes and sampling chloride by hand draws as continuous. "
              "Marker size is fixed on this figure, one size per class. Pale "
              "hydrography lies outside the study area.")
    _save_map(fig, "01_basin_overview.png")


def _parameter_map(st, sp, layers, parameter, title, fname):
    rows = sp[sp["parameter"] == parameter]
    ids = set(rows["station_uid"])
    # Continuity belongs to the record rather than to the site. A gage that
    # logs discharge every 15 minutes may still sample its conductance by hand,
    # so the marker shape here comes from this parameter's own record type.
    cont_ids = set(rows.loc[rows["record_type"] == "continuous", "station_uid"])
    sel = st[st["station_uid"].isin(ids) & st["is_primary"]].copy()
    if sel.empty:
        log.warning("no stations for %s, skipping %s", parameter, fname)
        return
    # Marker area is this parameter's record, not the station's. `por_start`
    # and `por_end` on the station row are the minimum and maximum across every
    # parameter, so a discharge gage that took four conductance samples would
    # draw a century-long conductance record. Dataset-scope rows stay out for
    # the same reason.
    span = (station_level(rows).groupby("station_uid")
            .agg(start=("por_start", "min"), end=("por_end", "max")))
    years = ((span["end"] - span["start"]).dt.days / 365.25).clip(lower=0)
    sel["years"] = sel["station_uid"].map(years).fillna(0.0)
    sel["cont"] = sel["station_uid"].isin(cont_ids)

    fig, ax = _map_figure(layers)
    _base(ax, layers)
    # Discrete underneath, continuous on top: the continuous network is what a
    # calibration draws on, and there are twenty times as many grab sites.
    for cont in (False, True):
        for src, g in sel[sel["cont"] == cont].groupby("source"):
            ax.scatter(g["lon"], g["lat"], s=_marker_size(g["years"]),
                       c=SOURCE_COLOR.get(src, OTHER_COLOR),
                       marker="o" if cont else "^",
                       alpha=0.95 if cont else 0.62,
                       linewidths=0.35 if cont else 0.0,
                       edgecolor="white", zorder=5 if cont else 4)

    # A legend entry for every color drawn, including the fallback if it is
    # used. The record-type keys are an outline against a fill rather than a
    # second gray, so gray means one thing on this figure. Each key has the
    # alpha of its markers, so a pale triangle on the map matches the pale
    # triangle in the key.
    present = set(sel["source"])
    handles = [Line2D([], [], marker="o", linestyle="", color=c,
                      label=f"{SOURCE_LABEL.get(s, s.upper())} "
                            f"({int((sel['source'] == s).sum())})", markersize=5)
               for s, c in SOURCE_COLOR.items() if s in present]
    if present - set(SOURCE_COLOR):
        handles.append(Line2D([], [], marker="o", linestyle="", color=OTHER_COLOR,
                              markersize=5, label="other source"))
    handles += [
        Line2D([], [], marker="o", linestyle="", color=INK, markersize=5,
               alpha=0.95,
               label=f"continuous record ({int(sel['cont'].sum())})"),
        Line2D([], [], marker="^", linestyle="", color="white", alpha=0.62,
               markeredgecolor=INK, markeredgewidth=0.9, markersize=5,
               label=f"discrete samples ({int((~sel['cont']).sum())})"),
    ]
    ax.add_artist(ax.legend(handles=handles, loc="lower left", frameon=False,
                            fontsize=6.5, labelspacing=0.42))
    _size_legend(ax)
    _register(fname, title,
              f"{len(sel):,} primary stations measure "
              f"{parameter.replace('_', ' ')} at some point in their record: "
              f"{int(sel['cont'].sum())} with a continuous monitor and "
              f"{int((~sel['cont']).sum())} by discrete samples. Both the "
              "marker shape and the marker area come from this parameter's own "
              "record rather than the station's, so a gage that logs discharge "
              "and samples its conductance by hand draws large and solid on "
              "the discharge map and small and pale on the conductance map. "
              "Marker area scales with the square root of record length, and "
              "the counts in the legend are stations rather than records.")
    _save_map(fig, fname)


def fig_density(st, sp, layers):
    """Stations per HUC12 reporting water temperature, specific conductance, or
    both.

    A subwatershed count is for finding the parts of the basin with no
    measurement, so the union of the two parameters is the right set: a HUC12
    with a temperature station and no conductance station is observed, and one
    with neither is not. The station maps show the two parameters apart.
    """
    want = {"water_temperature", "specific_conductance"}
    ids = set(sp.loc[sp["parameter"].isin(want), "station_uid"])
    prim = st[st["is_primary"]]
    sel = prim[prim["station_uid"].isin(ids)]

    huc12 = layers["huc12"][["huc12", "geometry"]].copy()
    counts = sel.groupby("huc12").size().rename("n")
    h = huc12.merge(counts, left_on="huc12", right_index=True, how="left")
    h["n"] = h["n"].fillna(0)

    bins = [0, 1, 2, 3, 5, 10, 20, 10_000]
    labels = ["none", "1", "2", "3-4", "5-9", "10-19", "20+"]
    cmap = ListedColormap(["#F2F0EC", "#DCECD2", "#B6DDC0", "#7FC7BD",
                           "#4FA3C0", "#2F6FAE", "#1B3F8B"])
    norm = BoundaryNorm(bins, cmap.N)

    fig, ax = _map_figure(layers)

    # The base layers go down by hand rather than through `_base`, because the
    # choropleth has to sit on top of the land fill and under the rivers. The
    # frame comes from `_frame`, as on the other maps.
    around = layers.get("surround")
    if around is None or around.empty:
        around = layers["huc8"]
    around[~around["in_scope"].astype(bool)].plot(
        ax=ax, facecolor=LAND_OUT, edgecolor=EDGE, linewidth=0.3,
        linestyle=":", zorder=0)
    out = layers.get("streams_out")
    if out is not None and not out.empty:
        out.plot(ax=ax, color=STREAM_OUT, linewidth=0.18, alpha=0.7, zorder=0.2)
    h.plot(ax=ax, column="n", cmap=cmap, norm=norm, edgecolor="white",
           linewidth=0.15, zorder=1)
    layers["aoi"].plot(ax=ax, facecolor="none", edgecolor=AOI_EDGE,
                       linewidth=0.7, zorder=2)
    # White rather than navy: the darkest class is #1B3F8B, and a navy river
    # disappears into it where the stations are densest.
    layers["tributaries"].plot(ax=ax, color="white", linewidth=0.7, alpha=0.9,
                               zorder=3)
    layers["mainstem"].plot(ax=ax, color="white", linewidth=1.4, zorder=3)
    _annotate_places(ax, reservoirs=False)
    _frame(ax, layers)

    patches = [Patch(facecolor=cmap(i), edgecolor="white", label=lab)
               for i, lab in enumerate(labels)]
    ax.legend(handles=patches, loc="lower right", ncol=2, frameon=False,
              fontsize=6.5, title="stations per HUC12", title_fontsize=6.5,
              handlelength=1.3, handleheight=1.1, labelspacing=0.5,
              borderpad=0.8)

    covered = int((h["n"] > 0).sum())
    outside = len(sel) - int(h["n"].sum())
    _register("05_density_by_huc12.png",
              "Where the basin is measured, and where it is not",
              f"{int(h['n'].sum()):,} stations measuring water temperature or "
              f"specific conductance, in {covered} of {len(h)} subwatersheds. "
              "The count is stations measuring either parameter, so a shaded "
              "subwatershed has at least one temperature or conductance "
              "record at some sampling frequency. "
              "The two unfilled circles at the downstream end are the 8 km "
              "anchor buffers around Vernalis and Mossdale: the study area "
              "includes the buffers and the HUC12 layer stops short of them."
              + (f" A further {outside} stations sit inside the buffers, in "
                 "HUC 18040003." if outside else ""))
    _save_map(fig, "05_density_by_huc12.png")


# Panels on the availability figure, in the order a calibration needs them.
TIMELINE_PARAMS = [
    ("water_temperature", "Water temperature", "#B3282D"),
    ("specific_conductance", "Specific conductance", "#123C8C"),
    ("discharge", "Discharge", "#4F493F"),
]

# The shortlist map keys on which of the two state variables a station holds.
# The single-parameter colors are the timeline panel colors above, so red means
# temperature and blue means conductance on both figures, and the plum between
# them means a station holds each.
PARAM_COLOR = {"temp": "#B3282D", "ec": "#123C8C", "both": "#6A2D91"}


def _station_spans(sp, parameter):
    """One row per station: its merged spans, first and last year.

    `spans` merges every record and `cont_spans` merges the continuous ones
    alone, so a year that only discrete samples cover sits in the first and
    not the second. Both come from `coverage.merged_spans`, which
    `coverage.spans` also calls for the spanning rule behind figures 12 to 15.
    """
    d = sp[sp["parameter"] == parameter].dropna(subset=["por_start", "por_end"])
    if d.empty:
        return pd.DataFrame(columns=["station_uid", "spans", "cont_spans",
                                     "first", "last", "years"])

    def _spans(g):
        c = g[g["record_type"] == "continuous"]
        return pd.Series({
            "spans": merged_spans(g["por_start"], g["por_end"]),
            "cont_spans": merged_spans(c["por_start"], c["por_end"])})

    out = (d.groupby("station_uid").apply(_spans, include_groups=False)
            .reset_index())
    out["first"] = out["spans"].map(lambda v: v[0][0])
    out["last"] = out["spans"].map(lambda v: v[-1][1])
    out["years"] = (out["last"] - out["first"]).dt.days / 365.25
    return out


def _yr(d):
    """A date as a fractional year, for the year axes of figures 6 and 7.

    January 1 sits on the year line and December 31 short of the next one, so
    a bar reaches into a year on the axis exactly when its record reaches into
    that calendar year, which is how figure 11 counts.
    """
    return d.year + (d.dayofyear - 1) / 365.25


def fig_timeline(st, sp):
    """Period of record for every station in the inventory, one row each.

    One row per station per parameter, so the shape is the figure: when each
    parameter arrived, how fast the network grew, and how much of it comes from
    a continuous monitor rather than from someone collecting samples. At one
    row per station a name would be under half a point tall, and figure 10
    names the stations worth pulling, so most rows here have no name.

    Sorted by first year within a panel, so the left edge is a staircase. A
    bar is solid across the years a continuous record covers and pale across
    the years only discrete samples cover, so the number of solid bars
    reaching into a year is the count figure 11 plots for that year.

    A bar breaks where the record breaks, and a gap is a period longer than
    `coverage.JOIN_DAYS` that no source reports covering. Read the two kinds
    differently: a gap in a WQP, CEDEN or eSMR record is real, because those
    periods come from the samples themselves, while USGS and CDEC publish a
    declared start and end, so an outage inside a USGS or CDEC period stays
    invisible here.

    Built from station-scope records only; see `coverage.station_level`.
    Without that filter the longest bars come from one EDI package that
    declares one period of record for its whole database and leaves the
    station-to-parameter pairing open.
    """
    core = station_level(sp)

    # Groundwater wells belong to a different question. A 40-year well
    # hydrograph is not a surface calibration series.
    #
    # Tested on channel_class rather than station_type. station_type is what
    # the source reports, and CDEC reports the sensor, so the stations named
    # "SAN JOAQUIN R - MONITORING WELL #130" and the like come through as
    # continuous_sonde or discrete_grab. channel_class works from the name and
    # gets them right.
    prim_all = st[st["is_primary"]].drop_duplicates("station_uid")
    wells = set(prim_all.loc[prim_all["channel_class"] == "groundwater",
                             "station_uid"])
    core = core[~core["station_uid"].isin(wells)]

    channel = prim_all.set_index("station_uid")["channel_class"].to_dict()

    panels = []
    for param, title, color in TIMELINE_PARAMS:
        rows = _station_spans(core, param)
        if rows.empty:
            continue
        # Canals, conduits, drains, and permitted outfalls sort into a block
        # at the foot of the panel. They record water entering or leaving a
        # modeled channel rather than a position on one, and ranked among the
        # river stations on record length they pass for river stations.
        rows["aside"] = rows["station_uid"].map(
            lambda u: channel.get(u) in OFF_CHANNEL_CLASSES)
        rows["class"] = rows["station_uid"].map(channel)
        rows = rows.sort_values(["aside", "first", "years"],
                                ascending=[True, True, False]).reset_index(drop=True)
        panels.append((param, title, color, rows))
    if not panels:
        log.warning("no records to draw, skipping the availability timeline")
        return

    counts = [len(r) for *_, r in panels]
    lo, hi = FIRST_YEAR, LAST_COMPLETE_YEAR + 1

    # Row height is the same in every panel, so panel area is station count and
    # the three are directly comparable.
    panel_in = 9.0
    fig, axes = plt.subplots(len(panels), 1, figsize=(9.6, panel_in + 1.9),
                             sharex=True,
                             gridspec_kw={"height_ratios": counts})
    axes = np.atleast_1d(axes)

    for ax, (param, title, color, rows) in zip(axes, panels):
        n = len(rows)
        segs = {True: [], False: []}
        early = []
        for y, r in enumerate(rows.itertuples()):
            drawn = False
            # Every record goes down faint, and the continuous records go over
            # it solid, so a bar is solid only across the years a continuous
            # monitor covers.
            for a, b in r.spans:
                x0, x1 = _yr(a), _yr(b)
                if x1 < lo:
                    continue
                if x0 < lo:
                    x0 = lo
                    early.append(y)
                segs[False].append([(x0, y), (max(x1, x0 + 0.25), y)])
                drawn = True
            for a, b in r.cont_spans:
                x0, x1 = max(_yr(a), lo), _yr(b)
                if x1 >= lo:
                    segs[True].append([(x0, y), (max(x1, x0 + 0.25), y)])
            # A record that both starts and ends before the axis draws no bar,
            # and the row still counts in the panel title. The arrow says the
            # record is off the left of the axis rather than absent.
            if not drawn:
                early.append(y)

        # A bracket in the left margin marks the block, rather than a band
        # across the panel: a shaded region behind a panel of hairlines changes
        # the apparent weight of every line inside it.
        n_aside = int(rows["aside"].sum())
        if n_aside:
            ax.plot([-0.014, -0.014], [n - n_aside - 0.5, n - 0.5],
                    transform=ax.get_yaxis_transform(), color=MUTED,
                    linewidth=1.4, clip_on=False, zorder=5)
        lw = max(0.35, panel_in / max(sum(counts), 1) * 72 * 0.95)
        for cont, alpha in ((False, 0.42), (True, 1.0)):
            if segs[cont]:
                ax.add_collection(mpl.collections.LineCollection(
                    segs[cont], colors=color, linewidths=lw, alpha=alpha,
                    zorder=3 if cont else 2))

        # Records that begin before the axis does keep their bar and gain an
        # arrow, so a clipped record does not pass for one starting in 1950.
        if early:
            ax.plot([lo - 0.5] * len(early), early, marker="<", markersize=1.8,
                    linestyle="", color=color, clip_on=False, zorder=4)

        # Six gages get a name in the left margin, with the row overdrawn so
        # the eye lands on it. At one row per station a reader has no way to
        # find a familiar gage among several hundred hairlines, and naming
        # every station puts the type under half a point tall.
        #
        # Five of the six sit within 40 rows of each other on the temperature
        # panel, which is 0.06 in apart. A label pushes down past the one
        # above it and keeps a leader back to its own row.
        idx = {u: i for i, u in enumerate(rows["station_uid"])}
        found = sorted((idx[u], name) for u, name in TIMELINE_ANCHORS.items()
                       if u in idx)
        pitch = ANCHOR_PITCH_IN * sum(counts) / panel_in
        ly = -pitch
        for y, name in found:
            ly = max(ly + pitch, y)
            # Heavy across the years a continuous record covers and light
            # across the rest, so a named row keeps the split the other rows
            # draw in color.
            for col, width, alpha, z in (("spans", 0.5, 0.45, 5.0),
                                         ("cont_spans", 1.0, 1.0, 5.1)):
                marks = []
                for a, b in rows.at[y, col]:
                    x0, x1 = max(_yr(a), lo), _yr(b)
                    if x1 >= lo:
                        marks.append([(x0, y), (max(x1, x0 + 0.25), y)])
                if marks:
                    ax.add_collection(mpl.collections.LineCollection(
                        marks, colors=INK, linewidths=width, alpha=alpha,
                        zorder=z))
            ax.plot([0.0, -0.006, -0.012], [y, ly, ly], color=INK,
                    transform=ax.get_yaxis_transform(), linewidth=0.4,
                    clip_on=False, zorder=5)
            ax.annotate(name, (-0.015, ly), xycoords=ax.get_yaxis_transform(),
                        fontsize=5.8, color=INK, va="center", ha="right",
                        annotation_clip=False, zorder=6)

        ax.set_ylim(n - 0.5, -0.5)
        ax.set_yticks([])
        ax.set_title(title, loc="left", fontsize=9, fontweight="bold", pad=3)
        ax.set_title(f"{n:,} stations", loc="right", fontsize=7.5, color=MUTED,
                     pad=4)
        ax.grid(axis="x", alpha=0.25, linewidth=0.4, zorder=1)
        ax.set_axisbelow(False)
        ax.tick_params(labelsize=7.5)
        for side in ("left", "right", "top"):
            ax.spines[side].set_visible(False)

        if n_aside:
            # Name the classes in the block rather than the four the set
            # holds. Panels differ: the discharge panel has drains and the
            # conductance panel has none.
            words = {"conveyance": "canals", "drain": "drains",
                     "effluent": "permitted outfalls",
                     "receiving_water": "receiving water"}
            present = [words.get(c, c) for c in
                       rows.loc[rows["aside"], "class"].value_counts().index]
            label = ", ".join(present[:-1]) + (" and " if len(present) > 1
                                               else "") + present[-1]
            ax.annotate(textwrap.fill(f"{label} ({n_aside})", 16),
                        (-0.022, n - n_aside / 2),
                        xycoords=ax.get_yaxis_transform(), fontsize=6.0,
                        color=MUTED, va="center", ha="right",
                        linespacing=1.35, annotation_clip=False)

    axes[-1].set_xlabel("year", fontsize=8.5)
    axes[-1].set_xlim(lo, hi)
    axes[-1].xaxis.set_major_locator(mpl.ticker.MultipleLocator(10))

    handles = [
        Line2D([], [], color=MUTED, linewidth=3.0, label="continuous record"),
        Line2D([], [], color=MUTED, linewidth=3.0, alpha=0.42,
               label="discrete samples"),
        Line2D([], [], color=MUTED, marker="<", markersize=4, linestyle="",
               label=f"record reaches back past {FIRST_YEAR}"),
    ]
    fig.legend(handles=handles, frameon=False, fontsize=7.5, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, 0.004))
    n_total = sum(counts)
    _register("06_availability_timeline.png",
              "Period of record, one row per station",
              f"{n_total:,} station records across three parameters, one row "
              "per station, sorted by first year within a block. A break is a "
              f"period longer than {coverage.JOIN_DAYS} days that no source "
              "reports covering. Read the two kinds of gap "
              "differently: a gap in a WQP, CEDEN or eSMR record comes from the "
              "samples themselves, while USGS and CDEC publish a declared start "
              "and end, so an outage inside a USGS or CDEC period stays "
              "invisible. Rows come from station-scope records, so the EDI "
              "package periods that cover a station list rather than a named "
              "station stay out, and so do the groundwater wells. A bar is "
              "solid across the years a continuous record covers and pale "
              "across the years only discrete samples cover, so the number of "
              "solid bars reaching into a year on a panel is the count figure "
              "11 plots for that year. Six gages have a name in the left "
              "margin and a black line, heavy where the bar is solid and thin "
              "where the bar is pale: Vernalis and Mossdale near the "
              "downstream boundary, Friant at the upstream one, and one gage "
              "each on the Stanislaus, Tuolumne, and Merced.")
    fig.subplots_adjust(top=0.985, bottom=0.062, left=0.115, right=0.988,
                        hspace=0.10)
    _save(fig, "06_availability_timeline.png", tight=False)


# Slough gages sit a few kilometers up their own channel, so projecting them
# onto the mainstem needs a wider tolerance than a station-position check does.
# Salt Slough at Highway 165 is 4 km off the river and Mud Slough near Gustine
# about 8. Both project close to their confluences, which is where the label
# belongs.
LANDMARK_OFFSET_M = 10000.0


def _mainstem_landmarks(line, st=None) -> dict[str, float]:
    """River-km of the control points, measured rather than typed.

    A hardcoded distance drifts the moment the mainstem geometry or the
    confluence detection changes.
    """
    from .places import FIXED_POINTS, MAINSTEM_LANDMARK_STATIONS
    from .rivers import confluences

    short = {"Friant Dam (upstream boundary)": "Friant",
             "Mendota Pool": "Mendota",
             "Salt Slough inflow": "Salt Sl.",
             "Mud Slough inflow": "Mud Sl.",
             "Vernalis (downstream boundary)": "Vernalis",
             "Stanislaus River confluence": "Stanislaus",
             "Tuolumne River confluence": "Tuolumne",
             "Merced River confluence": "Merced"}
    pts = {**FIXED_POINTS, **confluences()}
    gdf = gpd.GeoDataFrame(
        {"name": [short.get(k, k) for k in pts]},
        geometry=gpd.points_from_xy([v[1] for v in pts.values()],
                                    [v[0] for v in pts.values()]),
        crs="EPSG:4326")
    km = river_distance_km(gdf, line, max_offset_m=LANDMARK_OFFSET_M)
    out = {n: k for n, k in zip(gdf["name"], km) if pd.notna(k)}

    # Landmarks defined by station, taken straight from the persisted river
    # distance so the figure and the catalog cannot disagree.
    if st is not None and "mainstem_km" in st.columns:
        pos = (st.drop_duplicates("station_uid")
                 .set_index("station_uid")["mainstem_km"].to_dict())
        for uid, label in MAINSTEM_LANDMARK_STATIONS.items():
            k = pos.get(uid)
            if k is not None and pd.notna(k):
                out[label] = float(k)
            else:
                log.warning("no mainstem_km for landmark station %s (%s); "
                            "figure 7 leaves out its label", uid, label)
    return out


def fig_longitudinal(st, sp, layers):
    """When and where the mainstem was measured.

    Each station is a vertical line at its distance from Vernalis, drawn over
    the spans `_station_spans` merges from its records. A line is in the
    continuous style across the years a continuous record covers and in the
    discrete style across the years only discrete samples cover, as the bars
    on figure 6 are. Read down a column for the history at a point; read
    across for the coverage along the river.

    Stations on a natural channel or a permitted receiving water only.
    `mainstem_km` lands on every station within `rivers.ON_CHANNEL_M` of the
    centerline, and on this valley floor that still takes in canals, drains,
    piezometers, and reservoir gages, which is ink on the temperature panel and
    none of the signal.

    An empty column marks a reach with no station of either record type, and a
    column with only the discrete style marks one with no continuous station.
    Neither gets shading: most of the mainstem qualifies for temperature and EC,
    so a shaded band covers more of the panel than the data does. The kilometer counts are
    in docs/gap_analysis.md, computed once under one stated rule.
    """
    from .gaps import RIVER_CLASSES  # local import: gaps imports rivers, not maps

    line = merged_mainstem_line(layers["mainstem"])
    prim = st[st["is_primary"]].copy()
    if "mainstem_km" not in prim.columns:
        prim["mainstem_km"] = river_distance_km(prim, line)
    near = prim.dropna(subset=["mainstem_km"])
    n_all = len(near)
    # Name what leaves rather than lump it: the excluded set is more than
    # canals and wells, and a reader counting bars needs the classes.
    dropped = (near.loc[~near["channel_class"].isin(RIVER_CLASSES),
                        "channel_class"].value_counts())
    prim = prim[prim["channel_class"].isin(RIVER_CLASSES)]
    on = prim.dropna(subset=["mainstem_km"])
    if on.empty:
        log.warning("no stations near the mainstem, skipping longitudinal figure")
        return

    core = station_level(sp)

    order = ["water_temperature", "specific_conductance", "discharge"]
    landmarks = _mainstem_landmarks(line, prim)
    cont_c, disc_c = "#123C8C", "#C46A08"
    since = pd.Timestamp(domain_config()["calibration_period"]["start"]).year

    fig, axes = plt.subplots(len(order), 1, figsize=(10.4, 8.8), sharex=True)
    for ax, param in zip(axes, order):
        sel = on.merge(_station_spans(core, param), on="station_uid")

        # Records still running take the harvest date as their end, so the
        # axis stops just above it. A higher ceiling leaves a band of white
        # above every current record that passes for a stopped record.
        ax.set_ylim(FIRST_YEAR - 2, LAST_COMPLETE_YEAR + 2)
        ax.yaxis.set_major_locator(mpl.ticker.MultipleLocator(20))
        ax.yaxis.set_minor_locator(mpl.ticker.MultipleLocator(10))

        # The period the model is aimed at, on every panel. The three panels
        # share one axis range, so a reader compares them directly and reads a
        # blank stretch on one of them as an absence rather than as a
        # difference in scale.
        ax.axhspan(since, LAST_COMPLETE_YEAR + 2, color="#F2EFE8", zorder=0.5)
        for x in landmarks.values():
            ax.axvline(x, color=EDGE, linewidth=0.5, zorder=1)

        # Every record goes down in the discrete style and the continuous
        # records go over it, as on figure 6, so a line takes the continuous
        # style only across the years a continuous monitor covers. The figure
        # draws no line for a span that ends before the axis, and the arrow
        # below marks it.
        for cont, col in ((False, "spans"), (True, "cont_spans")):
            g = (sel[["mainstem_km", col]].explode(col, ignore_index=True)
                 .dropna(subset=[col]))
            y0 = g[col].map(lambda v: _yr(v[0]))
            y1 = g[col].map(lambda v: _yr(v[1]))
            shown = y1 >= FIRST_YEAR
            start = y0[shown].clip(lower=FIRST_YEAR)
            ax.vlines(g.loc[shown, "mainstem_km"], start,
                      np.maximum(y1[shown], start + 0.25),
                      color=cont_c if cont else disc_c,
                      linewidth=2.4 if cont else 1.2,
                      alpha=1.0 if cont else 0.6,
                      zorder=4 if cont else 3)
            # An arrow below the axis for a gage that predates it. Discharge on
            # this river was measured from the 1890s.
            early = g[y0 < FIRST_YEAR]
            if not early.empty:
                # Inside the axis, not on the spine. At FIRST_YEAR - 2.4 the
                # markers straddle the bottom spine, which is set at
                # FIRST_YEAR - 2.
                ax.plot(early["mainstem_km"], [FIRST_YEAR - 1.2] * len(early),
                        marker="v", markersize=2.6, linestyle="",
                        color=cont_c if cont else disc_c, zorder=5)

        ax.set_title(param.replace("_", " "), loc="left", fontsize=9,
                     fontweight="bold", pad=4)
        ax.set_ylabel("year", fontsize=8)
        ax.grid(axis="y", which="major", alpha=0.22, linewidth=0.5)
        ax.grid(axis="y", which="minor", alpha=0.10, linewidth=0.4)
        ax.set_axisbelow(True)
        ax.tick_params(labelsize=7.5)

    # Landmark names once, above the top panel, so the columns stay readable.
    # Vernalis, the Stanislaus, and the Tuolumne all land inside the first
    # 20 km of the axis, so the loop spreads their labels to a minimum spacing
    # and draws a leader back to the true position. The spacing applies only
    # where two labels would touch.
    span_km = max(landmarks.values()) - min(landmarks.values())
    min_sep = span_km * 0.022
    placed = -1e9
    for name, x in sorted(landmarks.items(), key=lambda kv: kv[1]):
        lx = max(x, placed + min_sep)
        placed = lx
        axes[0].annotate(name, (lx, 1.09), xycoords=("data", "axes fraction"),
                         rotation=90, ha="center", va="bottom", fontsize=6.6,
                         color=MUTED, annotation_clip=False)
        if abs(lx - x) > 1e-6:
            axes[0].plot([x, lx], [1.005, 1.075], transform=axes[0].get_xaxis_transform(),
                         color=EDGE, linewidth=0.5, clip_on=False, zorder=1)

    handles = [Line2D([], [], color=cont_c, linewidth=2.6, label="continuous record"),
               Line2D([], [], color=disc_c, linewidth=1.6, alpha=0.6,
                      label="discrete samples"),
               Line2D([], [], color=INK, marker="v", markersize=4,
                      linestyle="",
                      label=f"record reaches back past {FIRST_YEAR}"),
               Patch(facecolor="#F2EFE8",
                     label=f"calibration period, {since} onward")]
    fig.legend(handles=handles, frameon=False, fontsize=8, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, 0.002))
    axes[-1].set_xlabel("distance upstream from Vernalis along the San Joaquin "
                        "mainstem (km)", fontsize=8.5)
    axes[-1].set_xlim(-8, None)

    # The axis runs upstream, so the river runs right to left. Saying so costs
    # one arrow and saves every reader the same moment of doubt.
    axes[-1].annotate("", xy=(0.10, -0.235), xytext=(0.34, -0.235),
                      xycoords="axes fraction", annotation_clip=False,
                      arrowprops={"arrowstyle": "-|>", "color": MUTED,
                                  "linewidth": 0.9})
    axes[-1].annotate("direction of flow", (0.35, -0.235), xycoords="axes fraction",
                      fontsize=7, color=MUTED, va="center", annotation_clip=False)
    left_out = ", ".join(f"{int(n)} {c.replace('_', ' ')}"
                         for c, n in dropped.items())
    _register("07_mainstem_longitudinal.png",
              "Mainstem coverage in space and time",
              "Each vertical line is one station, drawn over its period of "
              "record against its distance upstream from Vernalis. A line is "
              "heavy and blue across the years a continuous record covers and "
              "thin and orange across the years only discrete samples cover, "
              f"and a break is a period longer than {coverage.JOIN_DAYS} days "
              "that no source reports covering. Stations on a "
              "natural channel or a permitted receiving water only: of the "
              f"{n_all} within {int(ON_CHANNEL_M)} m of the centerline, "
              f"{n_all - len(on)} belong to other classes and have no line "
              f"here ({left_out}). The three panels "
              "share one year axis, so a blank stretch on one of them is an "
              "absence rather than a change of scale. A vertical band with no "
              "lines is a reach of the mainstem with no record of that "
              "parameter in those years.")
    fig.subplots_adjust(top=0.900, bottom=0.105, left=0.062, right=0.985, hspace=0.30)
    _save(fig, "07_mainstem_longitudinal.png", tight=False)


def _shortlist(limit: int | None = None) -> pd.DataFrame:
    """The stations worth pulling first, ranked and numbered once.

    Read from `catalog_filtered.csv`, including the number, so the numbered
    marker on figure 08 and the row a modeller opens in a spreadsheet cannot
    disagree about which station is number 7. `inventory._rank_shortlist` sets
    the order: downstream to upstream along the mainstem, then the off-mainstem
    sites, then the off-channel ones.
    """
    path = PROCESSED / "catalog_filtered.csv"
    if not path.exists():
        log.warning("no catalog_filtered.csv; run sjrwq.inventory first")
        return pd.DataFrame()
    cf = pd.read_csv(path)
    sel = cf[cf["usable"].astype(bool)].copy()
    if sel.empty:
        return sel
    # The CSV round-trips a nullable integer through a float column, so a
    # number drawn straight from it reads "371.0" on figure 08.
    sel["rank"] = sel["rank"].astype(int)
    sel = sel.sort_values("rank").reset_index(drop=True)
    if limit:
        sel = sel.head(limit)
    return sel


# Vertical pitch between two anchor names in the timeline margin, in inches.
# A 5.8 pt label is about this tall with the leader clear of its neighbor.
ANCHOR_PITCH_IN = 0.085


# The box a shortlist number occupies, in degrees at the scale these maps are
# drawn: about 0.030 deg per digit across and 0.030 deg tall for a 5.4 pt bold
# label. LABEL_GAP_DEG is the space between a marker and its number, and the
# step a label takes when its first choice is occupied.
LABEL_W_DEG = 0.030
LABEL_H_DEG = 0.030
LABEL_GAP_DEG = 0.020


def _label_slots(w: float):
    """Offsets from a marker where its number fits, nearest first.

    Right of the marker first, which is where an uncrowded number goes and
    where a reader looks for it, then the same distance left, then rings
    further out. Working outward in rings rather than straight down keeps the
    six numbers inside the Vernalis buffer from stacking into one column of
    long leaders.
    """
    for ring in range(1, 9):
        dx = LABEL_GAP_DEG * ring
        rows = [0.0]
        for k in range(1, ring + 1):
            rows += [k * LABEL_H_DEG, -k * LABEL_H_DEG]
        for dy in rows:
            yield dx, dy
            yield -dx - w, dy


def _text_box(lon, lat, chars, per_char, height, pad_x=0.012, rise=0.006):
    """The rectangle a label drawn up and right of (lon, lat) covers."""
    x0 = lon + pad_x
    y0 = lat + rise - height / 2
    return (x0, y0, x0 + chars * per_char, y0 + height)


def _place_obstacles(pts=None, towns=True, reservoirs=True):
    """Boxes a shortlist number stays clear of: place names and the markers."""
    boxes = []
    if towns:
        for name, (lat, lon) in TOWNS.items():
            boxes.append(_text_box(lon, lat, len(name), 0.026, 0.034))
    if reservoirs:
        for name, (lat, lon) in RESERVOIRS.items():
            label = RESERVOIR_LABEL.get(name, name)
            boxes.append(_text_box(lon, lat, len(label), 0.024, 0.032,
                                   rise=-0.020))
    for lon, lat in (pts or ()):
        boxes.append((lon - 0.012, lat - 0.012, lon + 0.012, lat + 0.012))
    return boxes


def _spread_labels(ax, pts, obstacles=()):
    """Draw the shortlist numbers, moving apart the ones that would collide.

    Six of them fall inside the Vernalis buffer and render as one smear
    unplaced. Each number takes the first free slot from `_label_slots` and
    gains a leader wherever it moved far enough to need one.
    """
    boxes = list(obstacles)

    def free(b):
        return not any(b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3]
                       for o in boxes)

    for lon, lat, n in sorted(pts, key=lambda t: (-t[1], t[0])):
        text = str(n)
        w = LABEL_W_DEG * len(text)
        dx, dy, box = LABEL_GAP_DEG, 0.0, None
        for dx, dy in _label_slots(w):
            lx, ly = lon + dx, lat + dy
            cand = (lx, ly - LABEL_H_DEG / 2, lx + w, ly + LABEL_H_DEG / 2)
            if free(cand):
                box = cand
                break
        if box is None:
            # No slot free, so the number goes where an uncrowded one would.
            # dx and dy come back with it: they still hold the last ring tried,
            # and the leader test below would draw a line to a place the label
            # is not.
            dx, dy = LABEL_GAP_DEG, 0.0
            lx, ly = lon + dx, lat + dy
            box = (lx, ly - LABEL_H_DEG / 2, lx + w, ly + LABEL_H_DEG / 2)
        boxes.append(box)
        if abs(dx) > LABEL_GAP_DEG * 1.2 or abs(dy) > LABEL_H_DEG * 0.4:
            ax.plot([lon, lx], [lat, ly], color=MUTED, linewidth=0.4,
                    zorder=7.5)
        ax.annotate(text, (lx, ly), fontsize=5.4, va="center", ha="left",
                    fontweight="bold", color=INK, zorder=8, path_effects=HALO)


def fig_colocation(st, sp, layers):
    """Where all three calibration parameters are measured at one place.

    Split by continuous versus discrete, because a site with all three from
    quarterly samples cannot calibrate a temperature model, and the
    undivided count flatters the network.
    """
    want = set(coverage.CORE_PARAMETERS)
    have = (sp[sp["parameter"].isin(want)]
            .groupby("station_uid")["parameter"].agg(set))
    two = {u for u, s in have.items() if len(s) == 2}
    # All three recorded, rather than all three present: `coverage.colocated`
    # puts a site that logs discharge every 15 minutes and takes its EC by
    # hand in the discrete group. The gap report counts from the same call.
    full, cont_ids = coverage.colocated(sp)

    prim = st[st["is_primary"]].copy()
    prim["cont"] = prim["station_uid"].isin(cont_ids)
    # Marker area is the record of these three parameters, not the station's
    # whole span across every parameter it reports.
    span = (station_level(sp[sp["parameter"].isin(want)]).groupby("station_uid")
            .agg(start=("por_start", "min"), end=("por_end", "max")))
    years = ((span["end"] - span["start"]).dt.days / 365.25).clip(lower=0)
    prim["years"] = prim["station_uid"].map(years).fillna(0.0)

    fig, ax = _map_figure(layers)
    _base(ax, layers)
    b = prim[prim["station_uid"].isin(two)]
    ax.scatter(b["lon"], b["lat"], s=8, c="#b8b2a8", linewidths=0, zorder=4,
               label=f"two of three ({len(b)})")
    a = prim[prim["station_uid"].isin(full)]
    ad, ac = a[~a["cont"]], a[a["cont"]]
    ax.scatter(ad["lon"], ad["lat"], s=_marker_size(ad["years"]), c="#e0a24a",
               marker="^", alpha=0.75, linewidths=0.3, edgecolor="white", zorder=5,
               label=f"all three, discrete ({len(ad)})")
    ax.scatter(ac["lon"], ac["lat"], s=_marker_size(ac["years"]), c="#b3282d",
               linewidths=0.4, edgecolor="white", zorder=6,
               label=f"all three, continuous ({len(ac)})")
    # Number the shortlist sites, keyed to figure 10. An unlabeled red dot
    # leaves the reader with a position; a number leads to a named station.
    short = _shortlist()
    n_keyed = n_rest = n_rest_flow = n_rest_two = 0
    if not short.empty:
        keyed = short.set_index("station_uid")["rank"].to_dict()
        pts = [(r["lon"], r["lat"], keyed[r["station_uid"]])
               for _, r in a.iterrows() if r["station_uid"] in keyed]
        n_keyed = len(pts)
        _spread_labels(ax, pts, _place_obstacles(zip(a["lon"], a["lat"])))
        # The unnumbered shortlist stations, by what they lack and where they
        # draw. A station gaging discharge beside one state variable holds two
        # of the three and draws gray here, the same as one holding both state
        # variables and no discharge.
        rest = short[~short["station_uid"].isin(set(a["station_uid"]))]
        n_rest = len(rest)
        n_rest_flow = int(rest["has_flow"].astype(bool).sum())
        n_rest_two = int(rest["station_uid"].isin(set(b["station_uid"])).sum())

    # A reader sees numbered and unnumbered dots of the same color and has no
    # way to tell what the number means, so the legend says it.
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], linestyle="", marker="$7$", color=INK,
                          markersize=5))
    labels.append(f"numbered: on the shortlist ({n_keyed}); "
                  "catalog_filtered.csv lists them")
    ax.add_artist(ax.legend(handles, labels, loc="lower left", frameon=False,
                            fontsize=6.5))
    _size_legend(ax)
    _register("08_colocation.png", "Co-located stations",
              "Water temperature, specific conductance, and discharge measured "
              f"at one site: {len(a)} stations, {len(ac)} of them measuring "
              f"all three with a continuous monitor. {n_keyed} of the {len(a)} "
              "are on the shortlist and are numbered here, and "
              "`data/processed/catalog_filtered.csv` lists those "
              f"{n_keyed} under the same number in its `rank` column. The "
              f"remaining {n_rest:,} shortlist stations lack at least one of "
              f"the three: {n_rest_flow} of those {n_rest:,} gage discharge and "
              f"{n_rest - n_rest_flow:,} don't. The {n_rest_two} that measure "
              "two of the three draw as unnumbered gray dots, and the other "
              f"{n_rest - n_rest_two} measure one and appear on figure 10 "
              "and not on this map. Numbering runs downstream to "
              "upstream along the mainstem, then off-mainstem, then the "
              "canals, drains, wells, and outfalls, so the sequence on the "
              "map isn't contiguous. A leader line marks a label moved clear "
              "of a neighbor.")
    _save_map(fig, "08_colocation.png")


# The removed slice on the filtering figure, against BAR for what is kept.
CUT = "#D6D2CA"


def fig_funnel(cal):
    """What each filtering step removes, and what is left after each one.

    Each row is the row above it, split into what survived and what the step
    took out. The bar shrinks down the page, which is the funnel, and the gray
    is the difference, so there is one thing to read and no axis to decode. The
    scale stays linear, which keeps the two large cuts large.

    Read from `data/processed/funnel.csv`, which `inventory.funnel` writes in
    the same run that builds the tables. Two of the counts exist only inside
    `inventory.build`: what the harvests returned before the study-area clip,
    and what survived it. Drawing the first bar from `stations.gpkg` instead
    starts the funnel a third of the way down.

    The chain tests existence and length: does a station measure a state
    variable of the model, and does it measure one for long enough inside the
    calibration period. Record type, discharge, channel class, currency, and
    whether the two records overlap are columns on `catalog_filtered.csv`
    rather than steps here: a monthly conductance sample constrains a seasonal
    signal and, against a gaged flow, a monthly load; a canal or a well on the
    shortlist is a boundary term a salt balance needs; and a record that ended
    in 2014 covers fourteen years of the period.
    """
    path = PROCESSED / "funnel.csv"
    if not path.exists():
        log.warning("no funnel.csv; run sjrwq.inventory first")
        return
    steps = pd.read_csv(path)
    counts = steps["n"].tolist()

    fig, ax = plt.subplots(figsize=(8.4, 3.9))
    ys = np.arange(len(steps))[::-1]
    for i, (y, n) in enumerate(zip(ys, counts)):
        prev = counts[i - 1] if i else n
        ax.barh(y, n, height=0.56, color=BAR, zorder=3)
        if prev > n:
            ax.barh(y, prev - n, left=n, height=0.56, color=CUT, zorder=3)
            ax.annotate(f"\u2212{prev - n:,}", (n + (prev - n) / 2, y),
                        ha="center", va="center", fontsize=7.4, color=MUTED,
                        zorder=4)
        ax.annotate(f"{n:,}", (1.0, y), xycoords=("axes fraction", "data"),
                    xytext=(-2, 0), textcoords="offset points", ha="right",
                    va="center", fontsize=9, fontweight="bold", color=INK,
                    zorder=4)

    ax.set_xlim(0, counts[0] * 1.14)
    ax.set_ylim(ys[-1] - 0.45, ys[0] + 0.5)
    ax.set_yticks(ys)
    ax.set_yticklabels(steps["label"], fontsize=8)
    ax.set_xticks([])
    ax.set_xticks([], minor=True)
    ax.grid(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0, which="both")

    short = cal[cal["usable"].astype(bool)]
    pair = short["has_pair"].astype(bool)
    rec = short["continuous"].astype(bool)
    flow = short["has_flow"].astype(bool)
    # The same two settings `inventory.funnel` writes into the last label.
    period = domain_config()["calibration_period"]
    least = period["min_record_years"]
    since = pd.Timestamp(period["start"]).year
    handles = [Patch(facecolor=BAR, label="survives this step"),
               Patch(facecolor=CUT, label="removed by this step")]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=7.4,
              bbox_to_anchor=(1.0, -0.115), ncol=2, handlelength=1.4,
              borderpad=0.2, handletextpad=0.6)

    _register("09_station_filtering.png",
              "From harvested records to calibration sites",
              "Each bar is the bar above it, split into what survived the step "
              "named on the left and what the step removed, so a reader can "
              "subtract two bars and get the gray between them. "
              f"{len(short):,} stations remain: {int(pair.sum())} measure "
              f"both parameters, {int(rec.sum())} measure both with a "
              f"continuous monitor, and {int(flow.sum())} of the "
              f"{len(short):,} gage discharge. The chain tests two things, existence and length: whether a station "
              "measures a state variable of the model, and whether it measures "
              f"one for at least {least} years between {since} and today. The "
              "last step tests the longer of the temperature and conductance "
              "records rather than their overlap, because a modeler can "
              "calibrate the temperature field against a thermograph alone, "
              "with no conductance record. "
              "Record type, discharge, channel class, currency, and the "
              "overlap between the two records are columns on "
              "`catalog_filtered.csv` rather than steps here. A monthly "
              "conductance sample still constrains a seasonal signal and, "
              "against a gaged flow, a salt load. A canal or a well is a "
              "boundary term a salt balance needs. A record that ended in "
              f"{since + 14} covers fourteen years of the period. A station "
              "removed here stays in the inventory. Figure 10 maps what's "
              "left.")
    fig.subplots_adjust(top=0.960, bottom=0.115, left=0.335, right=0.945)
    _save(fig, "09_station_filtering.png", tight=False)


def fig_shortlist(st, layers):
    """The shortlist on the map: every station the funnel ends at.

    Figure 09's last bar and this map hold the same stations, so a reader who
    subtracts the funnel down to a number sees where those stations are. The
    marker shape marks which of the two parameters a station has measured, and
    the fill marks whether one of the records comes from a continuous monitor.
    The caption counts the stations holding `min_record_years` of both.

    The names are in `data/processed/catalog_filtered.csv`, ranked in the same
    order figure 08 numbers them, because a named list of every station on the
    shortlist belongs in a spreadsheet rather than on a figure.
    """
    short = _shortlist()
    if short.empty:
        log.warning("no usable stations, skipping shortlist figure")
        return
    sel = st[st["is_primary"] & st["station_uid"].isin(short["station_uid"])].copy()
    keyed = short.set_index("station_uid")
    for col in ("record_years_in_period", "temp_years_in_period",
                "ec_years_in_period", "has_pair", "temp_start", "ec_start",
                "temp_continuous", "ec_continuous"):
        sel[col] = sel["station_uid"].map(keyed[col])
    sel["years"] = sel["record_years_in_period"].astype(float).fillna(0.0)
    # Continuous means one of the two records comes from a continuous monitor,
    # not both. A site whose temperature is logged every 15 minutes and whose
    # conductance is a monthly bottle holds a continuous record.
    sel["cont"] = (sel["temp_continuous"].fillna(False).astype(bool)
                   | sel["ec_continuous"].fillna(False).astype(bool))
    # Which of the two parameters the station has measured, in any year.
    sel["kind"] = np.where(
        sel["has_pair"].fillna(False).astype(bool), "both",
        np.where(sel["temp_start"].notna(), "temp", "ec"))

    fig, ax = _map_figure(layers)
    _base(ax, layers)
    # One-parameter stations underneath: the pairs are what a joint calibration
    # draws on, and a pair hidden under a temperature-only marker reads as the
    # thinner record.
    shapes = {"temp": ("^", "temperature only"),
              "ec": ("v", "conductance only"),
              "both": ("o", "both parameters")}
    for kind in ("temp", "ec", "both"):
        marker, _ = shapes[kind]
        for cont in (False, True):
            g = sel[(sel["kind"] == kind) & (sel["cont"] == cont)]
            if g.empty:
                continue
            ax.scatter(g["lon"], g["lat"], s=_marker_size(g["years"]),
                       marker=marker,
                       c=PARAM_COLOR[kind] if cont else "white",
                       alpha=0.95 if cont else 0.85,
                       linewidths=0.35 if cont else 0.55,
                       edgecolor="white" if cont else PARAM_COLOR[kind],
                       zorder=(6 if kind == "both" else 5) + (1 if cont else 0))

    handles = []
    for kind in ("both", "temp", "ec"):
        marker, label = shapes[kind]
        n = int((sel["kind"] == kind).sum())
        handles.append(Line2D([], [], marker=marker, linestyle="",
                              color=PARAM_COLOR[kind], markersize=5,
                              label=f"{label} ({n})"))
    n_cont = int(sel["cont"].sum())
    handles += [
        Line2D([], [], marker="o", linestyle="", color=INK, markersize=5,
               label=f"filled: a continuous record ({n_cont})"),
        Line2D([], [], marker="o", linestyle="", color="white",
               markeredgecolor=INK, markeredgewidth=0.9, markersize=5,
               label=f"open: discrete samples only ({len(sel) - n_cont})"),
    ]
    ax.add_artist(ax.legend(handles=handles, loc="lower left", frameon=False,
                            fontsize=6.5, labelspacing=0.42))
    period = domain_config()["calibration_period"]
    least = period["min_record_years"]
    since = pd.Timestamp(period["start"]).year
    _size_legend(ax, title=f"record since {since}", keys=[5, 15, 25])

    n_pair = int((sel["kind"] == "both").sum())
    # Which records clear the rule. A station that has measured both
    # parameters can hold one of the two records for under `least` years.
    temp_ok = sel["temp_years_in_period"].astype(float) >= least
    ec_ok = sel["ec_years_in_period"].astype(float) >= least
    n_off = int(short["off_channel"].astype(bool).sum())
    _register("10_current_stations.png",
              f"Stations with {least} years or more of water temperature or "
              f"specific conductance since {since}",
              f"The {len(short):,} stations in figure 9's last bar, listed "
              "in `data/processed/catalog_filtered.csv` and ranked there in "
              "the order figure 8 numbers them. A station reaches this map on "
              f"one parameter or on two: {int((temp_ok & ec_ok).sum())} hold "
              f"{least} years or more of both since {since}, and "
              f"{int((temp_ok ^ ec_ok).sum())} of only one. Marker shape follows "
              "the parameters a station has measured in any year: "
              f"{n_pair} measured both, "
              f"{int((sel['kind'] == 'temp').sum())} temperature alone, and "
              f"{int((sel['kind'] == 'ec').sum())} conductance alone. Marker "
              f"area is the longer of the two records clipped to {since} onward, "
              "which is the quantity the rule tests, so a marker at the "
              "smallest size stands for a record of close to "
              f"{least} years. A filled marker means one of the two records "
              "comes from a continuous monitor, which is true at "
              f"{n_cont} of the {len(sel):,} stations. Record type, "
              "discharge, channel class, and currency stay columns on the CSV "
              f"rather than filters, so {n_off} stations here sit off the "
              "modeled channel, and "
              f"{int(short['has_flow'].astype(bool).sum())} of the "
              f"{len(short):,} gage discharge.")
    _save_map(fig, "10_current_stations.png")


def fig_coverage_time(st, sp):
    """How many stations held a continuous record covering each year.

    The gap analysis reports the same counts by decade, which hides the shape.
    Two things show up as a curve and not in a table: temperature and specific
    conductance arrive about twenty years after discharge, and the continuous
    networks for all three peak in the 2010s.

    Continuous records only. A second line per parameter covering discrete
    samples adds two features that look like findings and are artefacts: a
    spike across 1978 to 1980 that is a single National Park Service campaign
    in the upper Merced and Tuolumne, and a fall after about 2015 that is
    publication lag, because a discrete record ends on its most recent
    published sample.

    Groundwater wells are out, and rows come from station-scope records, both
    on the same rule figure 6 uses. The number of solid bars reaching into a
    year on a figure 6 panel is the value this curve plots for that year, and
    the two agree only if they start from the same rows.

    The series stops at the last complete calendar year, so a partial year of
    reporting stays out of a chart of complete ones.
    """
    core = ["discharge", "water_temperature", "specific_conductance"]
    # Stop at the last complete calendar year. Counting into the current year
    # mixes a partial year of reporting with complete ones and produces a fall
    # at the right edge that looks like a finding and is not.
    years = np.arange(FIRST_YEAR, LAST_COMPLETE_YEAR + 1)
    colors = {"discharge": "#4F493F", "water_temperature": "#b3282d",
              "specific_conductance": "#123C8C"}

    prim = st[st["is_primary"]].drop_duplicates("station_uid")
    wells = set(prim.loc[prim["channel_class"] == "groundwater", "station_uid"])
    rows = station_level(sp)
    rows = rows[~rows["station_uid"].isin(wells)]

    fig, ax = plt.subplots(figsize=(9.0, 4.6))
    for param in core:
        rec = rows[(rows["parameter"] == param)
                   & (rows["record_type"] == "continuous")].dropna(
                       subset=["por_start", "por_end"])
        counts = [rec[(rec["por_start"].dt.year <= y)
                      & (rec["por_end"].dt.year >= y)]["station_uid"].nunique()
                  for y in years]
        ax.plot(years, counts, "-", color=colors[param], linewidth=2.0, zorder=3)
        # The name sits outside the axes, past the last point. Padding the axis
        # to make room instead draws 15 years of empty grid that reads as
        # fifteen years of no data.
        ax.annotate(param.replace("_", " "), (years[-1], counts[-1]),
                    xytext=(6, 0), textcoords="offset points", fontsize=7.5,
                    color=colors[param], va="center", fontweight="bold",
                    annotation_clip=False)

    ax.set_xlim(FIRST_YEAR, years[-1])
    ax.set_xticks(np.arange(FIRST_YEAR, years[-1] + 1, 10))
    ax.set_ylim(0, None)
    ax.set_xlabel("year")
    ax.set_ylabel("stations with a continuous record covering this year")
    ax.grid(axis="y", alpha=0.22, linewidth=0.5)
    ax.set_axisbelow(True)
    _register("11_coverage_through_time.png",
              "Continuous records in each year",
              "How many stations held a continuous record covering each year "
              f"from {FIRST_YEAR} to {LAST_COMPLETE_YEAR}, by parameter. A "
              "station counts in every year its period of record spans, so the "
              "line is the standing network rather than the stations that "
              "started that year. Discrete samples are out: a temperature "
              "calibration uses a continuous record, and counting monthly "
              "samples would flatten the shape. Groundwater wells and "
              "dataset-scope records are out too, on the same rule figure 6 "
              "uses, so the number of solid bars reaching into a year on a "
              "figure 6 panel is the value this curve plots for that year.")
    fig.subplots_adjust(right=0.845)
    _save(fig, "11_coverage_through_time.png", tight=False)


# The ten map figures share one frame, so they can be paged through or
# printed as a set. `bbox_inches="tight"` sizes the frame from the drawn
# content, which produces ten different frames, so `_save_map` declares the
# size instead.
#
# The height is computed rather than declared. The study area is fixed, so its
# drawn width-to-height ratio is fixed too, and a declared height that does not
# match it letterboxes the map inside its own axes.
MAP_WIDTH_IN = 6.9
MAP_TOP_IN = 0.12        # a margin above the axes, matching the bottom
MAP_BOTTOM_IN = 0.12     # a margin below the axes, matching the side margin
MAP_SIDE_FRAC = 0.012


def _map_shape(layers) -> float:
    """Drawn height over drawn width for the study area, after the aspect."""
    b = layers["aoi"].total_bounds
    w = (b[2] - b[0]) + 2 * MAP_PAD_DEG
    h = ((b[3] - b[1]) + 2 * MAP_PAD_DEG) * MAP_ASPECT
    return h / w


def _map_figure(layers):
    """A map frame whose height is a whole number of pixels at `figure.dpi`.

    The macOS backend drops the fraction of a pixel from a figure's size at
    `figure.dpi` and Agg keeps it, so a fractional height saves at two sizes on
    the two backends. Rounding the height down here gives one size on both.
    """
    ax_w = MAP_WIDTH_IN * (1 - 2 * MAP_SIDE_FRAC)
    ax_h = ax_w * _map_shape(layers)
    dpi = mpl.rcParams["figure.dpi"]
    height = np.floor((ax_h + MAP_TOP_IN + MAP_BOTTOM_IN) * dpi) / dpi
    fig, ax = plt.subplots(figsize=(MAP_WIDTH_IN, height))
    fig.subplots_adjust(left=MAP_SIDE_FRAC, right=1 - MAP_SIDE_FRAC,
                        bottom=MAP_BOTTOM_IN / height,
                        top=1 - MAP_TOP_IN / height)
    return fig, ax


def _register(fname: str, title: str, caption: str) -> None:
    """Hold one figure's caption for `write_captions`.

    A figure registers its caption while it renders, so a count in the caption
    comes from the frame that drew the plot rather than from a hand-typed
    number that goes stale at the next harvest.
    """
    CAPTIONS.append({"fname": fname, "number": int(fname[:2]),
                     "title": " ".join(title.split()),
                     "caption": " ".join(caption.split())})


def write_captions() -> None:
    """docs/figures.md, one entry per figure, in figure order."""
    lede = ("`python -m sjrwq.maps` writes the PNGs in `figures/` and this "
            "file together. Every count below comes from `data/processed` at "
            "render time.")
    out = ["# Figures", "", textwrap.fill(lede, 79), ""]
    for c in sorted(CAPTIONS, key=lambda c: c["number"]):
        out += [f"## Figure {c['number']}. {c['title']}", "",
                f"![{c['title']}](../figures/{c['fname']})", "",
                textwrap.fill(c["caption"], 79), ""]
    path = DOCS / "figures.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    log.info("wrote docs/%s", path.name)


def _save(fig, name, tight=True, crop=True):
    """Write a figure. `crop=False` holds the declared size."""
    FIGURES.mkdir(parents=True, exist_ok=True)
    if tight:
        fig.tight_layout()
    fig.savefig(FIGURES / name, bbox_inches="tight" if crop else None)
    plt.close(fig)
    log.info("wrote figures/%s", name)


def _save_map(fig, name):
    _save(fig, name, tight=False, crop=False)



# --------------------------------------------------------------------------
# Concurrent coverage. Figures 12 to 14.
# --------------------------------------------------------------------------

# Figures 02 to 04 show where a parameter has been measured at some point.
# These three cover "where were these parameters measured together, over the
# same years", which is what a calibration needs. The window and the two
# membership rules are in sjrwq.coverage; see that module's docstring.

COVER_ON = "#B01B23"      # holds the full concurrent record
COVER_PART = "#E0A24A"    # measured in the window, not continuous across all of it
COVER_OFF = "#B8B2A8"     # other stations measuring one or more of the parameters


def _coverage_map(st, sp, layers, key, title, caption, fname,
                  strict_key=None, split_label=None):
    """One concurrent-coverage map.

    `key` names a rule in `coverage.COVERAGE_SETS`, so the map and
    `coverage_sets.csv` draw the same station set from one definition.
    `strict_key` turns on the two-class draw used by the salt-load figure: the
    stations meeting that stricter rule are drawn one way and the rest another.
    """
    parameters = coverage.COVERAGE_SETS[key]["parameters"]
    ids = coverage.members(sp, key)
    strict = coverage.members(sp, strict_key) if strict_key else set()
    prim = st[st["is_primary"]].copy()

    # Every station measuring at least one of these parameters inside the
    # window, so the map shows how much of the network falls short rather than
    # the survivors alone. Some of them measure every parameter and fail the
    # continuity or the span test instead, so the key says "does not meet the
    # rule" rather than "records some but not all".
    any_ids: set[str] = set()
    for v in parameters:
        any_ids |= coverage.stations_with(sp, [v], rule="within", continuous=False)
    other = prim[prim["station_uid"].isin(any_ids - ids)]
    all_ids = set.intersection(*(
        coverage.stations_with(sp, [v], rule="within", continuous=False)
        for v in parameters)) if parameters else set()
    n_near = len(prim.loc[prim["station_uid"].isin(all_ids - ids)])
    sel = prim[prim["station_uid"].isin(ids)].copy()
    if sel.empty:
        log.warning("no stations for %s, skipping", fname)
        return set()

    fig, ax = _map_figure(layers)
    _base(ax, layers)
    ax.scatter(other["lon"], other["lat"], s=5, c=COVER_OFF, alpha=0.75,
               linewidths=0, zorder=4,
               label=f"does not meet the rule ({len(other):,})")

    if split_label:
        a = sel[sel["station_uid"].isin(strict)]
        b = sel[~sel["station_uid"].isin(strict)]
        ax.scatter(a["lon"], a["lat"], s=34, c=COVER_ON, alpha=0.95,
                   linewidths=0.4, edgecolor="white", zorder=6,
                   label=f"flow and EC continuous across the window ({len(a)})")
        ax.scatter(b["lon"], b["lat"], s=34, c=COVER_PART, marker="s",
                   alpha=0.95, linewidths=0.4, edgecolor="white", zorder=6,
                   label=f"{split_label} ({len(b)})")
    else:
        ax.scatter(sel["lon"], sel["lat"], s=38, c=COVER_ON, alpha=0.95,
                   linewidths=0.4, edgecolor="white", zorder=6,
                   label=("records both across the window"
                          if len(parameters) == 2 else
                          f"records all {len(parameters)} across the window")
                         + f" ({len(sel)})")

    ax.legend(loc="lower right", frameon=False, fontsize=6.5, markerscale=1.0,
              handletextpad=0.6, labelspacing=0.7, borderpad=0.8)
    # A `within` rule with no continuity test admits every station measuring
    # every parameter in the window, so the near-miss sentence has nothing to
    # count there and drops out.
    near = (f"{n_near} of the gray stations measure every parameter in the "
            "rule and fail on continuity or on the span instead. "
            if n_near else "")
    _register(fname, title,
              f"{len(sel)} stations over {coverage.label()}. The "
              f"{len(other):,} stations that measure at least one of these "
              "parameters in the window and don't meet the rule are gray. "
              f"{near}{caption} Figure 15 shows how the counts on figures 12 "
              "and 13 change with the length of the window.")
    _save_map(fig, fname)
    return ids


def _wider(sp, parameters, years: int) -> int:
    """Stations meeting the same rule over a window `years` longer at the start."""
    start, _ = coverage.window()
    return len(coverage.stations_with(
        sp, parameters, start=start - pd.DateOffset(years=years)))


# The 2012-2016 drought and the two wet years that bracket the window. A
# calibration window that misses the drought misses the flow regime the model
# is most likely to be run over.
DROUGHT_YEARS = (2012, 2016)


def fig_coverage_window(sp):
    """Why the coverage window starts where it does.

    Figures 12 and 13 count stations holding a continuous record across a
    stated window, and the window sets the answer. Lengthening the window
    removes stations, because fewer hold a record across all of it, and
    shortening the window removes years from every station that stays.
    Neither curve alone picks the window.

    `station_years`, stations times length, is the concurrent record a window
    makes available, and it peaks where the two effects balance. The window
    gets shorter from left to right on both panels.

    Drawn from `coverage.window_tradeoff`, which takes its rules from
    `COVERAGE_SETS`, so a changed rule moves this figure and figures 12 to 14
    in one run.
    """
    d = coverage.window_tradeoff(sp)
    w_start, w_end = coverage.window()
    rules = [("temp_flow", "temperature and discharge", "#B3282D", 12),
             ("temp_ec_flow", "temperature, conductance and discharge",
              "#123C8C", 13)]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.0, 6.4), sharex=True)
    for ax, col, ylab in ((ax1, "n_stations", "stations holding the record"),
                          (ax2, "station_years",
                           "station-years of concurrent record")):
        ax.axvspan(*DROUGHT_YEARS, color="#F2EFE8", zorder=0.5)
        ax.axvline(w_start.year, color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)),
                   zorder=2)
        for key, label, color, fignum in rules:
            g = d[d["rule"] == key].sort_values("start_year")
            ax.plot(g["start_year"], g[col], "-", color=color, linewidth=2.0,
                    zorder=3)
            ax.annotate(f"figure {fignum}", (g["start_year"].iloc[-1],
                                             g[col].iloc[-1]),
                        xytext=(6, 0), textcoords="offset points", fontsize=7.5,
                        color=color, va="center", fontweight="bold",
                        annotation_clip=False)
        ax.set_ylabel(ylab, fontsize=8)
        ax.grid(axis="y", alpha=0.22, linewidth=0.5)
        ax.set_axisbelow(True)
        ax.set_ylim(0, None)

    # The peak on the lower panel is the argument for the window, so it gets a
    # marker and a number rather than a reader's eye.
    peaks = {}
    for key, label, color, _ in rules:
        g = d[d["rule"] == key].sort_values("start_year")
        row = g.loc[g["station_years"].idxmax()]
        peaks[key] = row
        ax2.plot([row["start_year"]], [row["station_years"]], "o", color=color,
                 markersize=5, zorder=4)
        ax2.annotate(f"{int(row['start_year'])}: {int(row['n_stations'])} "
                     f"stations × {row['window_years']:.0f} yr",
                     (row["start_year"], row["station_years"]),
                     xytext=(0, 9), textcoords="offset points", fontsize=7,
                     color=color, ha="center", fontweight="bold")

    top = ax1.secondary_xaxis(
        "top", functions=(lambda y: w_end.year - y, lambda n: w_end.year - n))
    top.set_xlabel("window length, years", fontsize=8)
    ax1.annotate(f"drought {DROUGHT_YEARS[0]}-{DROUGHT_YEARS[1]}",
                 (DROUGHT_YEARS[1], 1.0), xycoords=("data", "axes fraction"),
                 xytext=(-3, -8), textcoords="offset points", fontsize=6.6,
                 color=MUTED, ha="right", va="top")
    ax2.annotate(f"window in use, {coverage.label()}",
                 (w_start.year, 0.03), xycoords=("data", "axes fraction"),
                 xytext=(5, 0), textcoords="offset points", fontsize=7,
                 color=MUTED, ha="left", va="bottom")
    ax2.set_xlabel("first year of the window; every window ends "
                   f"{w_end.year - 1}")
    ax1.set_xlim(d["start_year"].min(), d["start_year"].max())

    tf = peaks["temp_flow"]
    tef = peaks["temp_ec_flow"]

    def _against(key, peak, which):
        """Where the window in use sits against one rule's peak."""
        if int(peak["start_year"]) == w_start.year:
            return f"at the {which}"
        here = d.loc[(d["rule"] == key) & (d["start_year"] == w_start.year),
                     "station_years"].iloc[0]
        gap = (peak["station_years"] - here) / peak["station_years"] * 100
        if gap <= 0:
            return f"level with the {which}"
        # Rounded up, so "within" stays true of the unrounded gap.
        return f"within {int(np.ceil(gap))} percent of the {which}"

    in_use = ""
    if w_start.year in set(d["start_year"]):
        in_use = (f" The window in use, {coverage.label()}, sits "
                  f"{_against('temp_flow', tf, 'first peak')} and "
                  f"{_against('temp_ec_flow', tef, 'second peak')}.")
    pair = d[d["rule"] == "temp_flow"].set_index("start_year")["n_stations"]
    first, last = int(d["start_year"].min()), int(d["start_year"].max())
    _register("15_coverage_window.png", "Choosing the coverage window",
              "Every point is one window, starting in the year on the axis "
              f"and ending {w_end.year - 1}. The count is the stations with a "
              "continuous record across the whole window and no break longer "
              f"than {coverage.JOIN_DAYS} days. The top panel is the "
              f"tradeoff. Reaching back to {first} leaves {int(pair[first])} "
              "stations measuring temperature and discharge together; "
              f"starting in {last} leaves {int(pair[last])}. A short window "
              "keeps more stations and a long one keeps more years, so "
              "neither end of the top panel settles the window. The lower "
              "panel plots stations times window length, which is the "
              "concurrent record a window makes available, and that product "
              f"peaks at {int(tf['start_year'])} for temperature and discharge "
              f"and {int(tef['start_year'])} for the three parameters "
              f"together.{in_use} The shaded band is the "
              f"{DROUGHT_YEARS[0]}-{DROUGHT_YEARS[1]} drought: a window "
              f"starting after {DROUGHT_YEARS[0]} covers part of the drought, "
              f"and one starting after {DROUGHT_YEARS[1]} covers none of it.")
    fig.subplots_adjust(top=0.930, bottom=0.085, left=0.085, right=0.865,
                        hspace=0.16)
    _save(fig, "15_coverage_window.png", tight=False)


def fig_cover_temp_flow(st, sp, layers):
    pair = coverage.COVERAGE_SETS["temp_flow"]["parameters"]
    back = 10
    return _coverage_map(
        st, sp, layers, "temp_flow",
        "Temperature and flow measured together",
        "Rule: both parameters from a continuous monitor from the first day "
        "of the window to the last, with no break longer than "
        f"{coverage.JOIN_DAYS} days. Extending the window {back} years "
        f"further back leaves {_wider(sp, pair, back)} stations. Figure 13 "
        "adds conductance to the same rule.",
        "12_cover_temp_flow.png")


def fig_cover_temp_ec_flow(st, sp, layers):
    n_pair = len(coverage.members(sp, "temp_flow"))
    n_trio = len(coverage.members(sp, "temp_ec_flow"))
    return _coverage_map(
        st, sp, layers, "temp_ec_flow",
        "Temperature, conductance and flow measured together",
        "Figure 12's rule with conductance added. Conductance is the scarce "
        f"measurement: {n_pair} stations meet the rule for temperature and "
        f"flow, {n_trio} of those {n_pair} also meet the rule for "
        f"conductance, and adding conductance drops {n_pair - n_trio} "
        "stations. A modeler can calibrate a temperature and salinity model "
        f"directly against those {n_trio}.",
        "13_cover_temp_ec_flow.png")


def fig_cover_salt_load(st, sp, layers):
    # What the squares are, counted rather than asserted. The largest group
    # decides the sentence, so a changed harvest changes the claim with it.
    parameters = coverage.COVERAGE_SETS["salt_load"]["parameters"]
    prim = st[st["is_primary"]].drop_duplicates("station_uid")
    loose = (coverage.members(sp, "salt_load")
             - coverage.members(sp, "salt_load_recorded")) & set(prim["station_uid"])
    # A square fails the circle rule on the span or on the record type: both
    # records are continuous and one of them covers part of the window, or
    # one of them has only discrete samples inside it.
    partial = loose & coverage.stations_with(sp, parameters, rule="within",
                                             continuous=True)
    classes = prim.loc[prim["station_uid"].isin(loose),
                       "channel_class"].value_counts()
    squares = (" A circle marks a station with both records from a continuous "
               "monitor across the whole window. Of the "
               f"{len(loose)} squares, {len(partial)} mark stations with both "
               "records from a continuous monitor and one or both covering "
               "part of the window, and at the other "
               f"{len(loose) - len(partial)} one or both parameters come only "
               "from discrete samples inside the window. "
               f"{int(classes.iloc[0])} of the {len(loose)} squares mark "
               f"stations on a {classes.index[0].replace('_', ' ')} channel."
               if len(classes) else "")
    return _coverage_map(
        st, sp, layers, "salt_load",
        "Where a salt load can be computed",
        "Rule: flow and conductance both measured inside the window, at "
        "whatever frequency each source reports, with no requirement that "
        "either spans the window. A gaged flow and a monthly conductance "
        "sample produce a monthly salt load, which is what a salt balance "
        "needs from a tributary mouth or a permitted outfall."
        f"{squares}",
        "14_cover_salt_load.png",
        strict_key="salt_load_recorded",
        split_label="one or both discrete, or continuous for part of the window")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    st, sp, layers = _load()
    fig_overview(st, sp, layers)
    _parameter_map(st, sp, layers, "water_temperature",
                  "Water temperature monitoring", "02_temperature_stations.png")
    _parameter_map(st, sp, layers, "specific_conductance",
                  "Specific conductance / EC monitoring", "03_salinity_stations.png")
    _parameter_map(st, sp, layers, "discharge",
                  "Streamflow monitoring", "04_flow_stations.png")
    fig_density(st, sp, layers)
    fig_timeline(st, sp)
    fig_longitudinal(st, sp, layers)
    fig_colocation(st, sp, layers)
    cal = pd.read_csv(PROCESSED / "catalog_filtered.csv")
    fig_funnel(cal)
    fig_shortlist(st, layers)
    fig_coverage_time(st, sp)
    tq = fig_cover_temp_flow(st, sp, layers)
    teq = fig_cover_temp_ec_flow(st, sp, layers)
    fig_cover_salt_load(st, sp, layers)
    fig_coverage_window(sp)
    write_captions()
    log.info("coverage %s: temp+flow %d, temp+EC+flow %d (subset: %s)",
             coverage.label(), len(tq), len(teq), teq <= tq)


if __name__ == "__main__":
    main()

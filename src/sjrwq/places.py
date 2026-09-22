"""Named locations used by both the figures and the gap report.

One module, so each coordinate appears once. The boundary anchors sit in
config/domain.yml instead, because `sjrwq.domain` builds the study-area buffers
from them, and `boundary_anchor` returns them from there. Tributary
confluences are not here. `sjrwq.rivers` computes them from the river
geometry, which keeps them on the line as the geometry changes; a hand-entered
confluence drifts off it.
"""

from __future__ import annotations

from .config import domain_config


def boundary_anchor(name: str) -> tuple[float, float]:
    """(lat, lon) of one boundary anchor in config/domain.yml."""
    for a in domain_config()["boundary_anchors"]:
        if a["name"] == name:
            return (a["lat"], a["lon"])
    raise KeyError(f"config/domain.yml lists no boundary anchor named {name!r}")


# Control points that set boundary conditions for the model. Each was checked
# against a harvested station position and resolves to within a few hundred
# meters. Vernalis comes from the boundary anchors, so the study-area buffer,
# km 0 on the mainstem axis, the figure 07 landmark, and the gap report row
# share one point.
FIXED_POINTS: dict[str, tuple[float, float]] = {
    "Friant Dam (upstream boundary)": (36.9983, -119.7061),
    "Mendota Pool": (36.7761, -120.3778),
    "Salt Slough inflow": (37.2477, -120.8521),   # USGS 11261100 at Hwy 165
    "Mud Slough inflow": (37.2624, -120.9066),    # USGS 11262900 nr Gustine
    "Vernalis (downstream boundary)": boundary_anchor("Vernalis"),
}

# Rim dams. Release temperature from each sets the upstream boundary for the
# reach below it.
RESERVOIRS: dict[str, tuple[float, float]] = {
    "Millerton Lake (Friant)": (37.0011, -119.7053),
    "New Melones (Stanislaus)": (37.9469, -120.5253),
    "Don Pedro (Tuolumne)": (37.7017, -120.4217),
    "Lake McClure (Merced)": (37.5906, -120.2686),
    "Hensley Lake (Fresno R)": (37.1319, -119.8858),
    "Eastman Lake (Chowchilla R)": (37.2136, -119.9750),
}

# Short reservoir labels for map annotation, where the parenthetical river name
# would collide with everything around it.
RESERVOIR_LABEL = {
    "Millerton Lake (Friant)": "Millerton",
    "New Melones (Stanislaus)": "New Melones",
    "Don Pedro (Tuolumne)": "Don Pedro",
    "Lake McClure (Merced)": "McClure",
    "Hensley Lake (Fresno R)": "Hensley",
    "Eastman Lake (Chowchilla R)": "Eastman",
}

# Towns, for orientation only. Stockton sits outside the study area and gets a
# label regardless: it is the nearest city to the downstream boundary, and the
# northern end of the map otherwise holds no landmark a reader recognises.
TOWNS: dict[str, tuple[float, float]] = {
    "Stockton": (37.9577, -121.2908),
    "Modesto": (37.6391, -120.9969),
    "Merced": (37.3022, -120.4830),
    "Los Banos": (37.0583, -120.8499),
    "Madera": (36.9613, -120.0607),
    "Fresno": (36.7378, -119.7871),
    "Sonora": (37.9829, -120.3822),
}


# Extra orientation points for the longitudinal figure, given as station uids
# rather than coordinates. The figure places each label at the station's
# `mainstem_km`, so a uid here has to be a mainstem station: a tributary gage
# named for a nearby town has a null `mainstem_km`, and the figure skips it.
# The figure also skips a landmark whose station falls out of the inventory.
MAINSTEM_LANDMARK_STATIONS: dict[str, str] = {
    "usgs:USGS-11274570": "Patterson",
    "usgs:USGS-11274550": "Crows Landing",
    "usgs:USGS-11260815": "Stevinson",
    "wqp:CEDEN-541MAD007": "Sack Dam",
    "cdec:SJB": "Bifurcation",
    "cdec:GRF": "Gravelly Ford",
}

# Anchor stations named in the left margin of the timeline figure. One row per
# station puts a name under half a point tall, so the figure names six gages a
# reader is likely to know and leaves the rest to figure 10. Two on the
# mainstem at the downstream end, one at the upstream boundary, and one gage on
# each of the three east-side tributaries.
TIMELINE_ANCHORS: dict[str, str] = {
    "usgs:USGS-11303500": "Vernalis",
    "usgs:USGS-11304200": "Mossdale",
    "usgs:USGS-11303000": "Stanislaus",
    "usgs:USGS-11290000": "Tuolumne",
    "usgs:USGS-11272500": "Merced",
    "usgs:USGS-11251000": "Friant",
}

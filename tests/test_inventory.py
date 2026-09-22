"""Verification checks from the project plan.

Unit tests run in every case. The integration checks skip when data/processed
is empty, so a fresh clone passes before the first harvest.

    pytest -q
"""

from __future__ import annotations

import ast
import json
import pathlib
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import geopandas as gpd
import pandas as pd
import pytest

from sjrwq.config import PROCESSED, ParameterLookup, domain_config
from sjrwq.dedupe import normalize_name

# Stations a correct harvest of this basin has to include.
SPOT_CHECKS = [
    ("usgs", "USGS-11303500", "San Joaquin River near Vernalis"),
    ("usgs", "USGS-11273400", "San Joaquin River above the Merced near Newman"),
    ("dwr_wdl", "B00470", "Salt Slough near Stevinson (DWR WDL)"),
    ("usbr_rise", "413", "Millerton Lake and Friant Dam (RISE)"),
    ("cdec", "VNS", "Vernalis (CDEC)"),
    ("cdec", "MSD", "Mossdale"),
    ("esmr", "750006", "Turlock RWQCF outfall to the San Joaquin"),
]

# Stations that must be EXCLUDED: they are downstream of the boundary anchors,
# in Stockton and the interior Delta, and would silently widen the domain.
MUST_EXCLUDE = [
    ("cdec", "SJG", "San Joaquin River at Garwood Bridge, Stockton"),
]

MAINSTEM_LENGTH_KM = (450, 500)


def _load_sp() -> pd.DataFrame:
    path = PROCESSED / "station_parameter.parquet"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.inventory` first")
    return pd.read_parquet(path)


def _load_catalog() -> pd.DataFrame:
    path = PROCESSED / "catalog.csv"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.inventory` first")
    return pd.read_csv(path)


def _load_filtered() -> pd.DataFrame:
    path = PROCESSED / "catalog_filtered.csv"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.inventory` first")
    return pd.read_csv(path)


def _load_stations() -> gpd.GeoDataFrame:
    path = PROCESSED / "stations.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.harvest` and `python -m sjrwq.inventory` first")
    return gpd.read_file(path, layer="stations")


# --- unit tests -------------------------------------------------------------

def test_parameter_lookup_is_unambiguous():
    lk = ParameterLookup.build()
    assert lk.by_usgs_pcode["00010"] == "water_temperature"
    assert lk.by_usgs_pcode["00095"] == "specific_conductance"
    assert lk.by_cdec_sensor[146] == "water_temperature"
    assert lk.by_cdec_sensor[100] == "specific_conductance"
    assert lk.by_wqp_name["temperature, water"] == "water_temperature"
    assert lk.by_esmr_parameter["temperature"] == "water_temperature"
    assert lk.by_esmr_parameter["electrical conductivity @ 25 deg. c"] == \
        "specific_conductance"


def test_domain_excludes_delta_and_sacramento_side():
    cfg = domain_config()
    assert "18040003" not in cfg["huc8_in_scope"], "Delta must be excluded"
    for huc in ("18040012", "18040013", "18040051"):
        assert huc in cfg["huc8_excluded"]
    # A subbasin is in scope when its water passes Vernalis. The Calaveras
    # joins the San Joaquin at Stockton, below Vernalis, so a station on it
    # cannot close a balance at the modeled outlet.
    assert "18040011" in cfg["huc8_excluded"]
    assert "18040011" not in cfg["huc8_in_scope"]
    assert len(cfg["huc8_in_scope"]) == 8


def test_bbox_covers_every_in_scope_huc8():
    """The harvest bbox contains the whole study area.

    The Upper Stanislaus reaches 38.52 N. A bbox-filtered source returns fewer
    stations rather than an error when the box falls short, so a missing corner
    shows up here and in no other check.
    """
    path = PROCESSED / "domain.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.domain` first")
    cfg = domain_config()
    west, south, east, north = cfg["bbox"]
    huc8 = gpd.read_file(path, layer="huc8")
    inside = huc8[huc8["huc8"].isin(cfg["huc8_in_scope"])]
    w, s, e, n = inside.total_bounds
    assert west <= w and south <= s and east >= e and north >= n, (
        f"bbox {cfg['bbox']} does not contain the in-scope HUC8 extent "
        f"{[round(v, 3) for v in (w, s, e, n)]}"
    )


def test_name_normalization_collapses_agency_spellings():
    a = normalize_name("SAN JOAQUIN R NEAR VERNALIS CA")
    b = normalize_name("San Joaquin River nr Vernalis")
    assert a.startswith("sj r") and b.startswith("sj r")
    assert "vernalis" in a and "vernalis" in b


# --- integration checks -----------------------------------------------------

def test_spot_check_stations_present():
    st = _load_stations()
    have = set(zip(st["source"], st["source_station_id"]))
    missing = [f"{s}:{i} ({label})" for s, i, label in SPOT_CHECKS if (s, i) not in have]
    assert not missing, f"expected stations absent from inventory: {missing}"


def test_downstream_stations_are_clipped_out():
    """The AOI stops at the boundary anchors. CDEC publishes Garwood Bridge,
    17 km below Mossdale in Stockton, so the harvest returns it and its absence
    here comes from the clip rather than from a gap in the harvest."""
    st = _load_stations()
    have = set(zip(st["source"], st["source_station_id"]))
    leaked = [f"{s}:{i} ({label})" for s, i, label in MUST_EXCLUDE if (s, i) in have]
    assert not leaked, f"stations below the boundary leaked into the AOI: {leaked}"


def test_vernalis_collapses_across_sources():
    """USGS 11303500 and CDEC VNS are the same gage and must share a uid."""
    st = _load_stations()
    usgs = st[st["source_station_id"] == "USGS-11303500"]
    cdec = st[(st["source"] == "cdec") & (st["source_station_id"] == "VNS")]
    assert not usgs.empty and not cdec.empty
    assert usgs.iloc[0]["station_uid"] == cdec.iloc[0]["station_uid"], (
        "Vernalis did not deduplicate across USGS and CDEC"
    )


def test_boundary_stations_flagged_not_dropped():
    """Vernalis sits inside the excluded Delta HUC and survives only via the
    anchor buffer, so it must be present and flagged."""
    st = _load_stations()
    vns = st[st["source_station_id"] == "USGS-11303500"].iloc[0]
    assert bool(vns["boundary"]) is True
    assert bool(st["boundary"].astype(bool).any())


def test_station_counts_are_plausible():
    st = _load_stations()
    n_unique = st["station_uid"].nunique()
    assert n_unique > 500, (
        f"only {n_unique} unique stations; a HUC query is probably malformed, "
        "since this basin is densely monitored"
    )
    wqp = st[st["source"] == "wqp"]
    assert len(wqp) > 100, "WQP returned too few stations, check the HUC filter"


def test_every_station_has_a_uid_and_one_primary():
    st = _load_stations()
    assert st["station_uid"].notna().all()
    per = st[st["is_primary"]].groupby("station_uid").size()
    assert (per == 1).all(), "each cluster must have exactly one primary record"


def test_core_parameters_have_coverage():
    path = PROCESSED / "station_parameter.parquet"
    if not path.exists():
        pytest.skip("no station_parameter.parquet")
    sp = pd.read_parquet(path)
    for param in ("water_temperature", "specific_conductance", "discharge"):
        n = sp.loc[sp["parameter"] == param, "station_uid"].nunique()
        assert n > 20, f"only {n} stations report {param}"


def test_esmr_longitudes_were_sign_corrected():
    """About a fifth of eSMR records publish a positive longitude.

    California lies west of the prime meridian, so a positive value can
    only be a dropped minus sign. Left alone it puts Tracy's outfall in
    Kyrgyzstan, where the study-area clip quietly deletes it, and the loss
    looks like an absence of data rather than a coordinate bug.
    """
    st = _load_stations()
    e = st[st["source"] == "esmr"]
    if e.empty:
        pytest.skip("no eSMR harvest present")
    assert (e["lon"] < 0).all(), "eSMR longitudes were not sign-corrected"
    assert (e["lon"].between(-121.7, -118.8)).all()


def test_esmr_reaches_dischargers_the_wqp_misses():
    """eSMR exists in the inventory to supply municipal return flows.

    If the crosswalk or the place-type mapping breaks, this collapses quietly:
    the stations still land, they just stop reporting the three parameters that
    make an outfall usable as a boundary condition.
    """
    path = PROCESSED / "station_parameter.parquet"
    if not path.exists():
        pytest.skip("no station_parameter.parquet")
    st = _load_stations()
    sp = pd.read_parquet(path)
    e = sp[sp["source"] == "esmr"]
    if e.empty:
        pytest.skip("no eSMR harvest present")
    for param in ("water_temperature", "specific_conductance", "discharge"):
        n = e.loc[e["parameter"] == param, "station_uid"].nunique()
        assert n > 10, f"only {n} eSMR stations report {param}"
    types = set(st.loc[st["source"] == "esmr", "station_type"])
    assert {"effluent", "receiving_water"} <= types, (
        f"eSMR place types did not map through: {sorted(types)}"
    )


def test_mainstem_length_is_realistic():
    from sjrwq.rivers import merged_mainstem_line
    path = PROCESSED / "domain.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.domain` and `python -m sjrwq.rivers` first")
    ms = gpd.read_file(path, layer="mainstem")
    km = merged_mainstem_line(ms).length / 1000.0
    lo, hi = MAINSTEM_LENGTH_KM
    assert lo < km < hi, f"mainstem length {km:.0f} km outside expected {lo}-{hi} km"


def test_tributaries_reach_the_mainstem():
    """Each contributing tributary reaches the San Joaquin.

    NLDI navigation starts at the gage, and the gages that anchor these traces
    sit well inland, so `rivers.tributaries` adds a downstream trace from each
    gage. Upstream navigation alone ends the Tuolumne at Modesto, 20 km short
    of the river it flows into.
    """
    from sjrwq.rivers import CONTRIBUTING, merged_mainstem_line
    path = PROCESSED / "domain.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.rivers` first")
    trib = gpd.read_file(path, layer="tributaries").to_crs("EPSG:3310")
    line = merged_mainstem_line(gpd.read_file(path, layer="mainstem"))
    for river in CONTRIBUTING:
        geom = trib[trib["river"] == river].union_all()
        assert not geom.is_empty, f"{river} missing from the tributaries layer"
        gap_m = geom.distance(line)
        assert gap_m < 100, (
            f"{river} stops {gap_m / 1000:.1f} km short of the San Joaquin"
        )
    assert set(trib["river"]) == set(CONTRIBUTING), (
        "the tributaries layer should hold exactly the contributing rivers"
    )


def test_confluences_are_in_correct_downstream_order():
    """The three confluences fall in their real order along the mainstem.

    NLDI downstream navigation continues along the San Joaquin after a
    tributary joins, so every point below a confluence also touches the
    mainstem. `rivers.confluences` takes the touch furthest upstream; the one
    furthest downstream puts the Stanislaus and the Tuolumne on one coordinate
    near Vernalis. Going upstream, the Stanislaus joins at about 4 km, the
    Tuolumne at about 19 km, and the Merced at about 77 km.
    """
    from sjrwq.rivers import confluences, merged_mainstem_line, river_distance_km
    path = PROCESSED / "domain.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.rivers` first")
    conf = confluences()
    assert len(conf) == 3, f"expected 3 confluences, got {sorted(conf)}"

    ms = gpd.read_file(path, layer="mainstem")
    line = merged_mainstem_line(ms)
    pts = gpd.GeoDataFrame(
        {"name": list(conf)},
        geometry=gpd.points_from_xy([v[1] for v in conf.values()],
                                    [v[0] for v in conf.values()]),
        crs="EPSG:4326",
    )
    km = dict(zip(pts["name"], river_distance_km(pts, line)))
    stan = km["Stanislaus River confluence"]
    tuol = km["Tuolumne River confluence"]
    merc = km["Merced River confluence"]
    assert stan < tuol < merc, f"confluence order wrong: {km}"
    assert 0 < stan < 15, f"Stanislaus should join a few km above Vernalis, got {stan:.1f} km"
    assert 60 < merc < 95, f"Merced should join near Hills Ferry, got {merc:.1f} km"


def test_the_mainstem_starts_at_the_vernalis_anchor():
    """Km 0 on the mainstem axis is the Vernalis boundary anchor.

    NLDI returns the whole flowline holding the Vernalis gage, and that
    flowline runs on below the gage. `merged_mainstem_line` cuts it at the
    anchor in config/domain.yml, so distance along the line is distance
    upstream from Vernalis and the Vernalis gage sits at km 0.
    """
    from shapely.geometry import Point

    from sjrwq.places import boundary_anchor
    from sjrwq.rivers import merged_mainstem_line, river_distance_km
    path = PROCESSED / "domain.gpkg"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.rivers` first")
    line = merged_mainstem_line(gpd.read_file(path, layer="mainstem"))
    lat, lon = boundary_anchor("Vernalis")
    anchor = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs("EPSG:3310").iloc[0]
    assert line.project(anchor) < 1.0, "the line starts away from the anchor"

    st = _load_stations()
    gage = st[st["source_station_id"] == "USGS-11303500"]
    assert not gage.empty
    assert float(river_distance_km(gage, line).iloc[0]) < 0.1
    assert float(gage["mainstem_km"].iloc[0]) < 0.1

# ---------------------------------------------------------------- units ----

def test_fahrenheit_converts_to_celsius():
    from sjrwq import units
    r = units.resolve("water_temperature", "degC", "DEG F")
    assert r.convertible
    # 212 F is 100 C.
    assert abs(212 * r.factor + r.offset - 100.0) < 1e-6


@pytest.mark.parametrize("unit", ["mS/cm", "mmho/cm", "mmhos/cm"])
def test_millisiemens_scales_to_microsiemens(unit):
    from sjrwq import units
    r = units.resolve("specific_conductance", "uS/cm @25C", unit)
    assert r.convertible and r.factor == 1000.0


def test_percent_is_not_a_concentration():
    from sjrwq import units
    r = units.resolve("calcium", "mg/L", "%")
    assert not r.convertible
    assert "percent" in r.note


def test_load_units_are_refused():
    from sjrwq import units
    r = units.resolve("total_dissolved_solids", "mg/L", "tons/day")
    assert not r.convertible and "load" in r.note
    # tons per acre-foot, by contrast, is a real concentration.
    ok = units.resolve("total_dissolved_solids", "mg/L", "tons/ac ft")
    assert ok.convertible and 700 < ok.factor < 800


def test_pool_elevation_is_not_storage():
    from sjrwq import units
    r = units.resolve("reservoir_storage", "acre-ft", "FEET")
    assert not r.convertible and "elevation" in r.note


def test_milliequivalents_convert_through_the_equivalent_weight():
    """meq/L times the ion's mass over its charge is mg/L."""
    from sjrwq import units
    hardness = units.resolve("hardness", "mg/L as CaCO3", "meq/L")
    assert hardness.convertible and hardness.factor == pytest.approx(50.04)
    chloride = units.resolve("chloride", "mg/L", "meq/l")
    assert chloride.factor == pytest.approx(35.453)
    micro = units.resolve("chloride", "mg/L", "ueq/L")
    assert micro.factor == pytest.approx(chloride.factor / 1000.0)
    # Total nitrogen sums several species, so it has no single ionic charge.
    nitrogen = units.resolve("total_nitrogen", "mg/L as N", "meq/L")
    assert not nitrogen.convertible and "charge" in nitrogen.note


@pytest.mark.parametrize(("parameter", "canonical", "name", "factor"), [
    ("nitrate", "mg/L as N", "Nitrate, Total (as NO3)", 0.2259),
    ("ammonia", "mg/L as N", "Ammonia (as NH3)", 0.8224),
    ("phosphorus", "mg/L as P", "Orthophosphate (as PO4)", 0.3261),
    ("sulfate", "mg/L", "Sulfate, Total (as S)", 2.996),
    # parameters.yml gives calcium and magnesium in mg/L of the ion, and the
    # NURE survey files both "as CaCO3" in the portal.
    ("calcium", "mg/L", "Calcium (as CaCO3)", 0.4004),
    ("magnesium", "mg/L", "Magnesium (as CaCO3)", 0.2428),
    # A name stating the canonical basis, or none, takes a factor of 1.
    ("nitrate", "mg/L as N", "Nitrate, Total (as N)", 1.0),
    ("nitrate", "mg/L as N", "Nitrate", 1.0),
])
def test_a_basis_stated_in_the_name_sets_the_factor(parameter, canonical, name, factor):
    from sjrwq import units
    r = units.resolve(parameter, canonical, "mg/L", name)
    assert r.convertible, r.note
    assert r.factor == pytest.approx(factor, rel=1e-3)


def test_a_mixed_or_joined_basis_stays_unconverted():
    """One row, one factor. A row holding two bases has no single factor."""
    from sjrwq import units
    mix = units.resolve("alkalinity", "mg/L as CaCO3", "mg/L",
                        "Alkalinity (as HCO3/CO3/OH)")
    assert not mix.convertible and "mix of bases" in mix.note
    joined = units.resolve("nitrate", "mg/L as N", "mg/L",
                           "Nitrate, Total (as N);Nitrate, Total (as NO3)")
    assert not joined.convertible and "joined" in joined.note
    # `annotate` resolves each name apart, so two names under one unit string
    # take two factors.
    sp = pd.DataFrame({"parameter": ["nitrate", "nitrate"],
                       "unit": ["mg/L", "mg/L"],
                       "parameter_source_name": ["Nitrate, Total (as N)",
                                                 "Nitrate, Total (as NO3)"]})
    out = units.annotate(sp, {"nitrate": "mg/L as N"})
    assert out["unit_factor"].round(4).tolist() == [1.0, 0.2259]


def test_every_unit_row_states_a_factor_or_a_reason():
    sp = _load_sp()
    assert "unit_convertible" in sp.columns
    good = sp[sp["unit_convertible"].astype(bool)]
    bad = sp[~sp["unit_convertible"].astype(bool)]
    assert good["unit_factor"].notna().all()
    assert (bad["unit_note"].astype(str).str.len() > 0).all()


# ------------------------------------------------------------ junk records --

def test_no_demo_or_air_quality_records_survive():
    st = _load_stations()
    ops = st["operator"].astype(str)
    assert not ops.str.contains("DEMOTEST", case=False).any()
    assert not ops.eq("Air Quality System").any()
    ids = st["source_station_id"].astype(str)
    assert not ids.str.contains(r"1231231231231\d\d", regex=True).any()


# --------------------------------------------------------- classification ---

@pytest.mark.parametrize(("name", "expected"), [
    ("CAL AQUEDUCT CHECK 13 (KA007089)", "conveyance"),
    ("DELTA MENDOTA CA TO MENDOTA POOL", "conveyance"),
    ("PACHECO PUMPING PLANT (SLR00000)", "conveyance"),
    ("TURLOCK CN NR LA GRANGE CA", "conveyance"),
    ("TILE DRAIN BVS6016", "drain"),
    ("HARDING DRAIN A CARPENTER RD NR PATTERSON CA", "drain"),
    ("SAN LUIS DR SITE A NR S DOS PALOS CA", "drain"),
    ("SAN JOAQUIN R NR VERNALIS CA", "natural"),
    ("MUD SLOUGH NR GUSTINE CA", "natural"),
    # A creek measured below a mine drain is still a creek.
    ("SAN CARLOS C BL NEW IDRIA MINE DRAIN A IDRIA CA", "natural"),
    ("05N08E26P001M", "groundwater"),
])
def test_channel_class(name, expected):
    from sjrwq.classify import channel_class
    assert channel_class(name) == expected


def test_conveyance_is_flagged_not_dropped():
    st = _load_stations()
    assert "channel_class" in st.columns
    classes = set(st["channel_class"].dropna())
    assert {"natural", "conveyance", "drain"} <= classes
    # Cal Aqueduct Check 13 is a real 36-year record and belongs in the
    # inventory. It just has to stay out of the San Joaquin class.
    check13 = st[st["name"].astype(str).str.contains("CAL AQUEDUCT CHECK", na=False)]
    assert not check13.empty
    assert (check13["channel_class"] == "conveyance").all()


@pytest.mark.parametrize(("name", "location_type", "expected"), [
    # USGS files these test holes under groundwater, and the portal gives the
    # hyporheic zone as their sample medium, whatever the name reads as.
    ("HYPORHEIC X-SEC IN SAN JOAQUIN R", "Test hole not completed as a well",
     "groundwater"),
    ("HYPORHEIC X-SEC IN SAN JOAQUIN R", "Hyporheic-zone well", "groundwater"),
    ("SITE 4", "Groundwater drain", "drain"),
    ("SITE 4", "Lake, Reservoir, Impoundment", "reservoir"),
    ("SITE 4", "River/Stream Perennial", "natural"),
    ("SITE 4", "River/Stream Intermittent", "natural"),
    ("SITE 4", "Spring", "natural"),
    # Agencies apply River/Stream to canals as well, so the name decides.
    ("TURLOCK CN NR LA GRANGE CA", "River/Stream", "conveyance"),
    ("SITE 4", "River/Stream", "unknown"),
])
def test_wqp_location_type_decides_before_the_name(name, location_type, expected):
    from sjrwq.classify import channel_class
    assert channel_class(name, "discrete_grab", location_type) == expected


def test_a_spill_is_a_return_and_a_spill_elevation_is_not():
    """MID and TID name a canal's outlet to a river by the word Spill alone."""
    from sjrwq.classify import channel_class, conveyance_role
    assert channel_class("Spenker Spill") == "conveyance"
    assert conveyance_role("Spenker Spill", "conveyance") == "return"
    assert channel_class("Waterford L.M. Spill") == "conveyance"
    assert channel_class("TULLOCH DAM SPILLWAY") == "conveyance"
    # CDEC's Farmington names a dam's spill elevation, not a canal outlet.
    assert channel_class("FARMINGTON (SPILL ELEV 156.5 FT)") != "conveyance"
    # The drain pattern runs before the conveyance pattern.
    assert channel_class("Main Drain Spill") == "drain"


# ------------------------------------------------------------- position ----

def test_mainstem_km_anchors():
    st = _load_stations()
    assert "mainstem_km" in st.columns
    prim = st[st["is_primary"]].set_index("source_station_id")
    vernalis = prim.loc["USGS-11303500", "mainstem_km"]
    friant = prim.loc["USGS-11250110", "mainstem_km"]   # release at Friant Dam
    assert 0 <= float(vernalis) < 3
    assert 300 < float(friant) < 325


def test_off_river_stations_are_not_named_for_a_river():
    from sjrwq.rivers import ON_CHANNEL_M
    st = _load_stations()
    far = st[st["distance_to_river_m"].astype(float) > ON_CHANNEL_M]
    assert not far.empty
    assert far["river"].isna().all()


def test_river_km_defaults_to_the_channel_tolerance():
    import inspect

    from sjrwq.rivers import ON_CHANNEL_M, river_distance_km
    default = inspect.signature(river_distance_km).parameters["max_offset_m"].default
    assert default == ON_CHANNEL_M


def test_one_tolerance_names_the_river_and_places_the_km():
    """A station with a mainstem kilometer is named for a river.

    `rivers.ON_CHANNEL_M` decides both. A wider tolerance for the kilometer
    puts wells, canals, and outfalls on the mainstem axis with a null river
    name, where the gap tables and the shortlist order read them as river
    stations.
    """
    from sjrwq.rivers import ON_CHANNEL_M
    st = _load_stations()
    placed = st[st["mainstem_km"].notna()]
    assert not placed.empty
    unnamed = placed[placed["river"].isna()]
    assert unnamed.empty, (
        f"{len(unnamed)} stations have a mainstem_km and no river: "
        f"{unnamed['name'].head(5).tolist()}")
    assert (placed["distance_to_river_m"].astype(float) <= ON_CHANNEL_M).all()


def test_mainstem_landmark_stations_have_a_river_km():
    """Figure 7 places each landmark at its station's `mainstem_km`.

    A tributary gage named for a nearby town has a null `mainstem_km`, and
    figure 7 then leaves out its label. Crows Landing is USGS 11274550 on the
    San Joaquin, not the Orestimba Creek gage beside it.
    """
    from sjrwq.places import MAINSTEM_LANDMARK_STATIONS
    st = _load_stations()
    prim = st[st["is_primary"]].set_index("station_uid")
    km = {label: prim["mainstem_km"].get(uid)
          for uid, label in MAINSTEM_LANDMARK_STATIONS.items()}
    assert "Crows Landing" in km
    missing = [label for label, k in km.items() if k is None or pd.isna(k)]
    assert not missing, f"landmarks with no mainstem_km: {missing}"


# ----------------------------------------------- record type and frequency -

def test_record_type_and_frequency_are_populated_and_known():
    sp = _load_sp()
    assert {"record_type", "frequency"} <= set(sp.columns)
    assert set(sp["record_type"].dropna()) <= {"continuous", "discrete"}
    assert set(sp["frequency"].dropna()) <= {
        "sub-daily", "daily", "weekly", "monthly", "quarterly", "annual",
        "irregular", "unknown"}
    # CDEC sondes report hourly or on event.
    cdec = sp[sp["source"] == "cdec"]
    assert (cdec["frequency"] == "sub-daily").any()
    assert (cdec["record_type"] == "continuous").any()


def test_record_type_and_frequency_are_two_columns():
    """A monthly sample is discrete at a known frequency, not an unknown one.

    One column cannot tell a monthly sampling program from two samples thirty
    years apart. The portal and CEDEN publish no stated interval, so their
    frequency comes from what they do publish.
    """
    sp = _load_sp()
    for src in ("wqp", "ceden"):
        rows = sp[sp["source"] == src]
        if rows.empty:
            continue
        assert (rows["record_type"] == "discrete").all()
        assert (rows["frequency"] != "unknown").any(), \
            f"{src} publishes a count and a span, so some rows have a frequency"
    # Frequency alone does not make a record continuous.
    monthly = sp[sp["frequency"] == "monthly"]
    assert not monthly.empty
    assert (monthly["record_type"] == "discrete").all()


def test_continuous_uses_the_record_type_not_the_station_type():
    cal = _load_filtered()
    assert {"temp_continuous", "ec_continuous", "pair_active", "usable"} <= set(cal.columns)
    # Every continuous row must be continuous in both parameters, by definition.
    cont = cal[cal["continuous"].astype(bool)]
    assert cont["temp_continuous"].astype(bool).all()
    assert cont["ec_continuous"].astype(bool).all()


def test_pair_active_is_about_the_pair_not_the_station():
    cal = _load_filtered()
    # A station can be active while its temperature and EC records are not.
    assert (cal["active"].astype(bool) & ~cal["pair_active"].astype(bool)).any()


def test_pair_active_needs_both_records():
    """A station holding one of the two records has no pair to be current.

    `min` skips a missing end date, so an unguarded minimum reads a current
    thermograph with no conductance record as a current pair.
    """
    cal = _load_filtered()
    solo = cal[~cal["has_pair"].astype(bool)]
    assert not solo.empty
    assert not solo["pair_active"].astype(bool).any()


def test_discrete_counts_skip_continuous_rows():
    """`_n_discrete` counts samples, not the readings a continuous monitor logs.

    eSMR and EDI publish a count on continuous rows too. cdec:OH1 records
    temperature and conductance from continuous monitors alone, so both of its
    counts are blank.
    """
    from sjrwq.coverage import station_level
    cf, sp = _load_filtered(), _load_sp()
    oh1 = cf[cf["station_uid"] == "cdec:OH1"]
    assert not oh1.empty
    assert oh1[["temp_n_discrete", "ec_n_discrete"]].isna().all(axis=None)
    core = station_level(sp)
    for param, short in (("water_temperature", "temp"),
                         ("specific_conductance", "ec")):
        rows = core[core["parameter"] == param]
        cont_only = (rows.groupby("station_uid")["record_type"]
                     .agg(lambda c: (c == "continuous").all()))
        ids = set(cont_only[cont_only].index)
        counted = cf.loc[cf["station_uid"].isin(ids), f"{short}_n_discrete"]
        assert counted.isna().all(), \
            f"{int(counted.notna().sum())} continuous-only {short} records have a count"


def test_a_same_day_record_has_no_frequency():
    """Replicates on one day make a count and no span, so no interval."""
    from sjrwq.classify import SAMPLE_COUNT_BANDS, measured_frequency
    start = pd.Series(pd.to_datetime(["2015-06-01", "2015-06-01", "2015-01-01"]))
    end = pd.Series(pd.to_datetime(["2015-06-01", "2015-06-01", "2015-12-31"]))
    n = pd.Series([3, 1, 13])
    interval, freq = measured_frequency(start, end, n, SAMPLE_COUNT_BANDS)
    assert freq.tolist() == ["unknown", "unknown", "monthly"]
    assert interval.isna().tolist() == [True, True, False]


# --------------------------------------------------------------- sources ---

def test_all_eight_sources_contribute():
    st = _load_stations()
    sp = _load_sp()
    expected = {"usgs", "cdec", "wqp", "esmr", "ceden", "dwr_wdl", "usbr_rise",
                "edi"}
    assert expected <= set(st["source"])
    assert expected <= set(sp["source"])


def test_salinity_comes_from_dwr_and_edi():
    """Two sources publish a salinity record in this basin, not one."""
    sp = _load_sp()
    sal = sp[sp["parameter"] == "salinity"]
    assert not sal.empty
    assert {"dwr_wdl", "edi"} <= set(sal["source"])


# ---------------------------------------------------------------- README ---

def test_readme_counts_match_the_data():
    """The generated block agrees with the files it describes.

    A hand-typed count goes stale within one harvest, so `sjrwq.gaps` writes
    the block from data/processed. This check fails when the block and the
    files come from different runs.
    """
    readme = pathlib.Path(__file__).resolve().parents[1] / "README.md"
    if not readme.exists():
        pytest.skip("no README")
    text = readme.read_text()
    if "harvest-summary:start" not in text:
        pytest.skip("README has no generated block")
    block = text.split("harvest-summary:start -->")[1].split("<!--")[0]
    if "Run `python -m sjrwq.gaps`" in block:
        pytest.skip("summary block not generated yet")

    st = _load_stations()
    cat = _load_catalog()
    assert f"{len(st):,} source records" in block
    assert f"{len(cat):,} distinct stations" in block


# ---------------------------------------------------------------- dedupe ---

def test_wide_radius_does_not_merge_a_well_field():
    """State well numbers differ only in their last digits.

    `012S013E31A005M` and `012S013E31A006M` are two wells on one drill pad,
    meters apart, and they score 97 on string similarity. The outer radius
    requires the numbers in two names to agree, which keeps a well field of
    seventy-five such wells from collapsing into one station.
    """
    from sjrwq.dedupe import _wide_match_ok, normalize_name
    a = normalize_name("012S013E31A005M")
    b = normalize_name("012S013E31A006M")
    assert not _wide_match_ok(a, b)
    assert not _wide_match_ok(normalize_name("CHECK 20"), normalize_name("CHECK 21"))
    # A river station named two ways by two agencies does survive.
    assert _wide_match_ok(
        normalize_name("SAN JOAQUIN R BL HWY 41 NR PINEDALE CA"),
        normalize_name("San Joaquin River below Highway 41 near Pinedale"))


def test_opposing_terms_block_a_merge():
    from sjrwq.dedupe import _opposed, normalize_name
    assert _opposed(normalize_name("Orestimba Creek above the outfall"),
                    normalize_name("Orestimba Creek below the outfall"))
    assert not _opposed(normalize_name("Tuolumne River at Modesto"),
                        normalize_name("TUOLUMNE R A MODESTO CA"))


def test_upstream_and_downstream_abbreviations_are_opposed():
    """CEDEN writes upstream and downstream as u/s and d/s."""
    from sjrwq.dedupe import _opposed
    up = normalize_name("Orestimba Creek u/s outfall")
    down = normalize_name("Orestimba Creek d / s outfall")
    assert "us" in up.split() and "ds" in down.split()
    assert _opposed(up, down)


# An eSMR plant files each monitoring point under one facility name.
TURLOCK = "Turlock City, Turlock Regional Water Quality Control Facility"


def test_point_codes_keep_their_number():
    """EFF-001 and EFF-002 are two outfalls, not one outfall named twice.

    Turlock files EFF-001 and EFF-002 31 m apart, and RSW-001 and RSW-002
    48 m apart, so the number in the code is the whole difference.
    """
    from sjrwq.dedupe import _opposed, _point_codes
    eff1, eff2 = (normalize_name(f"{TURLOCK} EFF-00{k}") for k in (1, 2))
    rsw1, rsw2 = (normalize_name(f"{TURLOCK} RSW-00{k}") for k in (1, 2))
    assert _point_codes(eff1) == {"eff 001"}
    assert _point_codes(rsw2) == {"rsw 002"}
    assert _opposed(eff1, eff2)
    assert _opposed(rsw1, rsw2)
    assert not _opposed(eff1, normalize_name(f"{TURLOCK} EFF-001"))


def test_feature_words_have_to_overlap():
    """Two names calling the feature different things are two features."""
    from sjrwq.dedupe import _features_conflict
    assert _features_conflict(normalize_name("Salt Slough near Stevinson"),
                              normalize_name("San Joaquin River near Stevinson"))
    # A river and a dam against the river alone share the river.
    assert not _features_conflict(normalize_name("CHOWCHILLA R BLW BUCHANAN DAM"),
                                  normalize_name("CHOWCHILLA R BL BUCHANAN DM NR RY"))
    # A name with no feature word conflicts with neither.
    assert not _features_conflict(normalize_name("RIPON"),
                                  normalize_name("STANISLAUS R A RIPON"))


def test_district_abbreviations_separate_two_canals():
    """TID and MID each run a canal out of La Grange, at one coordinate.

    "mid" is also the English word, so one district against none blocks no
    merge.
    """
    from sjrwq.dedupe import _opposed
    assert _opposed(normalize_name("TID CANAL AT LA GRANGE"),
                    normalize_name("MID CANAL AT LA GRANGE"))
    assert not _opposed(normalize_name("MERCED R AT HWY 59 (MID)"),
                        normalize_name("MERCED R NR HWY 59"))


def test_neighbors_merge_by_what_their_names_say():
    """`assign_station_uid` on agency coordinates for pairs tens of meters apart.

    The Chowchilla records are one gage published three times. Every other
    pair is two points: a slough and the river 36 m from it, two canals at
    one coordinate, and two outfalls and two receiving-water points at one
    plant.
    """
    from sjrwq.dedupe import assign_station_uid
    rows = [
        ("dwr_wdl", "B00470", "Salt Slough near Stevinson", 37.294267, -120.851233),
        ("dwr_wdl", "B07400", "San Joaquin River near Stevinson", 37.294000, -120.851000),
        ("usgs", "USGS-11259000", "CHOWCHILLA R BL BUCHANAN DAM NR RAYMOND CA",
         37.215501, -119.991276),
        ("cdec", "BCQ", "CHOWCHILLA R BLW BUCHANAN DAM", 37.215600, -119.990300),
        ("wqp", "CALWR_WQX-B6415900", "CHOWCHILLA R BL BUCHANAN DM NR RY",
         37.214900, -119.991000),
        ("cdec", "TIL", "TID CANAL AT LA GRANGE", 37.672, -120.443),
        ("cdec", "MID", "MID CANAL AT LA GRANGE", 37.672, -120.443),
        ("esmr", "750006", f"{TURLOCK} EFF-001", 37.462778, -121.032500),
        ("esmr", "750007", f"{TURLOCK} EFF-002", 37.463056, -121.032500),
        ("esmr", "750012", f"{TURLOCK} RSW-001", 37.463609, -120.930945),
        ("esmr", "750013", f"{TURLOCK} RSW-002", 37.463690, -120.931476),
    ]
    frame = pd.DataFrame(rows, columns=["source", "source_station_id", "name",
                                        "lat", "lon"])
    g = gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(frame["lon"], frame["lat"]),
                         crs="EPSG:4326")
    out = assign_station_uid(g)
    uid = dict(zip(out["source_station_id"], out["station_uid"]))
    assert uid["BCQ"] == uid["CALWR_WQX-B6415900"] == uid["USGS-11259000"]
    for a, b in (("B00470", "B07400"), ("TIL", "MID"), ("750006", "750007"),
                 ("750012", "750013")):
        assert uid[a] != uid[b], f"{a} and {b} merged"


def test_known_neighbors_stay_apart_in_the_inventory():
    """The pairs above, in the harvested inventory."""
    st = _load_stations()
    uid = dict(zip(st["source"] + ":" + st["source_station_id"], st["station_uid"]))
    apart = [("dwr_wdl:B00470", "dwr_wdl:B07400"), ("cdec:TIL", "cdec:MID"),
             ("esmr:750006", "esmr:750007"), ("esmr:750012", "esmr:750013")]
    together = ["usgs:USGS-11259000", "cdec:BCQ", "wqp:CALWR_WQX-B6415900"]
    missing = [k for k in [k for pair in apart for k in pair] + together
               if k not in uid]
    assert not missing, f"expected records absent from the inventory: {missing}"
    for a, b in apart:
        assert uid[a] != uid[b], f"{a} and {b} share {uid[a]}"
    assert len({uid[k] for k in together}) == 1


@pytest.mark.parametrize(("name", "sources"), [
    ("MAZE RD", {"usgs", "dwr_wdl"}),
    ("TUOLUMNE R A MODESTO", {"usgs", "cdec"}),
])
def test_known_multi_agency_sites_collapse(name, sources):
    """Agency coordinates for one site disagree by up to 600 m in this basin.

    Under a 150 m radius each of these lands twice in the shortlist.
    """
    st = _load_stations()
    hit = st[st["name"].astype(str).str.upper().str.contains(name, na=False)]
    assert not hit.empty
    uid = hit["station_uid"].iloc[0]
    cluster = set(st.loc[st["station_uid"] == uid, "source"])
    assert sources <= cluster, f"{name} split across {cluster}"


# --------------------------------------------------- study area and scope ---

def test_no_calaveras_station_survives_the_clip():
    """The subbasin is outside the study area, so no row of it should remain."""
    st = _load_stations()
    cat = _load_catalog()
    assert not st["huc8"].astype(str).eq("18040011").any()
    assert not cat["huc8"].astype(str).eq("18040011").any()
    assert "Calaveras River" not in set(cat["river"].dropna())


# ------------------------------------------------- channel classification ---

@pytest.mark.parametrize(("name", "station_type", "expected"), [
    # An eSMR location code decides before the name patterns run. The natural
    # pattern matches a bare "r" for River and the R in "R-001" is not one.
    ("El Portal WWTF R-001", "receiving_water", "receiving_water"),
    ("El Portal WWTF RSW-001", "receiving_water", "receiving_water"),
    ("City of Modesto WQCF RSW-004", "receiving_water", "receiving_water"),
    ("Clovis WWTF R-002U", "receiving_water", "receiving_water"),
    ("Merced WWTF R-002", "effluent", "effluent"),
    # WWTF sits in the effluent pattern beside WWTP and WRF.
    ("El Portal WWTF M-001", None, "effluent"),
    ("Bear Valley WWTF EFF-001", None, "effluent"),
    # None of the above may disturb the ordinary cases.
    ("SAN JOAQUIN R NR VERNALIS CA", None, "natural"),
    ("TURLOCK CN NR LA GRANGE CA", None, "conveyance"),
    ("HARDING DRAIN A CARPENTER RD NR PATTERSON CA", None, "drain"),
    ("SJR Upstream of the Newman Wasteway", None, "natural"),
])
def test_channel_class_handles_permit_codes(name, station_type, expected):
    from sjrwq.classify import channel_class
    assert channel_class(name, station_type) == expected


@pytest.mark.parametrize(("name", "expected"), [
    ("Delta Mendota Canal to Mendota Pool", "import"),
    ("CAL AQUEDUCT CHECK 13 (KA007089)", "import"),
    ("PACHECO PUMPING PLANT (SLR00000)", "import"),
    ("FRIANT-KERN CN A FRIANT CA", "export"),
    # Turlock Canal is TID's diversion from the Tuolumne and moves basin water
    # rather than imported Delta water.
    ("TURLOCK CN NR LA GRANGE CA", "diversion"),
    ("MID Lateral 4 @ Paradise Rd.", "diversion"),
    ("Livingston Canal Spill to Merced R", "return"),
    ("Newman Wasteway near Hills Ferry Road", "return"),
])
def test_conveyance_role(name, expected):
    from sjrwq.classify import conveyance_role
    assert conveyance_role(name, "conveyance") == expected


def test_conveyance_role_is_null_off_a_canal():
    from sjrwq.classify import conveyance_role
    assert conveyance_role("SAN JOAQUIN R NR VERNALIS CA", "natural") is None


# ----------------------------------------------------- eSMR frequency -------

def test_esmr_frequency_is_measured_not_assumed():
    """Flow and conductance at an outfall are not reported at the same rate.

    A permit setting an average daily flow limit obliges daily flow reporting,
    while the chemistry is monthly. One frequency label for the whole source
    hides the daily flow record at dozens of outfalls.
    """
    sp = _load_sp()
    e = sp[sp["source"] == "esmr"]
    if e.empty:
        pytest.skip("no eSMR rows")
    assert len(set(e["frequency"])) > 1, "eSMR frequency should vary by parameter"
    flow = e[e["parameter"] == "discharge"]["frequency"].value_counts()
    ec = e[e["parameter"] == "specific_conductance"]["frequency"].value_counts()
    assert flow.get("daily", 0) > 0
    assert ec.get("monthly", 0) > ec.get("daily", 0)
    assert e["median_interval_days"].notna().any()


def test_esmr_keeps_a_mixed_unit_series_apart():
    """Turlock reports effluent temperature in Celsius and in Fahrenheit.

    Keeping the modal unit alone applies one conversion factor to both halves.
    One row per station, parameter, and unit gives each half its own factor.
    """
    sp = _load_sp()
    e = sp[sp["source"] == "esmr"]
    if e.empty:
        pytest.skip("no eSMR rows")
    grouped = e.groupby(["source_station_id", "parameter"])
    assert (grouped.size() == grouped["unit"].nunique()).all(), \
        "one row per station, parameter, and unit"
    assert (grouped["unit"].nunique() > 1).any(), \
        "at least one series is reported in more than one unit"
    # And the split earns its keep: the two halves take different factors.
    mixed = e[e["parameter"] == "water_temperature"]
    factors = mixed.groupby("source_station_id")["unit_factor"].nunique()
    assert (factors > 1).any(), "a mixed degF/degC series needs two factors"


# ------------------------------------------------------ gap assumptions -----

def test_mainstem_gaps_ignore_canals_wells_and_drains():
    """A monitoring well 200 m from the bank does not observe the river.

    Every station within `rivers.ON_CHANNEL_M` of the centerline takes a
    mainstem kilometer, piezometers, canals, and drains among them. Counting
    those as temperature stations on the river closes gaps the river has, so
    the longest gap comes out shorter than it is.
    """
    import geopandas as gpd_

    from sjrwq.gaps import RIVER_CLASSES, mainstem_gaps
    dom = PROCESSED / "domain.gpkg"
    if not dom.exists():
        pytest.skip("run `python -m sjrwq.rivers` first")
    st = _load_stations()
    sp = _load_sp()
    ms = gpd_.read_file(dom, layer="mainstem")
    n_river, _, gaps_river = mainstem_gaps(st, sp, ms, "water_temperature")
    n_all, _, gaps_all = mainstem_gaps(st, sp, ms, "water_temperature",
                                       river_only=False)
    assert n_all > n_river, "the channel filter should remove something"
    assert max(g[2] for g in gaps_river) >= max(g[2] for g in gaps_all)
    assert RIVER_CLASSES == {"natural", "receiving_water"}


def test_coverage_window_membership():
    """Concurrent coverage is a subset relation, not a coincidence."""
    from sjrwq import coverage
    sp = _load_sp()
    t, e, q = "water_temperature", "specific_conductance", "discharge"
    tq = coverage.stations_with(sp, [t, q])
    teq = coverage.stations_with(sp, [t, e, q])
    eq = coverage.stations_with(sp, [e, q])
    assert teq <= tq, "adding a parameter cannot add stations"
    # Figure 13's point: conductance is the binding constraint.
    assert eq == teq, "every continuous EC and flow site also records temperature"
    assert coverage.reporting_since() > coverage.window()[0]


def test_off_channel_stations_are_flagged_not_dropped():
    """Cal Aqueduct Check 13 is a real 36-year record and an import term."""
    cal = _load_filtered()
    assert "off_channel" in cal.columns
    usable = cal[cal["usable"].astype(bool)]
    assert (~usable["off_channel"].astype(bool)).any(), "some river sites"
    assert usable["off_channel"].astype(bool).any(), "some import terms"
    # River stations sort above off-channel ones.
    order = usable["off_channel"].astype(bool).tolist()
    assert order == sorted(order), "off-channel stations should sort last"


def test_off_channel_classes_have_one_definition():
    """Two copies of a set drift, so `maps` imports `inventory`'s."""
    import sjrwq.maps as maps_mod
    from sjrwq.inventory import OFF_CHANNEL_CLASSES

    assert OFF_CHANNEL_CLASSES == {"conveyance", "drain", "groundwater",
                                   "effluent"}
    assert maps_mod.OFF_CHANNEL_CLASSES is OFF_CHANNEL_CLASSES

# ------------------------------------------------------------------ EDI -----

def test_frequency_bands_are_shared_not_copied():
    """One interval-to-frequency table, used by every source that needs it."""
    from sjrwq.classify import SAMPLE_COUNT_BANDS, frequency_from_interval

    assert frequency_from_interval(0.02) == "sub-daily"
    assert frequency_from_interval(1.0) == "daily"
    assert frequency_from_interval(7.0) == "weekly"
    assert frequency_from_interval(30.4) == "monthly"
    assert frequency_from_interval(91.0) == "quarterly"
    assert frequency_from_interval(365.0) == "annual"
    assert frequency_from_interval(900.0) == "irregular"
    # A missing interval is unknown rather than irregular: one sample and an
    # unreported count are different facts.
    assert frequency_from_interval(None) == "unknown"
    assert frequency_from_interval(float("nan")) == "unknown"
    # A source publishing a bare count cannot resolve below a day, so its
    # bands start at daily.
    assert frequency_from_interval(0.02, SAMPLE_COUNT_BANDS) == "daily"


@pytest.mark.parametrize(("name", "definition", "expected"), [
    # The name decides when it can.
    ("Water_Temperature", "Temperature of water in degrees Celsius",
     "water_temperature"),
    ("Specific_Conductance", "", "specific_conductance"),
    ("DO", "", "dissolved_oxygen"),
    # The definition decides when the name is bare.
    ("Temperature", "Surface water temperature", "water_temperature"),
    ("Conductivity", "Specific conductivity at depth of tow",
     "specific_conductance"),
    # Air and soil temperature are not water temperature.
    ("temperature", "Temperature", None),
    ("temperature", "Temperature at 2m", None),
    ("Air_temperature_mean_annual", "Air temperature, mean annual", None),
    # An identifier is not a measurement, however it is described. EZ stations
    # are positioned by conductance and are not a conductance column.
    ("EZStation", "EZ stations are located at the 2 and 6 mS/cm conductivity",
     None),
    ("lifeStage", "Life stage of the fish", None),
    # A flag column is not a value column.
    ("Chlorophyll_Sign", "Qualifier sign for chlorophyll", None),
])
def test_edi_attribute_matching(name, definition, expected):
    """`edi.entities` on a one-column EML table, guards and patterns both."""
    from sjrwq.sources import edi

    doc = ("<eml><dataset><dataTable><entityName>table.csv</entityName>"
           "<attributeList><attribute>"
           f"<attributeName>{escape(name)}</attributeName>"
           f"<attributeDefinition>{escape(definition)}</attributeDefinition>"
           "</attribute></attributeList></dataTable></dataset></eml>")
    found = edi.entities(ET.fromstring(doc), ParameterLookup.build())
    canon = next(iter(found[0]["parameters"])) if found else None
    assert canon == expected


def test_edi_prose_matching_ignores_bare_abbreviations():
    """`\\bdo\\b` has to stay out of the definition test.

    Half the definitions in an EML document contain the word "do". Matching a
    two-letter pattern against prose would put dissolved oxygen on every one of
    them, which is why the definition test runs only patterns holding a
    five-letter word.
    """
    from sjrwq.sources import edi

    lookup = ParameterLookup.build()
    canon, _ = edi._match("Comment", "Values that do not meet the criteria", lookup)
    assert canon is None
    short = [p.pattern for p, c in lookup.by_edi_pattern
             if not edi.PROSE_SAFE.search(p.pattern)]
    assert r"\bdo\b" in short and r"\bph\b" in short


def test_edi_rejects_modeled_and_dry_land_packages():
    """`edi.MODELED` on the wording in the packages it keeps out."""
    from sjrwq.sources import edi

    # edi.237 names it in an entity, edi.445 in its abstract. Both would
    # otherwise contribute Sierra lakes as river temperature stations.
    assert edi.MODELED.search("Modeled thermal sensitivity in '1625 lakes'")
    # A file name, so the word ends in an underscore rather than a space.
    assert edi.MODELED.search("Modeled_thermal_sensitivity_in_1625_lakes.csv")
    assert edi.MODELED.search("Motivated by predicted declines in snowfall")
    assert not edi.MODELED.search(
        "Sacramento-San Joaquin Bay-Delta Continuous (15 minute) Water Quality "
        "Monitoring: South Delta Region")


def test_edi_reads_a_point_coverage_and_skips_an_extent():
    """A station is four identical numbers; a wider box is a survey extent."""
    from sjrwq.sources import edi

    doc = """<eml><dataset><coverage>
      <geographicCoverage>
        <geographicDescription>SJD</geographicDescription>
        <boundingCoordinates>
          <westBoundingCoordinate>-121.3177</westBoundingCoordinate>
          <eastBoundingCoordinate>-121.3177</eastBoundingCoordinate>
          <northBoundingCoordinate>37.8223</northBoundingCoordinate>
          <southBoundingCoordinate>37.8223</southBoundingCoordinate>
        </boundingCoordinates>
      </geographicCoverage>
      <geographicCoverage>
        <geographicDescription>Suisun Bay to the Cache Slough Complex</geographicDescription>
        <boundingCoordinates>
          <westBoundingCoordinate>-122.1</westBoundingCoordinate>
          <eastBoundingCoordinate>-121.2</eastBoundingCoordinate>
          <northBoundingCoordinate>38.4</northBoundingCoordinate>
          <southBoundingCoordinate>37.7</southBoundingCoordinate>
        </boundingCoordinates>
      </geographicCoverage>
    </coverage></dataset></eml>"""
    bbox = domain_config()["bbox"]
    sites = edi.stations_in(ET.fromstring(doc), bbox)
    assert [s["code"] for s in sites] == ["SJD"]
    assert sites[0]["lat"] == 37.8223 and sites[0]["lon"] == -121.3177


def test_edi_extends_the_boundary_record_rather_than_adding_stations():
    """What EDI is for. It duplicates CDEC's South Delta sondes and reaches back.

    Every continuous EDI station in scope is already in the inventory from CDEC
    or USGS, so the harvest is worth running only if it lengthens a record. It
    does: edi.2180 publishes conductance at the Mossdale-area stations from
    1999, where CDEC's own record starts in 2013 or later.
    """
    from sjrwq.gaps import record_extension

    st, sp = _load_stations(), _load_sp()
    if "edi" not in set(sp["source"]):
        pytest.skip("no EDI rows")
    ext = record_extension(st, sp, "edi")
    assert not ext.empty
    # `record_extension` reads station-scope rows alone.
    continuous = ext[ext["kind"] == "continuous"]
    assert not continuous.empty, "edi.2180 states its per-station coverage"
    assert continuous["years_earlier"].max() > 10


def test_every_edi_row_has_a_period_scope():
    """A wide table cannot pair a station with a parameter."""
    sp = _load_sp()
    e = sp[sp["source"] == "edi"]
    if e.empty:
        pytest.skip("no EDI rows")
    assert set(e["period_scope"].dropna()) <= {"station", "dataset"}
    assert e["period_scope"].notna().all()
    named = e[e["period_scope"] == "station"]
    assert not named.empty
    # A station-scope row holds a real per-station sample count; a
    # dataset-scope one leaves it null rather than dividing a table's row count
    # between stations.
    assert named["n_obs"].notna().all()
    assert e[e["period_scope"] == "dataset"]["n_obs"].isna().all()


# ------------------------------------ what the figures and the catalog rank on -

def test_station_level_drops_dataset_rows_and_keeps_the_rest():
    """`coverage.station_level` is the filter behind every ranking."""
    from sjrwq.coverage import station_level

    sp = _load_sp()
    kept = station_level(sp)
    assert len(kept) <= len(sp)
    if "period_scope" not in sp.columns:
        pytest.skip("no source sets `period_scope`")
    assert not (kept["period_scope"] == "dataset").any()
    # EDI is the one source publishing a wide table without a station column,
    # so the filter keeps every row from the other seven.
    for src in set(sp["source"]) - {"edi"}:
        assert (sp["source"] == src).sum() == (kept["source"] == src).sum()

    # A frame with no `period_scope` column passes through unchanged.
    assert len(station_level(sp.drop(columns=["period_scope"]))) == len(sp)


def test_calibration_dates_come_from_station_scope_records():
    """Each start date in the calibration catalog comes from a station-scope row.

    edi.731 declares one period for its whole database and pairs no station
    with a parameter. At Vernalis that period starts decades before the
    station's own conductance record, so a start date taken from it adds
    decades the station has no record of.
    """
    from sjrwq.coverage import station_level

    cf, sp = _load_filtered(), _load_sp()
    first = (station_level(sp).groupby(["station_uid", "parameter"])["por_start"]
             .min().dt.strftime("%Y-%m-%d"))
    for col, param in (("temp_start", "water_temperature"),
                       ("ec_start", "specific_conductance")):
        got = cf.set_index("station_uid")[col].dropna().astype(str)
        want = first.xs(param, level="parameter").reindex(got.index)
        bad = got[got != want]
        assert bad.empty, f"{col} differs from the station-scope start at {list(bad.index[:5])}"

    vern = sp[(sp["station_uid"] == "usgs:USGS-11303500")
              & (sp["parameter"] == "specific_conductance")]
    dataset_start = pd.to_datetime(
        vern.loc[vern["period_scope"] == "dataset", "por_start"]).min()
    row = cf[cf["station_uid"] == "usgs:USGS-11303500"]
    if pd.isna(dataset_start) or row.empty:
        pytest.skip("no dataset-scope conductance row at Vernalis")
    assert pd.Timestamp(row["ec_start"].iloc[0]) > dataset_start


def test_every_catalog_station_reports_something():
    """The WQP station endpoint returns locations, not measurements.

    The harvest keeps the locations that report a target parameter. Carrying
    the endpoint's full response forward puts every other location into the
    catalog with no parameter attached, and the figures draw those as stations
    with no data.
    """
    cat = _load_catalog()
    assert (cat["n_parameters"].fillna(0) > 0).all()
    sp = _load_sp()
    assert set(cat["station_uid"]) == set(sp["station_uid"])


def test_short_historic_records_are_dropped():
    """A campaign that ran under two years and stopped before 2000.

    The rule judges a source's whole record of a parameter at a station. WQP
    writes one row per characteristic, reporting basis, and unit, so a row can
    be short while the record it belongs to is not.
    """
    from sjrwq.inventory import SHORT_RECORD_YEARS

    sp = _load_sp().copy()
    start = pd.Timestamp(domain_config()["calibration_period"]["start"])
    sp["por_start"] = pd.to_datetime(sp["por_start"], errors="coerce")
    sp["por_end"] = pd.to_datetime(sp["por_end"], errors="coerce")
    record = sp.groupby(["source", "source_station_id", "parameter"], dropna=False)
    first, last = record["por_start"].min(), record["por_end"].max()
    span = (last - first).dt.days / 365.25
    offending = (span < SHORT_RECORD_YEARS) & last.notna() & (last < start)
    assert not offending.any(), f"{int(offending.sum())} short pre-2000 records survived"


def test_a_record_split_across_rows_is_judged_whole():
    """Two short WQP rows that together span a decade both stay."""
    from sjrwq.inventory import _drop_short_historic

    def row(name, start, end):
        return {"source": "wqp", "source_station_id": "USGS-1", "parameter": "nitrate",
                "parameter_source_name": name, "station_uid": "wqp:USGS-1",
                "por_start": pd.Timestamp(start), "por_end": pd.Timestamp(end)}

    sp = pd.DataFrame([row("Nitrate (as N)", "1975-01-01", "1976-01-01"),
                       row("Nitrate (as NO3)", "1984-01-01", "1985-06-01"),
                       {**row("Nitrate", "1978-01-01", "1979-01-01"),
                        "source_station_id": "USGS-2", "station_uid": "wqp:USGS-2"}])
    kept = _drop_short_historic(sp)
    assert sorted(kept["parameter_source_name"]) == ["Nitrate (as N)", "Nitrate (as NO3)"]


def test_record_type_is_reported_not_required():
    """Record type is a column, which keeps the outfalls on the shortlist.

    A monthly conductance sample at a permitted outfall is a return-flow term
    in a salt balance, and a rule requiring a continuous monitor for both
    parameters removes the outfalls from the shortlist.
    """
    cf = _load_filtered()
    usable = cf[cf["usable"].astype(bool)]
    assert not usable.empty
    assert not usable["continuous"].astype(bool).all(), \
        "the shortlist should hold discrete records as well as continuous ones"
    assert usable["continuous"].astype(bool).any()
    # has_flow is a column, not a filter.
    assert not usable["has_flow"].astype(bool).all()


def test_the_funnel_is_a_chain():
    """Every bar on figure 09 is a subset of the bar above it.

    A step that draws its total from outside the chain makes the gray slice
    beside it wrong by construction, and a reader subtracting two bars gets a
    number that describes no set of stations.
    """
    from sjrwq.inventory import funnel

    st, cat, cal = _load_stations(), _load_catalog(), _load_filtered()
    steps = funnel(st, cat, cal)
    n = steps["n"].tolist()
    assert n == sorted(n, reverse=True), "each step is a subset of the one above"
    assert (steps["removed"] >= 0).all()
    # The chain tests existence and length and stops there. Currency, record
    # type, discharge, channel class, and the overlap between the two records
    # are columns on the shortlist.
    assert steps["n"].iloc[-1] == int(cal["usable"].astype(bool).sum())
    assert not steps["label"].str.contains("stopped reporting").any(), \
        "currency is a column, not a funnel step"


def test_either_parameter_reaches_the_shortlist():
    """One state variable is enough, and both is a column.

    Requiring temperature and conductance at one station drops about half the
    shortlist, the stations measuring one of the two. A thermograph calibrates
    the temperature field with no conductance beside it, so `has_pair` sits on
    the row rather than deciding membership.
    """
    cf = _load_filtered()
    short = cf[cf["usable"].astype(bool)]
    assert not short.empty
    assert short["has_pair"].astype(bool).any(), "some stations measure both"
    assert not short["has_pair"].astype(bool).all(), \
        "the shortlist should hold single-parameter stations too"
    solo = short[~short["has_pair"].astype(bool)]
    assert (solo["temp_start"].notna() | solo["ec_start"].notna()).all()


def test_the_shortlist_rule_measures_the_longer_record_in_the_period():
    """`usable` reads `record_years_in_period` against the configured minimum."""
    from sjrwq.config import domain_config

    cf = _load_filtered()
    least = float(domain_config()["calibration_period"]["min_record_years"])
    assert (cf["record_years_in_period"] >= least).equals(
        cf["usable"].astype(bool))
    # The longer of the two records, so it is at least the overlap.
    both = cf[cf["has_pair"].astype(bool)]
    assert (both["record_years_in_period"]
            >= both["overlap_years_in_period"] - 0.05).all()
    # A station missing one parameter measures the one it has, not the window.
    solo = cf[~cf["has_pair"].astype(bool)]
    assert (solo["overlap_years_in_period"] == 0).all()


def test_the_shortlist_is_numbered_once():
    """The rank on figure 08 and the rank in the CSV are the same number."""
    from sjrwq import maps

    cf = _load_filtered()
    short = cf[cf["usable"].astype(bool)]
    assert short["rank"].notna().all()
    assert sorted(short["rank"].astype(int)) == list(range(1, len(short) + 1))
    assert cf.loc[~cf["usable"].astype(bool), "rank"].isna().all()
    drawn = maps._shortlist()
    assert drawn["rank"].tolist() == sorted(drawn["rank"].tolist())
    assert drawn["station_uid"].tolist() == \
        short.sort_values("rank")["station_uid"].tolist()


def test_rank_orders_mainstem_then_off_mainstem_then_off_channel():
    """`_rank_shortlist` numbers Vernalis upstream, then the rest, then canals.

    Inside each block without a river kilometer the longer record comes first.
    """
    from sjrwq.inventory import _rank_shortlist

    cal = pd.DataFrame({
        "station_uid": ["canal", "trib", "trib_long", "upper", "lower", "short",
                        "canal_on_mainstem"],
        "usable": [True, True, True, True, True, False, True],
        "off_channel": [True, False, False, False, False, False, True],
        "mainstem_km": [None, None, None, 300.0, 5.0, 1.0, 2.0],
        "record_years_in_period": [20.0, 10.0, 15.0, 5.0, 5.0, 1.0, 20.0],
    })
    rank = _rank_shortlist(cal).set_index("station_uid")["rank"]
    order = ["lower", "upper", "trib_long", "trib", "canal_on_mainstem", "canal"]
    assert rank[order].tolist() == list(range(1, len(order) + 1))
    assert pd.isna(rank["short"])


def test_the_shortlist_runs_downstream_to_upstream():
    """The shortlist order CLAUDE.md names, on catalog_filtered.csv.

    Mainstem stations first, from Vernalis upstream, then the stations off the
    mainstem, then the off-channel classes.
    """
    cf = _load_filtered()
    short = cf[cf["usable"].astype(bool)].sort_values("rank")
    off_channel = short["off_channel"].astype(bool)
    off_mainstem = short["mainstem_km"].isna()
    block = off_channel.astype(int) * 2 + (~off_channel & off_mainstem).astype(int)
    assert block.is_monotonic_increasing, "blocks out of order"
    river_km = short.loc[~off_channel & ~off_mainstem, "mainstem_km"]
    assert not river_km.empty
    assert river_km.is_monotonic_increasing, "mainstem stations out of river order"


def test_the_window_tradeoff_shortens_toward_more_stations():
    """Figure 15's premise: a longer window keeps fewer stations.

    The count is monotone in the start year under a spanning rule, because a
    record spanning a longer window spans every window inside it. A count that
    rises with window length means figure 15 draws something other than that
    tradeoff.
    """
    from sjrwq import coverage

    sp = _load_sp()
    d = coverage.window_tradeoff(sp, years=range(1995, 2024, 4))
    assert not d.empty
    for _, g in d.groupby("rule"):
        g = g.sort_values("start_year")
        assert g["n_stations"].is_monotonic_increasing
        assert g["window_years"].is_monotonic_decreasing
        assert (g["station_years"].round(0)
                == (g["n_stations"] * g["window_years"]).round(0)).all()


def test_the_funnel_table_starts_at_the_harvest():
    """Figure 09 and the log come from one computation."""
    path = PROCESSED / "funnel.csv"
    if not path.exists():
        pytest.skip("no funnel.csv")
    steps = pd.read_csv(path)
    assert {"label", "n", "removed"} <= set(steps.columns)
    # The first bar is what the harvests returned, not what survived the clip.
    st = _load_stations()
    assert steps["n"].iloc[0] > len(st)


def test_wqp_endpoint_counts_add_up():
    """Each location Station/search returns is filtered out, dropped, or kept.

    `harvest.run_wqp` writes the counts to data/interim, and the gap report
    quotes all four.
    """
    from sjrwq.gaps import wqp_endpoint_counts

    wq = wqp_endpoint_counts()
    if not wq:
        pytest.skip("run `python -m sjrwq.harvest wqp` first")
    assert (wq["locations_from_station_endpoint"] - wq["locations_filtered_out"]
            - wq["locations_dropped"]) == wq["locations_with_a_target_parameter"]


def test_wqp_splits_a_location_by_basis_and_by_unit(monkeypatch):
    """One row per location, parameter, stated basis, and stated unit.

    The portal states the reporting basis in Result_MethodSpeciation and a
    unit on each result. A location reporting ammonia as N and as NH3, or
    temperature in deg C and in deg F, gets a row for each, so each row takes
    one conversion factor. A result with no unit takes the commonest stated
    unit at its location.
    """
    from sjrwq import units
    from sjrwq.sources import wqp

    def _location(ident, lat=37.5, org="USGS-CA"):
        return {"Location_Identifier": ident, "Location_Name": f"SITE {ident}",
                "Location_Latitude": lat, "Location_Longitude": -120.9,
                "Org_FormalName": org, "Org_Identifier": org,
                "ProviderName": "NWIS", "Location_Type": "River/Stream",
                "Location_CountyName": "Stanislaus",
                "Location_HUCTwelveDigitCode": "180400020101"}

    def _result(characteristic, day, unit, speciation=None):
        return {"Location_Identifier": "USGS-A",
                "Result_Characteristic": characteristic,
                "Activity_StartDate": day, "Result_MeasureUnit": unit,
                "Result_MethodSpeciation": speciation, "Activity_Media": "Water"}

    stations = pd.DataFrame([
        _location("USGS-A"),
        _location("USGS-B", lat=None),              # no coordinate
        _location("USGS-123123123123123"),          # synthetic test site
        _location("DEMO-C", org="DEMOTEST_WQX"),    # demonstration account
        _location("USGS-D"),                        # no target characteristic
    ])
    results = pd.DataFrame(
        [_result("Ammonia", f"2010-0{m}-01", "mg/L", "as N") for m in (1, 2)]
        + [_result("Ammonia", f"2011-0{m}-01", "mg/L", "as NH3") for m in (1, 2, 3)]
        + [_result("Temperature, water", f"2012-0{m}-01", "deg C") for m in (1, 2, 3)]
        + [_result("Temperature, water", f"2013-0{m}-01", "deg F") for m in (1, 2)]
        + [_result("Temperature, water", "2014-01-01", None)])
    monkeypatch.setattr(wqp, "COUNTS", {})
    monkeypatch.setattr(wqp, "stations", lambda huc8, bbox=None: stations)
    monkeypatch.setattr(wqp, "results",
                        lambda huc8, characteristics, bbox=None: results)

    gdf, sp = wqp.harvest(["18040002"])
    rows = {(r.parameter_source_name, r.unit): r.n_obs for r in sp.itertuples()}
    assert rows == {("Ammonia (as N)", "mg/L"): 2,
                    ("Ammonia (as NH3)", "mg/L"): 3,
                    ("Temperature, water", "deg C"): 4,
                    ("Temperature, water", "deg F"): 2}
    factor = (units.annotate(sp, ParameterLookup.build().units)
              .set_index(["parameter_source_name", "unit"])["unit_factor"])
    assert factor[("Ammonia (as N)", "mg/L")] == pytest.approx(1.0)
    assert factor[("Ammonia (as NH3)", "mg/L")] == pytest.approx(0.8224, rel=1e-3)
    assert factor[("Temperature, water", "deg F")] == pytest.approx(5 / 9)

    assert set(gdf["source_station_id"]) == {"USGS-A"}
    assert wqp.COUNTS["locations_from_station_endpoint"] == 5
    assert wqp.COUNTS["locations_filtered_out"] == 3
    assert wqp.COUNTS["locations_dropped"] == 1
    assert wqp.COUNTS["locations_with_a_target_parameter"] == 1


def test_fetch_refetches_when_the_request_changed(tmp_path, monkeypatch):
    """A cached file serves a request when its `.meta.json` records that request.

    The cache keys on the label, so a changed bbox under the same label has to
    miss. Parameter order and a comma encoded as %2C make no difference.
    """
    from sjrwq import config

    calls = []

    class _Response:
        status_code = 200
        text = "new"
        content = b"new"

        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            return None

    class _Session:
        def get(self, url, params=None, timeout=None, headers=None):
            calls.append(params)
            return _Response(url)

    monkeypatch.setattr(config, "RAW", tmp_path)
    monkeypatch.setattr(config, "session", _Session)
    url = "https://example.test/Station/search"
    params = {"huc": "18040003", "bBox": "-121.40,37.60,-121.17,37.86"}
    cached = tmp_path / "wqp" / "station.csv"
    cached.parent.mkdir()
    sidecar = cached.with_suffix(".csv.meta.json")

    def _prime(recorded):
        cached.write_text("old")
        if recorded is None:
            sidecar.unlink(missing_ok=True)
        else:
            sidecar.write_text(json.dumps({"request_url": recorded}))

    def _fetch():
        return config.fetch(url, "wqp", params=params, label="station.csv",
                            retries=1)

    _prime(f"{url}?bBox=-121.40%2C37.60%2C-121.17%2C37.86&huc=18040003")
    assert _fetch() == "old"
    # A file with no sidecar has no URL to compare, so it serves the request.
    _prime(None)
    assert _fetch() == "old"
    assert not calls

    _prime(f"{url}?huc=18040003&bBox=-121.39,37.60,-121.19,37.87")
    assert _fetch() == "new"
    assert len(calls) == 1
    assert cached.read_text() == "new"
    assert "bBox=-121.40,37.60" in json.loads(sidecar.read_text())["request_url"]


def test_decade_table_counts_station_scope_records():
    """A dataset-scope row puts no station in a decade.

    edi.731 declares one period, starting in the 1950s, for every station in
    its database. Counted, that period fills the 1950s conductance column,
    where this basin has 0 station-scope conductance records.
    """
    from sjrwq.coverage import station_level
    from sjrwq.gaps import coverage_by_decade

    sp = _load_sp()
    for c in ("por_start", "por_end"):
        sp[c] = pd.to_datetime(sp[c], errors="coerce")
    table = coverage_by_decade(sp)
    assert table.equals(coverage_by_decade(station_level(sp)))
    assert table.loc["1950s", "specific_conductance"] == 0


def test_all_sources_are_named_on_the_parameter_maps():
    """A color with no legend entry is a color a reader cannot decode."""
    from sjrwq.maps import SOURCE_COLOR, SOURCE_LABEL

    sp = _load_sp()
    st = _load_stations()
    prim = st[st["is_primary"]]
    for param in ("water_temperature", "specific_conductance", "discharge"):
        ids = set(sp.loc[sp["parameter"] == param, "station_uid"])
        drawn = set(prim.loc[prim["station_uid"].isin(ids), "source"])
        missing = drawn - set(SOURCE_COLOR)
        assert not missing, f"{param} draws {missing} with no color"
    assert set(SOURCE_COLOR) == set(SOURCE_LABEL)
    assert len(set(SOURCE_COLOR.values())) == len(SOURCE_COLOR), "duplicate color"


def test_coverage_label_names_the_years_it_covers():
    """The window ends on the first instant outside it."""
    from sjrwq import coverage

    start, end = coverage.window()
    label = coverage.label()
    assert label == f"{start:%Y}-{end.year - 1}"


def test_a_flood_relief_cut_is_a_channel():
    """Paradise Cut and Doughty Cut move San Joaquin water."""
    from sjrwq.classify import channel_class

    assert channel_class("PARADISE CUT UPSTREAM") == "natural"
    assert channel_class("OLD RIVER ABOVE DOUGHTY CUT") == "natural"


def test_subbasin_labels_stay_inside_their_polygon():
    """`_clear_point` places the label away from the stations and off the edge."""
    from shapely.geometry import Polygon

    from sjrwq.maps import LABEL_INSET_DEG, _clear_point

    poly = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    # One dense cluster of stations in the lower-left quadrant.
    lons = [0.4, 0.5, 0.6, 0.45]
    lats = [0.4, 0.5, 0.6, 0.55]
    pt = _clear_point(poly, lons, lats)
    assert poly.buffer(-LABEL_INSET_DEG + 1e-9).contains(pt)
    assert pt.x > 1.0 and pt.y > 1.0, "the label went to the crowded corner"


def test_coverage_sets_match_the_rules_the_figures_draw():
    """One definition of each rule, so the CSV and figures 12 to 14 agree."""
    import pandas as pd

    from sjrwq import coverage

    path = PROCESSED / "coverage_sets.csv"
    sv_path = PROCESSED / "station_parameter.parquet"
    if not path.exists() or not sv_path.exists():
        pytest.skip("run `python -m sjrwq.inventory` first")
    sp = pd.read_parquet(sv_path)
    for c in ("por_start", "por_end"):
        sp[c] = pd.to_datetime(sp[c], errors="coerce")
    table = pd.read_csv(path)
    for key in coverage.COVERAGE_SETS:
        ids = coverage.members(sp, key)
        assert set(table.loc[table[key], "station_uid"]) == ids, key
    assert (table["window"] == coverage.label()).all()


def test_the_timeline_anchors_are_stations_that_exist():
    """A renamed or dropped gage would leave the margin short of a name."""
    import pandas as pd

    from sjrwq.places import TIMELINE_ANCHORS

    path = PROCESSED / "station_parameter.parquet"
    if not path.exists():
        pytest.skip("run `python -m sjrwq.inventory` first")
    sp = pd.read_parquet(path)
    core = {"water_temperature", "specific_conductance", "discharge"}
    have = (sp[sp["parameter"].isin(core)]
            .groupby("station_uid")["parameter"].agg(set))
    missing = [u for u in TIMELINE_ANCHORS if have.get(u, set()) != core]
    assert not missing, f"anchors without all three parameters: {missing}"


def test_every_figure_has_a_caption_in_docs():
    """A figure with no `_register` call renders and leaves docs/figures.md
    short, which a reader notices before the maintainer does."""
    from sjrwq.config import DOCS, FIGURES

    pngs = sorted(p.name for p in FIGURES.glob("*.png"))
    doc = DOCS / "figures.md"
    if not pngs or not doc.exists():
        pytest.skip("run `python -m sjrwq.maps` first")
    text = doc.read_text()
    missing = [n for n in pngs if f"../figures/{n}" not in text]
    assert not missing, f"no caption in docs/figures.md for {missing}"
    assert text.count("## Figure ") == len(pngs)


def test_captions_live_in_docs_not_on_the_frame():
    """Captions belong in docs/figures.md, and `maps.write_captions` writes them."""
    from sjrwq import maps

    src = pathlib.Path(maps.__file__).read_text()
    assert "_caption(" not in src
    assert "def write_captions" in src


def _map_figure_names() -> list[str]:
    """Each file name `maps.py` saves through `_save_map`, read from its source.

    A literal passed to `_save_map` counts directly. A helper that passes one
    of its own parameters counts once per call site, with the literal the
    caller gives, which is how the three parameter maps and the three
    coverage maps reach `_save_map`.
    """
    from sjrwq import maps

    tree = ast.parse(pathlib.Path(maps.__file__).read_text())
    names, helpers = [], {}
    for func in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for call in ast.walk(func):
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) == "_save_map"):
                continue
            arg = call.args[1]
            if isinstance(arg, ast.Constant):
                names.append(arg.value)
            else:
                params = [a.arg for a in func.args.args]
                helpers[func.name] = (params.index(arg.id), arg.id)
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) in helpers:
            pos, kw = helpers[call.func.id]
            given = (call.args[pos] if len(call.args) > pos
                     else next(k.value for k in call.keywords if k.arg == kw))
            names.append(given.value)
    return sorted(names)


def test_every_map_figure_is_the_same_size(tmp_path, monkeypatch):
    """The map figures page through as a set, at the size `_save_map` declares.

    The expected size comes from a blank frame that `_map_figure` builds and
    `_save_map` writes, so a map saved with a tight crop, or a frame built
    some other way, fails here. `_map_figure` rounds the height down to whole
    pixels at `figure.dpi`, so Agg and the macOS backend save the same size and
    the match is exact.
    """
    from PIL import Image

    from sjrwq import maps
    from sjrwq.config import FIGURES

    names = _map_figure_names()
    assert "10_current_stations.png" in names
    paths = [FIGURES / n for n in names]
    dom = PROCESSED / "domain.gpkg"
    if not dom.exists() or not all(p.exists() for p in paths):
        pytest.skip("run `python -m sjrwq.maps` first")

    monkeypatch.setattr(maps, "FIGURES", tmp_path)
    fig, _ = maps._map_figure({"aoi": gpd.read_file(dom, layer="aoi")})
    maps._save_map(fig, "blank.png")
    expected = Image.open(tmp_path / "blank.png").size
    sizes = {p.name: Image.open(p).size for p in paths}
    assert len(set(sizes.values())) == 1, f"map figures differ in size: {sizes}"
    wrong = {n: s for n, s in sizes.items() if s != expected}
    assert not wrong, f"the declared frame is {expected}, the maps are {wrong}"


def test_a_break_in_the_continuous_record_fails_the_spanning_rule():
    """One merged span has to cover the window, as figures 12 to 15 state.

    Maze Rd logged temperature 1985 to 1989 and again from 2006. A window
    starting in 1990 falls in the break, one starting in 2007 does not, and a
    gap shorter than `coverage.JOIN_DAYS` joins the two rows into one span.
    """
    from sjrwq import coverage

    def row(param, start, end):
        return {"station_uid": "s", "parameter": param, "record_type": "continuous",
                "period_scope": "station", "por_start": pd.Timestamp(start),
                "por_end": pd.Timestamp(end)}

    params = ["water_temperature", "discharge"]
    flow = row("discharge", "1960-01-01", "2026-01-01")
    broken = pd.DataFrame([row("water_temperature", "1985-08-26", "1989-09-30"),
                           row("water_temperature", "2006-06-27", "2026-01-01"), flow])
    end = pd.Timestamp("2026-01-01")
    assert coverage.stations_with(broken, params, pd.Timestamp("1990-01-01"), end) == set()
    assert coverage.stations_with(broken, params, pd.Timestamp("2007-01-01"), end) == {"s"}

    short_gap = pd.DataFrame([row("water_temperature", "1985-08-26", "2006-06-12"),
                              row("water_temperature", "2006-06-27", "2026-01-01"), flow])
    assert coverage.stations_with(short_gap, params, pd.Timestamp("1990-01-01"), end) == {"s"}


def _one_station_record() -> pd.DataFrame:
    """A continuous record for 2000 to 2004 and samples from 2010 to 2015."""
    return pd.DataFrame({
        "station_uid": ["a", "a"],
        "parameter": ["water_temperature"] * 2,
        "record_type": ["continuous", "discrete"],
        "por_start": pd.to_datetime(["2000-01-01", "2010-01-01"]),
        "por_end": pd.to_datetime(["2004-12-31", "2015-12-31"])})


def test_station_spans_keep_continuous_years_apart():
    """A year that only discrete samples cover is out of `cont_spans`."""
    from sjrwq.maps import _station_spans

    row = _station_spans(_one_station_record(), "water_temperature").iloc[0]
    assert [(s.year, e.year) for s, e in row["spans"]] == [(2000, 2004), (2010, 2015)]
    assert [(s.year, e.year) for s, e in row["cont_spans"]] == [(2000, 2004)]


def test_figure_7_draws_continuous_where_a_continuous_record_covers(monkeypatch):
    """Figure 7 draws the continuous style across 2000 to 2004 and no later."""
    import matplotlib.pyplot as plt

    from sjrwq import maps

    dom = PROCESSED / "domain.gpkg"
    if not dom.exists():
        pytest.skip("run `python -m sjrwq.rivers` first")
    drawn = []
    monkeypatch.setattr(maps, "CAPTIONS", [])
    monkeypatch.setattr(maps, "_save", lambda fig, name, **kw: drawn.append(fig))
    st = pd.DataFrame({"station_uid": ["a"], "is_primary": [True],
                       "mainstem_km": [120.0], "channel_class": ["natural"]})
    maps.fig_longitudinal(st, _one_station_record(),
                          {"mainstem": gpd.read_file(dom, layer="mainstem")})
    assert drawn, "fig_longitudinal saved no figure"
    temperature = drawn[0].axes[0]
    by_width = sorted(temperature.collections, key=lambda c: c.get_linewidths()[0])
    discrete, continuous = by_width[0], by_width[-1]
    cont = [(seg[0][1], seg[1][1]) for seg in continuous.get_segments()]
    disc = [(seg[0][1], seg[1][1]) for seg in discrete.get_segments()]
    plt.close(drawn[0])
    assert len(cont) == 1 and 2000 <= cont[0][0] and cont[0][1] < 2006
    assert max(top for _, top in disc) > 2015


def test_figure_8_takes_its_groups_from_coverage_colocated(monkeypatch):
    """Figure 8 and the gap report count co-located stations with one function."""
    from sjrwq import coverage, maps

    class _Called(Exception):
        pass

    def _colocated(sp):
        raise _Called

    monkeypatch.setattr(coverage, "colocated", _colocated)
    sp = pd.DataFrame({"station_uid": ["a"], "parameter": ["discharge"],
                       "record_type": ["continuous"]})
    with pytest.raises(_Called):
        maps.fig_colocation(None, sp, None)

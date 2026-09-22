# SJR-WQ

An inventory of water quality and streamflow monitoring stations in the San
Joaquin basin, built to support calibration of a water temperature and salinity
model (ATS/EcoSIM). The harvest collects station metadata and per-parameter
period of record from eight public APIs. It downloads no time series.

**Start with the deck: [`docs/SJR-WQ.pptx`](docs/SJR-WQ.pptx).** It presents
the study area, the sources, the figures, and the shortlist, with the detail in
the speaker notes. It's a hand-edited file: no script writes it, so after a new
harvest its counts need a manual check against `docs/`.

| Path | What it is |
| --- | --- |
| `docs/SJR-WQ.pptx` | the deck |
| `docs/gap_analysis.md` | the full gap report: where the coverage runs out, by parameter, reach, and control point |
| `docs/figures.md` | a caption for each of the 15 figures in `figures/` |
| `data/processed/catalog_filtered.csv` | the shortlist: stations with 3 years or more of temperature or conductance since 2000, ranked |
| `data/processed/catalog.csv` | every station in the study area, one row each |
| `data/processed/station_parameter.parquet` | station by parameter by period of record |
| `data/processed/stations.gpkg`, `domain.gpkg` | the stations and the study area as GIS layers |
| `src/sjrwq/` | the package: six modules that run in order |
| `config/` | the study area (`domain.yml`), the parameter crosswalk (`parameters.yml`), source notes (`sources.yml`) |

## Rebuilding

```sh
pip install uv                     # or: pipx install uv
uv sync                            # builds .venv with Python 3.12
uv run python -m sjrwq.domain      # study area from the Watershed Boundary Dataset
uv run python -m sjrwq.rivers      # river centerlines from the NLDI
uv run python -m sjrwq.harvest     # the eight sources (or: harvest cdec usgs)
uv run python -m sjrwq.inventory   # merge, clip, deduplicate, classify, rank
uv run python -m sjrwq.maps        # figures/ and docs/figures.md
uv run python -m sjrwq.gaps        # docs/gap_analysis.md and the summary below
uv run pytest -q
```

The harvest caches every response under `data/raw/<source>/`, so a re-run
sends no requests. A cached file whose recorded URL no longer matches the
request, after a change to `config/domain.yml` for example, is fetched again
with a warning. Delete a file, or a source directory, to refresh it by hand.
`domain` and `rivers` reach the network on a cold cache; the rest run offline.

## What the harvest found

<!-- harvest-summary:start -->
- 1,834 source records from eight APIs, collapsing to 1,350 distinct stations, 283 of which appear in more than one source.
- Each station in the catalog reports at least one target parameter, and 153 of them are groundwater wells. The Water Quality Portal's station endpoint returns every monitoring location in a HUC regardless of what it measures, 12,152 in this run. The harvest removes 489 of them for a missing coordinate, a synthetic test-site number, or an organization outside water monitoring, and drops 10,773 of the rest because they report 0 of this inventory's 36 characteristics.
- 845 stations have a dated station-scope record of water temperature or specific conductance, and `data/processed/catalog_filtered.csv` lists those 845. 13 more have a temperature or conductance record with no start date (7) or with only a package-wide period from EDI (6), and stay out of that file. 412 of the 845 hold 3 years or more of one since 2000. 207 measure both parameters and 65 measure both with a continuous monitor. 96 of the 412 also gage discharge. None of those three is a requirement: a thermograph calibrates the temperature field with no conductance beside it, and a monthly conductance sample against a gaged flow constrains a seasonal signal and a load. 280 sit on rivers, sloughs or reservoirs, 27 have a name that matches no channel class (`unknown`), and 105 sit on canals, drains, permitted outfalls, or monitoring wells: real records, and boundary terms rather than river calibration sites. `figures/10_current_stations.png` maps all 412 and `data/processed/catalog_filtered.csv` lists them.
- Below Friant Dam, the upstream boundary at 312 km, the longest run of mainstem with no continuous temperature station is 55 km, from 100 to 155 km above Vernalis. The river is 475 km end to end.
- For specific conductance the longest such run is 55 km, from 100 to 155 km. Above `SAN JOAQUIN R RELEASE A FRIANT DAM CA` at 312 km, 0 stations have recorded EC continuously along the 163 km to the headwaters, across all channel classes and all years, and 8 have discrete EC samples.
- 5 of the 6 rim dams have a station within 15 km on the same river, or in the same subbasin for the 2 on rivers with no traced centerline, measuring temperature with a continuous monitor that still reports. 0 of the 6 have a flagged depth profile in the eight harvested sources. RISE flags profiles explicitly and sets that flag on 0 of its 136 series in this basin. RISE publishes series within 5 km of 2 of the 6 dams (Millerton and New Melones), so the absence is a checked finding at 2 and unchecked at the other 4. Reclamation, the USACE Sacramento District, the Turlock and Modesto Irrigation Districts, and Merced Irrigation District hold the profiles.
- 107 of the 8,122 station-parameter rows have a unit that does not convert to the canonical one in `config/parameters.yml`: 43 are a different quantity filed under the parameter's name, 40 have no unit, 16 have a unit string outside the tables in `sjrwq/units.py`, and 8 join more than one reporting basis in one row. The three commonest reasons: source reported no unit (40); mass load per unit time, not a concentration (29); unrecognized unit 'degree' (11). `station_parameter.parquet` lists each row's reason in `unit_note`.
<!-- harvest-summary:end -->

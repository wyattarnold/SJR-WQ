# SJR-WQ

An inventory of water quality and streamflow monitoring stations in the San Joaquin basin. The scripts collect station metadata and per-parameter period of record from eight public APIs. _No time series downloading or quality assurance is performed here._

> **Summary of the inventoried results: [`docs/SJR-WQ.pptx`](docs/SJR-WQ.pptx).**

| Path | Description |
| --- | --- |
| `docs/gap_analysis.md` | gap report (coverage by parameter, reach, and control point) |
| `docs/figures.md` | captions for figures in `figures/` |
| `data/processed/catalog_filtered.csv` | shortlist stations with 3 years or more of temperature or conductance since 2000, ranked |
| `data/processed/catalog.csv` | all inventoried stations in the study area |
| `data/processed/station_parameter.parquet` | station by parameter by period of record |
| `data/processed/stations.gpkg`, `domain.gpkg` | stations and the study area as GIS layers |
| `config/` | study area (`domain.yml`), parameter crosswalk (`parameters.yml`), and source notes (`sources.yml`) |

## How to run

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

- Scripts cache responses under `data/raw/<source>/`
- A cached file whose recorded URL no longer matches the request, after a change to `config/domain.yml` for example, is fetched again with a warning.
- Delete a file, or a source directory, to refresh it by hand.
- `domain` and `rivers` reach the network on a cold cache; the rest run offline.



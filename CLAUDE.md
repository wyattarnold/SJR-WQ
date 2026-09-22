# Working in this repository

An inventory of water quality, streamflow, and channel geometry data for the San
Joaquin basin, built to support calibration of an ATS/EcoSIM temperature and
salinity model. It harvests station metadata and per-parameter period of
record from eight APIs. It downloads no time series.

## Scope

- **Metadata only.** Periods of record, sample counts, record type, sampling
  frequency, coordinates, units. Adding a bulk time-series download changes
  what this repository is, so raise it before writing one.
- **Calibration period 2000 to present.** The concurrent-coverage figures use a
  narrower window, 2011-01-01 to 2026-01-01, set in `config/domain.yml`.
- **Credentials come from the environment.** `EDI_API_KEY` and `EDI_TOKEN`
  belong in neither the repository nor `data/raw/`. The DataONE route needs no
  account, so the harvest runs without either.

## Running

`uv sync` builds `.venv` from `pyproject.toml` with a managed Python 3.12 and
installs the package in editable mode. `uv run` uses that interpreter. Where
`uv` is not on the PATH, `python -m uv` does the same.

```sh
uv run python -m sjrwq.domain      # study area, HUC8/HUC12, state, surround
uv run python -m sjrwq.rivers      # river centerlines from the NLDI
uv run python -m sjrwq.harvest     # eight sources (or: harvest cdec usgs)
uv run python -m sjrwq.inventory   # merge, clip, deduplicate, classify
uv run python -m sjrwq.maps        # the 15 figures and docs/figures.md
uv run python -m sjrwq.gaps        # docs/gap_analysis.md, README summary
uv run pytest -q
uv run ruff check src/sjrwq/ tests/
```

`data/raw/<source>/` caches every response with a `.meta.json` holding the
request URL, the status, and the retrieval timestamp, so a re-run costs almost
nothing and the acquisition date sits beside the file rather than in the path.
`config.fetch` compares the request URL with the `request_url` (or `url`)
in the sidecar before serving a cached file, and refetches on a mismatch with a
warning, so a changed bbox or parameter list cannot reuse an old response.
Delete a file, or a source directory, to force a refresh. `sjrwq.domain` and
`sjrwq.rivers` reach the network on a cold cache; `sjrwq.inventory`,
`sjrwq.maps`, and `sjrwq.gaps` stay offline.

Changing prose in `maps.py` or `gaps.py` means re-running that module and
checking the output before reporting the change as done.

## Generated files

Edit the source, not the output.

| Output | Written by |
| --- | --- |
| `docs/gap_analysis.md` | `gaps.build_report` |
| the block between `<!-- harvest-summary:start -->` and `:end` in `README.md` | `gaps.harvest_summary` |
| `figures/*.png` | `maps.py` |
| `docs/figures.md` | `maps.write_captions` |
| `data/processed/*` | `inventory.py`, `domain.py`, `rivers.py` |
| `data/interim/wqp_endpoint_counts.csv` | `harvest.run_wqp`, read by `gaps.wqp_endpoint_counts` |

A number a reader might quote belongs in generated text, computed from
`data/processed`. A hand-typed count goes stale within one harvest.

`docs/SJR-WQ.pptx` is the exception: the deck is edited by hand in PowerPoint
and no script writes it. Its counts are typed for the harvest named on its
footer. After a new harvest, re-read the slides against `docs/figures.md` and
`docs/gap_analysis.md`. Adding a figure means adding a slide by hand.

## Invariants worth knowing before making changes

- **`coverage.station_level(sp)`** keeps rows whose `period_scope` is
  `station`. EDI publishes wide tables keyed by a station column without
  pairing station to parameter, so those rows have `dataset` there and hold
  the package's period of record. Call sites in `coverage`, `inventory`,
  `maps`, and `gaps` depend on it: every table ranked or dated on record
  length goes through it, the decade table and the coverage sets included, so
  grep before changing it.
- **One word for the measured quantity: `parameter`.** Every source calls it
  that, so the column, `station_parameter.parquet`, `config/parameters.yml`,
  `ParameterLookup`, and the prose all do too. `variable` is out, and the local
  frame is `sp`. The word survives in one place: temperature and salinity are
  the model's state variables.
- **`record_type` and `frequency`, not one column.** `record_type` is
  `continuous` or `discrete` and comes from what the source states about the
  instrument. `frequency` is `sub-daily` through `irregular` and comes from a
  stated interval or from count over span. A monthly sample is discrete at a
  known frequency, which is why they are two columns. `classify` owns the
  vocabulary, the bands, and the rank.
- **`channel_class`, not `station_type`.** `station_type` is what the source
  reports, and CDEC reports the sensor, so monitoring wells arrive typed
  `continuous_sonde` or `discrete_grab`. `channel_class` works from the name.
- **`inventory.OFF_CHANNEL_CLASSES`** is `{conveyance, drain, groundwater,
  effluent}`, one definition, imported by `maps`. It is not the complement of
  `gaps.RIVER_CLASSES`: `reservoir` and `unknown` sit in neither, because a
  reservoir station is on the modeled water body and not on a river reach.
- **One tolerance for the channel.** `rivers.ON_CHANNEL_M` decides both whether
  a station is named for a river and whether it takes a mainstem kilometer. Two
  values put wells and canals on the mainstem axis with a null river name.
- **`gaps.RIVER_CLASSES`** is `{natural, receiving_water}`, the classes that
  count as observing the river. Canals, drains, and wells stay in the inventory
  and close no river gap.
- **`coverage.COVERAGE_SETS`** defines the four concurrent-coverage rules
  once. Figures 12 to 14 draw them through `coverage.members`, and
  `inventory.main` writes `coverage_sets.csv` from the same dict, so a changed
  rule reaches the maps and the table in one run.
- **The funnel is a chain, and it tests existence and length.**
  `inventory.funnel` writes `data/processed/funnel.csv` and figure 9 draws it,
  so the log and the figure come from one computation. Every row is a subset of
  the row above, which is what lets a reader subtract two bars; a step drawing
  its total from outside the chain breaks the gray slice beside it. The two
  questions are whether a station measures a state variable of the model and
  whether it measures one for `min_record_years` inside the calibration period.
- **Either state variable reaches the shortlist.** `usable` reads
  `record_years_in_period`, which is the longer of the temperature and
  conductance records clipped to the period, not their overlap. About half the
  stations on the shortlist hold both and half hold one, and a thermograph
  calibrates the temperature field with no conductance beside it. `has_pair`
  and `overlap_years_in_period` are columns. `_in_period` masks a missing
  parameter, because `max` and `min` skip NaT and an unguarded clip hands a
  temperature-only station the whole window for its conductance.
- **Record type, discharge, channel class, currency, and the overlap between
  the two records are columns, not filters.** Each is a matter of degree rather
  than a yes or a no, so each stays on `catalog_filtered.csv` and out of the
  funnel. A monthly conductance sample against a gaged flow gives a monthly
  load, a canal is a boundary term a salt balance needs, a two-year overlap is
  shorter than a twenty-year one without being nothing, and a record that ended
  in 2014 still covers fourteen years of the period.
- **The shortlist is numbered once.** `inventory._rank_shortlist` writes `rank`
  into `catalog_filtered.csv` in mainstem order, downstream to upstream, then
  off-mainstem, then off-channel. Figure 8 numbers its markers from that column
  through `maps._shortlist`, so the map and the file cannot disagree about
  which station is number 7. Rows below the rule take no number.
- **One definition of a record's span.** `coverage.merged_spans` joins a
  station's rows for a parameter into spans, bridging a break of up to
  `coverage.JOIN_DAYS`. Figures 6 and 7 draw those spans, and the spanning rule
  behind figures 12 to 15 and `coverage_sets.csv` requires one continuous span
  to cover the window, so a record that stopped in 1989 and resumed in 2006
  covers no window that starts in the break. A changed tolerance moves the
  timeline bars and the coverage sets in one run.
- **`coverage.window_tradeoff` is the argument for the coverage window.**
  Figure 15 draws stations against window length and their product, which peaks
  where the two effects balance. Changing `coverage_window` in
  `config/domain.yml` moves figures 12 to 15 in one run, so the window is a
  measured choice rather than a typed one.
- **WQP has two endpoints that return different things.** `Station/search`
  returns every monitoring location in a HUC; `Result/search` returns the
  target characteristics. `sources/wqp.py` keeps the intersection, and
  `harvest.run_wqp` writes both counts to `data/interim` because neither
  survives into `data/processed`.
- **The maps share one frame.** `_map_figure` and `_save_map` compute height
  from the AOI aspect and save at a declared size, so the drawn content cannot
  resize the figure. All ten map figures share one size.
- **Captions live in `docs/figures.md`, not on the figures.** A figure has a
  legend and axis labels and no title: the deck slide that shows it carries
  the title. Each figure calls `maps._register` with its title and caption
  while it renders, and `maps.write_captions` writes the file at the end of
  the run, so a caption holds the counts of the frame beside it. Adding a
  figure means adding a `_register` call.

## Writing

This applies to the README, `docs/`, figure captions, code comments, and
docstrings alike.

- Active voice with a named subject. "The harvest drops them", not "they are
  dropped".
- No personification. A file, a table, or a station does not say, carry, read,
  answer, know, or want. Use has, includes, lists, records, measures, moves,
  returns. An agency or a program can publish, drop, log, or compute.
- No absolutes. Replace never, nobody, nothing, always, certainly, any, and at
  all with a count where one exists: "sets that flag on 0 series at the six rim
  dams".
- Name the referent. Two candidates in a sentence means it, them, and this go
  out.
- Write to what is there. No prior versions, no what-used-to-happen, no
  narration of a fix. Where the history implies a rule, state the rule.
- No em or en dashes. Use a period, a comma, a colon, or parentheses.
- Short sentences, plain words, contractions. American English.

## Conventions

- The figures are Arial at 9 pt or smaller, and time axes start at 1950 with an
  arrow for records reaching back past it.
- Terminology follows USGS: continuous record, discrete samples, sampling
  frequency, continuous monitor. `cadence`, `recorder`, and `bottle` are out.
- Tests live in one file, `tests/test_inventory.py`. The unit tests run in every
  case; the integration checks skip when `data/processed` is absent.
- Commit only when asked.

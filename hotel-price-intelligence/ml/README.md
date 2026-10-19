# ML workspace

Code is split by task; datasets and data-quality policy are shared.

```text
ml/
  regression/       training/, configs/, scripts/, notebooks/, tests/
    models/         local fitted bundles + complete immutable run results
    results/        local standalone regression analyses (not files split out of a run)
    colab/          local delivery ZIPs, manifests and historical Colab notebooks
  classification/  reserved for the up/down/stable extension; not trained yet
    models/, results/, colab/   local artifacts, ignored by Git
  shared/           dataset_builder/, policies/, analysis/, scripts/, tests/
```

- [Regression commands](regression/README.md).
- [Dataset-builder and shared pipeline](shared/README.md).
- [Migration and compatibility](LAYOUT_MIGRATION_20261019.md).

From `hotel-price-intelligence/ml`, run all tests with `python -B -m pytest -q`.
MySQL integration tests remain opt-in (`ML_SMOKE=1`); do not enable them while the daily crawler is active.
The root pytest config bootstraps both task/shared import roots. CLI scripts bootstrap themselves and
work from other working directories; Python module names `training` and `dataset_builder` are retained
for compatibility, not duplicated in the old locations. The single `dataset_builder.py` root file
is an import bridge for immutable audit tools, not a second implementation of the builder.

Dataset Parquet remains at workspace `outputs/datasets/`; warehouse/operational databases and crawl
calendars did not move. Features and labels are shared inputs, not owned by either estimator family.
Source code/config/tests/docs are tracked; model/results/Colab artifacts under either task are ignored.
Gitignore is not a backup. There is no trained classification model yet.

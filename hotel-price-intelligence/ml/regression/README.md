# Regression

Contains the existing price-forecasting pipeline (persistence, Ridge, RF, XGBoost, train-v3).
No retraining, new scientific claim or model promotion is implied by the folder migration.

## Local commands

Run from `hotel-price-intelligence/ml` (dataset paths below are relative to that directory):

```powershell
python regression/scripts/train_models.py --dataset-dir ../../outputs/datasets/<dataset_version> --horizons 1
python regression/scripts/train_models_v3.py --dataset-dir ../../outputs/datasets/<dataset_version> --device auto
python regression/scripts/package_for_colab.py --dataset-dir ../../outputs/datasets/<dataset_version> --out regression/colab/<dataset_version>
python -B -m pytest regression/tests -q
```

Default local model output: `ml/regression/models/<dataset_version>/<run_id>/`.
Default package output: `ml/regression/colab/`; use an explicit per-dataset `--out` directory so
`COLAB_MANIFEST.json` from separate packages never overwrites a different package's manifest.
The new `results/` directory is for standalone analyses only. Reports, predictions, environment and
checksums that belong to a run stay beside the bundle under `models/`; moving them individually
would break the run's manifest.

## Colab

Use source notebooks `regression/notebooks/train_colab.ipynb` or `train_colab_v3.ipynb` for new runs.
Historical delivered packages/notebooks now live in `regression/colab/` and retain their original bytes.
Upload exactly the matching code ZIP, dataset ZIP and `COLAB_MANIFEST.json` to a dedicated Drive folder.
The local folder reorganization does **not** rename existing Drive folders or change `DRIVE_DIR`.

New packages deliberately keep the portable archive layout `ml/training`, `ml/dataset_builder`,
`ml/scripts`, `ml/configs`, `ml/CODE_MANIFEST.json`. Thus `/content/ml/...` inside Colab is correct;
it is not the on-disk source repository layout. Only the required shared pure files are packaged,
never MySQL code, policies from outside the dataset snapshot, models, results or delivery artifacts.
An extracted flat package defaults model output outside its code directory to keep strict code-set
verification intact on later invocations; notebooks explicitly save results to Drive.

Existing provenance, exact-runtime replay requirements, routing and dev/exploratory restrictions
are unchanged. This migration does not make old models official or eliminate existing data limitations.

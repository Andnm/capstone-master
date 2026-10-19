# ML layout migration — 2026-10-19

## Mapping

| Old location | New location |
| --- | --- |
| `ml/training`, `ml/configs`, `ml/notebooks`, `ml/requirements-train*.txt` | same names under `ml/regression/` |
| `ml/scripts/train_models*.py`, `package_for_colab.py` | `ml/regression/scripts/` |
| workspace `outputs/models/` | `ml/regression/models/` |
| workspace `outputs/colab/` | `ml/regression/colab/` |
| `ml/dataset_builder`, `ml/policies`, `ml/analysis` | same names under `ml/shared/` |
| dataset-build/clone/N1 CLI scripts | `ml/shared/scripts/` |
| `ml/tests/` | regression tests under `regression/tests`, shared/quality tests under `shared/tests`; fixtures bootstrapped by root conftest |

99 historical model/Colab files (91,270,025 bytes) were relocated, not regenerated. Exact per-file
old/new paths and SHA-256 are in `LAYOUT_MIGRATION_MANIFEST.json`. This is a location map, not a
replacement for the original run/code/dataset manifests and not a backup of artifact payloads.
Historical paths recorded **inside** artifacts remain as provenance of their original execution.
ZIPs, model bundles, predictions, reports and delivered notebooks retain their original bytes.
Use the mapping to locate them; do not rewrite their metadata/checksums to the new paths.

The old source Python package names are retained: CLI/test bootstrap adds `regression` and `shared`
to the import path. Fresh portable Colab archives intentionally retain their old flat internal layout;
local source folder names and `/content/ml` in a notebook need not be the same.

`ml/dataset_builder.py` is a tiny import bridge to the real shared package. It lets frozen audit
tools continue adding only `ml/` to sys.path without changing their pinned source bytes or updating
historical pins. In particular `audit_tools/warehouse_audit_v3.py` remains byte-identical; its ledger/
prevalence controller pins must not be reissued merely for a directory move.
Moved source paths are registered with Git intent-to-add (`git add -N`) so DEV execution can still
verify its source file membership. No content is staged and no commit/push is made; official builds
remain blocked by dirty-source guards until the migration is reviewed and committed.

## Dataset identity and safety

Shared builder code/CLI paths and some bootstrap code changed. Its current code manifest therefore
has a **new identity**. Existing pinned dataset versions must not be resumed/rebuilt using this new
code: the existing fail-closed identity checks must refuse them. For a new build, initialize a new
dataset_version, with the new manifest. To reproduce a historical build, use its original Git/code
version in a separate workspace and the recorded inputs. Exported historical datasets stay immutable;
training/replay may continue to read them through their existing integrity checks.

No warehouse/operational database, `outputs/datasets`, crawl workbook, deployed source or old discuss
thread is moved or rewritten. Historical diagnostics that explicitly hard-code old paths may require
their original checkout or explicit relocated arguments; no retrospective hash/provenance rewrite
has been performed to make such scripts appear current.

The local read-only `backend/scripts/n1_rollout_monitor.py` default policy path now points to
`ml/shared/policies/n1/n1_policy_v1.json`. Its SQL templates, thresholds and policy bytes are unchanged;
historical rollout receipts remain frozen, not re-signed to claim the new source hash was deployed.

Generated `models/`, `results/`, `colab/` under either task are ignored; source/config/tests remain
tracked. No retraining of historical real-data models, classifier creation, commit, push or model
promotion is part of this change (synthetic training/replay occurs in unit tests).

## Verification (2026-10-19)

| Check | Result |
| --- | --- |
| Historical artifacts | 99/99 SHA-256 match, 91,270,025 bytes; 7/7 transactional model runs verify |
| Regression tests | 479 passed |
| Shared tests, including 11 new migration cases | 1,191 passed, 52 skipped |
| Backend (backend/venv) | 567 passed, 23 skipped |
| EDA (eda/.venv) | 359 passed, 75 skipped |
| Diff whitespace / frozen audit-controller hash | PASS / unchanged |

Total ML collection is 1,722: 1,670 PASS, 52 SKIP, zero failures. Shared skips are 43 opt-in MySQL
tests and 9 real-artifact parity tests requiring the pandas 3.0.5 builder runtime. No MySQL integration
pass is claimed. Regression delivery tests were additionally rerun after updating generated-path documentation.

Raw final ML logs, collected IDs and the verification receipt are local/ignored under
`regression/results/layout-migration-20261019/`. Deletion of the temporary test tree was blocked by
the execution tool; it was **preserved**, not deleted, as `test-scratch/` inside that directory.
It contains only test fixtures/caches/debug logs (including intermediate failed attempts), not a
new production model or scientific result. It may be removed later independently of models/Colab artifacts.

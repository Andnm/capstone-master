"""Harness (chay bang SUBPROCESS `python -I`, khong phai test): chay CHINH `ml/scripts/init_dataset_build.py::main()` voi argv THAT cua spec, nhung moi cham toi Settings/.env/DB deu bi chan:
`dataset_builder.env` (nap `.env`) bi thay bang stub, `dotenv`/`mysql`/`app.core.config` bi chan, `app.warehouse.connection` la stub nem loi, `connect`/`fetch_all`/`resolve_pinned_thresholds`/`init_dataset_build`
bi thay bang ham gia (SQL la le => AssertionError). Ket qua in MOT dong `HARNESS_RESULT=<json>`: rc, stdout, stderr, cac lan goi init (dataset_version + config) va config_sha256 do CHINH `cfg.config_sha256` tinh.

    python -I harness_init_semantics.py <ml_dir> <json: {"argv": [...], "min_runs": 3, "min_coverage": 0.8}>
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

ML = Path(sys.argv[1]).resolve()
PAYLOAD = json.loads(sys.argv[2])
BACKEND = ML.parent / "backend"
sys.path[:0] = [str(ML), str(BACKEND)]
for blocked in ("dotenv", "mysql", "mysql.connector", "app.core.config"):
    sys.modules[blocked] = None                                                                          # import => ImportError: bat ky duong nao cham Settings/DB deu hong ro rang

env = types.ModuleType("dataset_builder.env")
env.ML_DIR, env.PROJECT_DIR, env.REPO_ROOT, env.BACKEND_DIR = ML, ML.parent, ML.parent.parent, BACKEND
env.DATASET_OUTPUT_ROOT = env.REPO_ROOT / "outputs" / "datasets"
env.HOLIDAYS_CSV = env.PROJECT_DIR / "data" / "vn_holidays.csv"
env.ensure_backend_importable = lambda: None
sys.modules["dataset_builder.env"] = env

connection = types.ModuleType("app.warehouse.connection")
connection.operational_database_name = lambda: "stub_operational"


def _blocked(*args, **kwargs):
    raise RuntimeError("DB access blocked by harness")


connection.warehouse_connection = _blocked
sys.modules["app.warehouse.connection"] = connection

import dataset_builder.config as cfg  # noqa: E402
import dataset_builder.manifest as manifest  # noqa: E402

spec = importlib.util.spec_from_file_location("init_dataset_build_under_harness", ML / "scripts" / "init_dataset_build.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

calls: list[dict] = []


class FakeConn:
    def commit(self):
        return None


@contextlib.contextmanager
def fake_connect(database):
    calls.append({"event": "connect", "database": database})
    yield FakeConn()


def fake_fetch_all(conn, sql, params=None):
    if "FROM etl_import_batches WHERE status='pass'" in sql:
        return [{"batch_id": PAYLOAD.get("batch_id", "b20261004_3src")}]
    raise AssertionError(f"unexpected SQL in harness: {sql!r}")


def fake_thresholds(conn, batch_id):
    return {"min_runs": PAYLOAD["min_runs"], "min_coverage": PAYLOAD["min_coverage"]}


def fake_init(conn, *, dataset_version, config):
    calls.append({"event": "init", "dataset_version": dataset_version, "config": config, "config_sha256": cfg.config_sha256(config)})
    return {"dataset_version": dataset_version, "status": "running", "last_completed_step": "initialized"}


script.connect, script.fetch_all = fake_connect, fake_fetch_all
manifest.resolve_pinned_thresholds, manifest.init_dataset_build = fake_thresholds, fake_init

sys.argv = ["init_dataset_build.py", *PAYLOAD["argv"]]
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    try:
        rc = script.main()
    except SystemExit as exc:                                                                            # argparse
        rc = exc.code if isinstance(exc.code, int) else 2
print("HARNESS_RESULT=" + json.dumps({"rc": rc, "stdout": out.getvalue(), "stderr": err.getvalue(), "calls": calls}, ensure_ascii=True, sort_keys=True), flush=True)

"""Bootstrap sys.path + gate MySQL cho test cua dataset builder.

Test thuan (khong can MySQL) luon chay; test `@pytest.mark.mysql` chi chay khi dat `ML_SMOKE=1` va tao/xoa
DISPOSABLE database rieng (prefix `warehouse_edafx*` cua fixture) - khong bao gio dung/sua warehouse da promote.
Fixture warehouse dung lai `eda/src/tests/warehouse_fixture.py` (chay `build_warehouse()` that, ~7 giay).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ML_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = ML_DIR.parent
for path in (ML_DIR, PROJECT_DIR / "eda" / "src", PROJECT_DIR / "eda" / "src" / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(scope="session")
def dataset_wh(tmp_path_factory):
    """Warehouse fixture dung chung (build that ~10 giay): xem `dataset_fixtures.dataset_fixture_spec`."""
    import dataset_fixtures
    from warehouse_fixture import build_fixture_warehouse

    with build_fixture_warehouse(tmp_path_factory.mktemp("dataset_wh"), **dataset_fixtures.dataset_fixture_spec()) as fx:
        yield fx


@pytest.fixture()
def pipeline(dataset_wh, tmp_path, monkeypatch):
    """Tao dataset moi voi registry rieng (record id cua fixture); `_make(events, mode=, cutoff=, purge=)` -> (database, version)."""
    import helpers
    from dataset_builder import anomaly, manifest
    from dataset_builder import config as cfg
    from dataset_builder.db import connect

    created: list[tuple[str, str]] = []

    def _make(events: list[dict], *, mode="evaluation_asof", cutoff=None, purge_gap_days=14, required_label_splits=None, evaluation_horizons=None, purpose="rehearsal", **build_extra):
        import datetime as dt
        cutoff = cutoff if cutoff is not None or mode == "retrospective_full" else dt.datetime(2026, 10, 5)
        path = helpers.registry_file(tmp_path, events)
        monkeypatch.setenv(anomaly.REGISTRY_PATH_ENV, str(path))
        database = dataset_wh["warehouse_database"]
        with connect(database) as conn:
            thresholds = manifest.resolve_pinned_thresholds(conn, dataset_wh["batch_id"])
            config = cfg.build_config(import_batch_id=dataset_wh["batch_id"], purpose=purpose, anomaly_mode=mode,
                                      anomaly_cutoff_at=cutoff, anomaly_registry_file_sha256=cfg.default_registry_sha256_for(path),
                                      purge_gap_days=purge_gap_days, required_label_splits=required_label_splits,
                                      # purge < 14 chi hop le khi horizon duoc danh gia nho hon (fixture 19 mau): mac dinh = cac horizon <= purge
                                      evaluation_horizons=evaluation_horizons or [h for h in (1, 3, 7, 14) if h <= purge_gap_days], **build_extra, **thresholds)
            version = f"ds_20261005_p{len(created)}{abs(hash(str(path) + str(len(created)))) % 9973}"
            manifest.init_dataset_build(conn, dataset_version=version, config=config)
        created.append((database, version))
        return database, version

    yield _make
    for database, version in created:
        helpers.drop_dataset(database, version)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ML_SMOKE") == "1":
        return
    skip_mysql = pytest.mark.skip(reason="can MySQL that - dat ML_SMOKE=1 de chay")
    for item in items:
        if "mysql" in item.keywords:
            item.add_marker(skip_mysql)

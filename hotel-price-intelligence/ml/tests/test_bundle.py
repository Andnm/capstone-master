"""GPT review vong 2 R2-m3: gioi report tu chua cua artifact dataset (6 step + REPORTS_MANIFEST + checksum). Thuan, khong MySQL."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import bundle  # noqa: E402
from dataset_builder.manifest import STEPS  # noqa: E402

META = dict(dataset_version="ds_20261006_t", build_config_sha256="c" * 64, builder_code_sha256="d" * 64)


def _reports(tmp_path: Path, skip: tuple[str, ...] = ()) -> Path:
    report_dir = tmp_path / "_reports" / META["dataset_version"]
    report_dir.mkdir(parents=True)
    for step in STEPS:
        if step not in skip:
            (report_dir / f"{step}.json").write_text(json.dumps({"step": step}), encoding="utf-8")
    return report_dir


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "datasets" / META["dataset_version"]
    out.mkdir(parents=True)
    (out / "samples.parquet").write_bytes(b"PAR1")
    (out / "reports").mkdir()
    (out / "reports" / "split.json").write_text("{\"stale\": true}", encoding="utf-8")        # ban sao cu do features_labels de lai
    return out


def _published(tmp_path: Path):
    out, report_dir = _out(tmp_path), _reports(tmp_path)
    entries = bundle.build_bundle(out_dir=out, report_dir=report_dir, **META)
    stored = bundle.merge_checksums({"samples.parquet": {"file_sha256": "p" * 64}}, entries)
    return out, report_dir, entries, stored


def test_bundle_has_all_six_reports_and_a_manifest_with_hashes(tmp_path):
    out, report_dir, entries, stored = _published(tmp_path)
    names = sorted(p.name for p in (out / "reports").iterdir())
    assert names == sorted([f"{s}.json" for s in STEPS] + [bundle.MANIFEST_NAME])
    manifest = json.loads((out / "reports" / bundle.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["steps"] == list(STEPS) and manifest["dataset_version"] == META["dataset_version"]
    assert manifest["builder_code_sha256"] == META["builder_code_sha256"] and set(manifest["reports"]) == {f"{s}.json" for s in STEPS}
    assert json.loads((out / "reports" / "split.json").read_text(encoding="utf-8")) == {"step": "split"}   # ban cu bi thay bang report that
    assert set(entries) == {f"reports/{s}.json" for s in STEPS} | {"reports/REPORTS_MANIFEST.json"}
    assert not (out / "reports.tmp").exists()
    assert bundle.verify_bundle(out_dir=out, stored=stored, **META) == []


def test_missing_report_refuses_to_publish_a_partial_bundle(tmp_path):
    out, report_dir = _out(tmp_path), _reports(tmp_path, skip=("validation",))
    with pytest.raises(bundle.BundleError, match="validation"):
        bundle.build_bundle(out_dir=out, report_dir=report_dir, **META)
    assert json.loads((out / "reports" / "split.json").read_text(encoding="utf-8")) == {"stale": True}     # khong dong vao ban cu
    assert not (out / "reports.tmp").exists()


def test_missing_output_dir_is_an_error(tmp_path):
    with pytest.raises(bundle.BundleError, match="thu muc output"):
        bundle.build_bundle(out_dir=tmp_path / "none", report_dir=_reports(tmp_path), **META)


def test_merge_replaces_old_report_entries_and_keeps_other_files():
    stored = {"samples.parquet": {"file_sha256": "p" * 64}, "reports/old.json": {"file_sha256": "o" * 64}}
    merged = bundle.merge_checksums(stored, {"reports/new.json": {"file_sha256": "n" * 64}})
    assert set(merged) == {"samples.parquet", "reports/new.json"}
    assert bundle.strip_checksums(merged) == {"samples.parquet": {"file_sha256": "p" * 64}}
    assert bundle.merge_checksums(None, {}) == {}


@pytest.mark.parametrize("tamper, message", [
    (lambda out: (out / "reports" / "split.json").write_text("{}", encoding="utf-8"), "lech hash"),
    (lambda out: (out / "reports" / "validation.json").unlink(), "thieu reports/validation.json"),
    (lambda out: (out / "reports" / bundle.MANIFEST_NAME).unlink(), "thieu reports/REPORTS_MANIFEST.json"),
    (lambda out: (out / "reports" / bundle.MANIFEST_NAME).write_text("not json", encoding="utf-8"), "khong doc duoc JSON"),
])
def test_verify_bundle_detects_tamper(tmp_path, tamper, message):
    out, _, _, stored = _published(tmp_path)
    tamper(out)
    problems = bundle.verify_bundle(out_dir=out, stored=stored, **META)
    assert any(message in p for p in problems), problems


def test_verify_bundle_detects_manifest_rewrite_wrong_metadata_and_missing_db_entries(tmp_path):
    out, _, _, stored = _published(tmp_path)
    manifest_path = out / "reports" / bundle.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["builder_code_sha256"] = "e" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = bundle.verify_bundle(out_dir=out, stored=stored, **META)
    assert any("builder_code_sha256" in p for p in problems) and any("REPORTS_MANIFEST.json thieu hoac lech file" in p or "lech file" in p for p in problems)
    out2, _, _, stored2 = _published(tmp_path / "b")
    assert any("builder_code_sha256" in p for p in bundle.verify_bundle(out_dir=out2, stored=stored2, **{**META, "builder_code_sha256": "f" * 64}))
    assert any("thieu hoac lech" in p for p in bundle.verify_bundle(out_dir=out2, stored=bundle.strip_checksums(stored2), **META))
    forged = dict(stored2)
    forged["reports/split.json"] = {"file_sha256": "0" * 64}
    assert any("reports/split.json" in p for p in bundle.verify_bundle(out_dir=out2, stored=forged, **META))


def test_cleanup_of_validation_removes_only_its_own_artifacts(tmp_path):
    out, _, _, _ = _published(tmp_path)
    (out / "sufficiency_report.json").write_text("{}", encoding="utf-8")
    bundle.remove_validation_artifacts(out)
    remaining = {p.name for p in (out / "reports").iterdir()}
    assert "validation.json" not in remaining and bundle.MANIFEST_NAME not in remaining
    assert {f"{s}.json" for s in STEPS if s != "validation"} <= remaining
    assert (out / "samples.parquet").read_bytes() == b"PAR1" and (out / "sufficiency_report.json").exists()
    bundle.remove_validation_artifacts(out)                              # idempotent
    bundle.remove_validation_artifacts(tmp_path / "does-not-exist")      # thu muc khong co => khong loi


def test_republish_after_cleanup_gives_a_verifiable_bundle_again(tmp_path):
    out, report_dir, _, stored = _published(tmp_path)
    bundle.remove_validation_artifacts(out)
    stored = bundle.strip_checksums(stored)
    assert any("thieu" in p for p in bundle.verify_bundle(out_dir=out, stored=stored, **META))
    entries = bundle.build_bundle(out_dir=out, report_dir=report_dir, **META)
    assert bundle.verify_bundle(out_dir=out, stored=bundle.merge_checksums(stored, entries), **META) == []

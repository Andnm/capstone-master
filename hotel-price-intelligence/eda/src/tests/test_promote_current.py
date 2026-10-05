"""Test `eda/promote_current.py` (promote artifact Wave A thanh pointer hien hanh, fail-closed). Thuan Python, khong can MySQL."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import artifacts

_spec = importlib.util.spec_from_file_location("promote_current", Path(__file__).resolve().parents[2] / "promote_current.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

AID = "eda_b20261004_3src_20261005_000000_abcd"
WH = {"batch_id": "b3", "warehouse_database": "wh3", "source_manifest_sha256": "s", "cohort_manifest_sha256": "c",
      "ownership_manifest_sha256": "o", "canonicalization_version": "v1"}
BULLETS = ["Load pointer và xác minh đúng DB/batch PASS.", "para: Mọi tỷ lệ phải có denominator.", "Định nghĩa “active hotel” phải được ghi cạnh bảng."]
PLAN_TEXT = ("# Plan\n\n- Load pointer và xác minh đúng DB/batch PASS.\n\nMọi tỷ lệ phải có denominator.\n\n"
             "Định nghĩa “active hotel” phải được ghi cạnh bảng, ví dụ hotel có owned item.\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def env(tmp_path):
    outputs = tmp_path / "eda" / "outputs"
    art = outputs / AID
    (art / "tables").mkdir(parents=True)
    (art / "tables" / "t.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with open(art / "EDA_COVERAGE_MATRIX.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["bullet_id", "plan_bullet"])
        writer.writeheader()
        for i, text in enumerate(BULLETS):
            writer.writerow({"bullet_id": f"7.1.{i}", "plan_bullet": text})
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN_TEXT, encoding="utf-8")
    provenance = {"is_dirty": False, "dirty_guarded_files": [], "git_head": "h" * 40, "plan_authority_sha256": _sha(plan),
                  "eda_src_and_notebooks_and_plan_content_sha256": "k" * 64}
    (art / "input_manifest.json").write_text(json.dumps({**WH, "code_provenance": provenance}), encoding="utf-8")
    artifacts.write_artifact_manifest(art)
    wh = tmp_path / "warehouse_current.json"
    wh.write_text(json.dumps(WH), encoding="utf-8")
    review = tmp_path / "review.md"
    review.write_text(f"# Review\n\nPASS WAVE A cho `{AID}`.\n", encoding="utf-8")
    return {"art": art, "outputs": outputs, "plan": plan, "wh": wh, "review": review, "pointer": outputs / "eda_current.json",
            "history": outputs / "eda_pointer_history", "root": tmp_path, "provenance": provenance}


def _promote(env, **kw):
    args = dict(apply=kw.pop("apply", False), pointer_path=env["pointer"], history_dir=env["history"], gate="PASS WAVE A", reviewed_by="GPT",
                review_date="2026-10-06", warehouse_pointer_path=env["wh"], plan_path=env["plan"], repo_root=env["root"],
                now=dt.datetime(2026, 10, 6, 1, 0, 0))
    args.update(kw)
    return pc.promote(env["art"], env["review"], **args)


def test_dry_run_checks_everything_but_writes_nothing(env):
    result = _promote(env)
    assert result["applied"] is False and not env["pointer"].exists()
    p = result["pointer"]
    assert p["analysis_id"] == AID and p["plan_changed_since_run"] is False and p["code_content_sha256"] == "k" * 64
    assert p["artifact_manifest_sha256"] == _sha(env["art"] / "artifact_manifest.json") and p["artifact_file_count"] == 3  # t.csv + matrix + input_manifest (manifest khong tu liet ke)


def test_apply_writes_pointer_archives_old_one_and_rereads(env):
    env["outputs"].mkdir(exist_ok=True)
    env["pointer"].write_text(json.dumps({"analysis_id": "old_artifact", "promoted_at_utc": "2026-09-23T19:43:46Z"}), encoding="utf-8")
    result = _promote(env, apply=True)
    assert result["applied"] and result["verified_after_write"]
    pointer = json.loads(env["pointer"].read_text(encoding="utf-8"))
    assert pointer["analysis_dir"] == AID and pointer["review_gate"] == "PASS WAVE A" and pointer["review_file_sha256"] == _sha(env["review"])
    archived = list(env["history"].glob("eda_current__old_artifact__*.json"))
    assert len(archived) == 1 and json.loads(archived[0].read_text(encoding="utf-8"))["analysis_id"] == "old_artifact"
    assert not list(env["outputs"].glob(".*.tmp"))


def test_plan_changed_but_every_bullet_still_present_is_accepted_and_flagged(env):
    env["plan"].write_text(PLAN_TEXT + "\n**Trạng thái:** cập nhật sau review.\n", encoding="utf-8")
    p = _promote(env)["pointer"]
    assert p["plan_changed_since_run"] is True and p["plan_coverage_bullets_verified_present"] is True
    assert p["plan_authority_sha256_at_promotion"] != p["plan_authority_sha256_at_run"]


def test_plan_changed_and_bullet_removed_is_rejected(env):
    env["plan"].write_text(PLAN_TEXT.replace("Load pointer và xác minh đúng DB/batch PASS.", "Đã bị sửa."), encoding="utf-8")
    with pytest.raises(pc.PromoteError, match="khong con nguyen van"):
        _promote(env)


@pytest.mark.parametrize("mutate, message", [
    (lambda e: (e["art"] / "tables" / "t.csv").write_text("a,b\n9,9\n", encoding="utf-8"), "SHA-256 lech"),
    (lambda e: (e["art"] / "tables" / "extra.csv").write_text("x\n", encoding="utf-8"), "file thua"),
    (lambda e: (e["art"] / "FAILED.json").write_text("{}", encoding="utf-8"), "FAILED.json"),
    (lambda e: e["wh"].write_text(json.dumps({**WH, "ownership_manifest_sha256": "other"}), encoding="utf-8"), "ownership_manifest_sha256"),
    (lambda e: e["review"].write_text("# Review\n\nPASS WAVE A cho artifact khac.\n", encoding="utf-8"), "analysis id"),
    (lambda e: e["review"].write_text(f"# Review\n\n{AID} NOT PASS.\n", encoding="utf-8"), "gate"),
    (lambda e: e["review"].unlink(), "file review"),
])
def test_rejects_when_any_condition_fails(env, mutate, message):
    mutate(env)
    with pytest.raises(pc.PromoteError, match=message):
        _promote(env)
    assert not env["pointer"].exists()


def test_rejects_dirty_provenance(env):
    im_path = env["art"] / "input_manifest.json"
    im = json.loads(im_path.read_text(encoding="utf-8"))
    im["code_provenance"]["is_dirty"] = True
    im_path.write_text(json.dumps(im), encoding="utf-8")
    artifacts.write_artifact_manifest(env["art"])  # manifest khop file moi: chi provenance la ly do tu choi
    with pytest.raises(pc.PromoteError, match="provenance khong sach"):
        _promote(env)

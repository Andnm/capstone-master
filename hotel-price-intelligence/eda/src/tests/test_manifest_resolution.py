"""Test resolve/verify manifest fail-closed (GPT eda file 14 M1) + hai sua trinh bay MIN1/MIN2. Thuan Python, khong can MySQL."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd
import pytest

matplotlib.use("Agg")

import manifest_resolution as mr  # noqa: E402
import plots  # noqa: E402
import report  # noqa: E402
import wave_a  # noqa: E402

POINTER = {"ownership_manifest_sha256": "own-new", "cohort_manifest_sha256": "coh-new", "source_manifest_sha256": "src-new"}


def _fns():
    """Identity gia: doc truong `identity` trong JSON; file hong -> ValueError (nhu loader that)."""
    def ident(path: Path) -> str:
        return json.loads(path.read_text(encoding="utf-8"))["identity"]
    return {"ownership": ident, "cohort": ident, "source": ident}


def _write(dir_: Path, name: str, identity: str | None) -> Path:
    path = dir_ / name
    path.write_text(json.dumps({"identity": identity}) if identity is not None else "{khong phai json", encoding="utf-8")
    return path


def test_resolves_by_identity_not_by_name(tmp_path):
    _write(tmp_path, "ownership_manifest_20260916.json", "own-old")
    new = _write(tmp_path, "ownership_manifest_20261004.json", "own-new")
    _write(tmp_path, "ownership_manifest_20261004_auxonly.json", "own-aux")
    _write(tmp_path, "cohort_history_20260916.json", "coh-new")           # cohort duoc dung lai: ten cu van dung neu identity khop
    _write(tmp_path, "source_manifest_20261004.json", "src-new")
    got = mr.resolve_manifest_paths(POINTER, tmp_path, _fns())
    assert got["ownership"] == new and got["cohort"].name == "cohort_history_20260916.json" and got["source"].name == "source_manifest_20261004.json"


def test_never_falls_back_to_old_named_file(tmp_path):
    _write(tmp_path, "ownership_manifest_20260916.json", "own-old")        # chi co bo ten cu, identity khong khop pointer moi
    _write(tmp_path, "cohort_history_20260916.json", "coh-new")
    _write(tmp_path, "source_manifest_20260916.json", "src-old")
    with pytest.raises(mr.ManifestResolutionError, match="ownership"):
        mr.resolve_manifest_paths(POINTER, tmp_path, _fns())
    with pytest.raises(mr.ManifestResolutionError, match="source"):
        mr.resolve_manifest_paths(POINTER, tmp_path, _fns(), kinds=("source",))


def test_ambiguous_match_and_unparseable_candidates(tmp_path):
    _write(tmp_path, "ownership_manifest_a.json", "own-new")
    _write(tmp_path, "ownership_manifest_b.json", "own-new")
    _write(tmp_path, "ownership_manifest_broken.json", None)
    with pytest.raises(mr.ManifestResolutionError, match="tim thay 2"):
        mr.resolve_manifest_paths(POINTER, tmp_path, _fns(), kinds=("ownership",))
    (tmp_path / "ownership_manifest_b.json").unlink()
    got = mr.resolve_manifest_paths(POINTER, tmp_path, _fns(), kinds=("ownership",))  # broken bi bo qua, con dung mot ung vien
    assert got["ownership"].name == "ownership_manifest_a.json"


def test_explicit_paths_are_verified_against_pointer(tmp_path):
    wrong = _write(tmp_path, "my_own.json", "own-old")
    good = _write(tmp_path, "my_good.json", "own-new")
    with pytest.raises(mr.ManifestResolutionError, match="KHAC pointer"):
        mr.resolve_manifest_paths(POINTER, tmp_path, _fns(), explicit={"ownership": wrong}, kinds=("ownership",))
    assert mr.resolve_manifest_paths(POINTER, tmp_path, _fns(), explicit={"ownership": good}, kinds=("ownership",))["ownership"] == good
    # Pointer fixture khong co khoa identity + duong dan tuong minh -> chap nhan (dry-run tren fixture)
    assert mr.resolve_manifest_paths({}, tmp_path, _fns(), explicit={"ownership": wrong}, kinds=("ownership",))["ownership"] == wrong
    with pytest.raises(mr.ManifestResolutionError, match="khong co ownership_manifest_sha256"):
        mr.resolve_manifest_paths({}, tmp_path, _fns(), kinds=("ownership",))


def test_wave_a_no_longer_hardcodes_old_manifest_names():
    for name in ("OWNERSHIP_MANIFEST_PATH", "COHORT_HISTORY_PATH", "SOURCE_MANIFEST_PATH"):
        assert not hasattr(wave_a, name), name
    source = Path(wave_a.__file__).read_text(encoding="utf-8")
    for prefix in ("ownership_manifest_2026", "cohort_history_2026", "source_manifest_2026"):
        assert prefix not in source, prefix  # khong ten file manifest nao cua mot snapshot cu the


def test_duplicate_section_wording_is_source_count_agnostic():
    def row(scope, source, city, n_groups, dup):
        return {"scope": scope, "source_code": source, "city": city, "n_observations": 10, "n_groups": n_groups, "duplicate_groups": dup,
                "duplicate_group_rate": dup / n_groups, "extra_observations": dup, "same_price_groups": 1, "divergent_price_groups": dup - 1,
                "divergent_share": (dup - 1) / dup, "max_group_size": 3}
    rows = [row(s, src, "(all)", 100, 10) for s in ("RAW", "MAIN") for src in ("(all)", "a", "b", "c")]
    rows += [row("RAW", "(all)", "Hà Nội", 50, 5)]
    summary = pd.DataFrame(rows)
    spread = pd.DataFrame([{"scope": "RAW", "source_code": "(all)", "spread_kind": "relative_symmetric", "n_divergent_groups": 9, "spread_mean": 0.1,
                            "spread_q50": 0.09, "spread_q90": 0.3, "spread_q99": 0.8, "spread_max": 1.5}])
    audit = pd.DataFrame({"group_id": ["g1"], "x": [1]})
    text = report._duplicate_section({"duplicate_series_summary_by_source_city": summary, "duplicate_series_price_spread_summary": spread,
                                      "duplicate_series_audit_sample": audit, "collision_option_coverage_summary": pd.DataFrame()})
    assert "ca hai nguon" not in text and "moi nguon trong snapshot (3 nguon)" in text


def test_duration_axis_label_is_publication_ready():
    df = pd.DataFrame({"source_code": ["a", "b"], "duration_minutes": [10.0, 20.0]})
    fig = plots.plot_run_duration_by_source(df)
    label = fig.axes[0].get_xlabel()
    assert label == "Thời lượng run production (phút)" and "notebook" not in label and "is_protocol_run" not in label
    matplotlib.pyplot.close(fig)

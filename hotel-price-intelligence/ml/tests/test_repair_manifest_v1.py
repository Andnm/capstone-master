"""audit_tools/repair_manifest_v1.py — manifest ma builder tinh THUAN (AST-trich ham that cua code_identity.py, khong import dataset_builder/.env) tren mot repo gia lap + doi chieu cong thuc hash."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
REAL_ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("repair_manifest_v1", TOOLS / "repair_manifest_v1.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)
rp_spec = importlib.util.spec_from_file_location("repair_parity_v1_m", TOOLS / "repair_parity_v1.py")
rp = importlib.util.module_from_spec(rp_spec)
rp_spec.loader.exec_module(rp)

REAL_CODE_IDENTITY = REAL_ROOT / rm.CODE_IDENTITY


def build_mini_repo(root: Path, *, a_text: bytes = b"from app.warehouse.hashing import canonical_json\nfrom app.core.config import settings\nVALUE = 1\n") -> Path:
    project = root / "hotel-price-intelligence"
    files = {
        "ml/dataset_builder/__init__.py": b"",
        "ml/dataset_builder/code_identity.py": REAL_CODE_IDENTITY.read_bytes(),
        "ml/dataset_builder/a.py": a_text,
        "ml/scripts/init_dataset_build.py": b"print('init')\n",
        "ml/scripts/build_dataset.py": b"print('build')\n",
        "backend/app/__init__.py": b"",
        "backend/app/warehouse/__init__.py": b"",
        "backend/app/warehouse/hashing.py": b"import hashlib\n",
        "backend/app/warehouse/etl_ddl.py": b"DDL = 'x'\n",
        "backend/app/database/setup.sql": b"CREATE TABLE t (id INT);\n",
        "backend/app/core/__init__.py": b"",
        "backend/app/core/config.py": b"SECRET = 'must-not-be-in-the-manifest'\n",
    }
    for rel, data in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


@pytest.fixture()
def mini(tmp_path):
    return build_mini_repo(tmp_path / "repo")


def test_manifest_covers_builder_files_clis_ddl_and_the_static_backend_closure_but_not_excluded_prefixes(mini):
    manifest = rm.compute_manifest(mini)
    names = set(manifest["files"])
    p = "hotel-price-intelligence/"
    assert {p + "ml/dataset_builder/a.py", p + "ml/dataset_builder/code_identity.py", p + "ml/dataset_builder/__init__.py", p + "ml/scripts/init_dataset_build.py", p + "ml/scripts/build_dataset.py",
            p + "backend/app/database/setup.sql", p + "backend/app/warehouse/etl_ddl.py", p + "backend/app/warehouse/hashing.py", p + "backend/app/__init__.py", p + "backend/app/warehouse/__init__.py"} <= names
    assert not any("app/core" in n for n in names) and manifest["excluded_backend_prefixes"] == ["app.core"]


def test_code_sha_is_the_documented_formula_over_the_files_and_prefixes_and_every_file_hash_is_the_file_sha(mini):
    manifest = rm.compute_manifest(mini)
    expected = hashlib.sha256(json.dumps({"files": manifest["files"], "excluded_backend_prefixes": ["app.core"]}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert manifest["code_sha256"] == expected == rp.code_sha256_of(manifest["files"], manifest["excluded_backend_prefixes"])
    for rel, digest in manifest["files"].items():
        assert digest == hashlib.sha256((mini / rel).read_bytes()).hexdigest()


def test_one_changed_byte_changes_exactly_that_file_and_the_aggregate(tmp_path):
    base = rm.compute_manifest(build_mini_repo(tmp_path / "a"))
    other = rm.compute_manifest(build_mini_repo(tmp_path / "b", a_text=b"from app.warehouse.hashing import canonical_json\nfrom app.core.config import settings\nVALUE = 2\n"))
    assert base["code_sha256"] != other["code_sha256"]
    assert rm.diff_files(base["files"], other["files"]) == {"changed": ["hotel-price-intelligence/ml/dataset_builder/a.py"], "added": [], "removed": []}


def test_the_manifest_is_identical_for_identical_trees_in_different_locations(tmp_path):
    assert rm.compute_manifest(build_mini_repo(tmp_path / "x"))["code_sha256"] == rm.compute_manifest(build_mini_repo(tmp_path / "y"))["code_sha256"]


def test_logic_comes_from_the_requested_tree_and_matches_the_real_logic_on_the_real_code_identity(mini):
    assert rm.compute_manifest(mini, logic_from=mini) == rm.compute_manifest(mini, logic_from=REAL_ROOT)


def test_a_code_identity_without_the_required_functions_is_a_loud_error(tmp_path):
    root = build_mini_repo(tmp_path / "bad")
    (root / rm.CODE_IDENTITY).write_text("def _sha256(path):\n    return ''\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="thieu"):
        rm.compute_manifest(root)


def test_a_new_static_backend_import_enters_the_closure(tmp_path):
    root = build_mini_repo(tmp_path / "r", a_text=b"from app.warehouse.hashing import canonical_json\nfrom app.warehouse import extra\n")
    (root / "hotel-price-intelligence/backend/app/warehouse/extra.py").write_bytes(b"X = 1\n")
    assert "hotel-price-intelligence/backend/app/warehouse/extra.py" in rm.compute_manifest(root)["files"]


# --------------------------------------------------------------------------- reproduce_pinned
OLD_A = b"line1\nline2\nVALUE = 1\n"
NEW_A = b"line1\nline2\nVALUE = 2\n"


def test_reproduce_pinned_finds_the_single_eol_style_of_the_old_file(tmp_path):
    old_manifest = rm.compute_manifest(build_mini_repo(tmp_path / "old", a_text=OLD_A.replace(b"\n", b"\r\n")))             # file cu o dang CRLF
    current = rm.compute_manifest(build_mini_repo(tmp_path / "new", a_text=NEW_A))
    rel = "hotel-price-intelligence/ml/dataset_builder/a.py"
    result = rm.reproduce_pinned(current, old_manifest["code_sha256"], {rel: OLD_A})                                    # git blob (LF)
    assert result["reproduced"] is True and result["matches"] == [{rel: "CRLF"}] and len(result["tried"]) == 2


def test_reproduce_pinned_reports_no_match_when_another_file_also_changed(tmp_path):
    old_root = build_mini_repo(tmp_path / "old", a_text=OLD_A)
    old_manifest = rm.compute_manifest(old_root)
    new_root = build_mini_repo(tmp_path / "new", a_text=NEW_A)
    (new_root / "hotel-price-intelligence/ml/scripts/build_dataset.py").write_bytes(b"print('changed')\n")
    result = rm.reproduce_pinned(rm.compute_manifest(new_root), old_manifest["code_sha256"], {"hotel-price-intelligence/ml/dataset_builder/a.py": OLD_A})
    assert result["reproduced"] is False and result["matches"] == []


def test_reproduce_pinned_with_two_candidates_checks_every_eol_combination(tmp_path):
    old_a, old_s = OLD_A, b"print('init')\n"
    old_root = build_mini_repo(tmp_path / "old", a_text=old_a)
    (old_root / "hotel-price-intelligence/ml/scripts/init_dataset_build.py").write_bytes(old_s.replace(b"\n", b"\r\n"))
    old_manifest = rm.compute_manifest(old_root)
    new_root = build_mini_repo(tmp_path / "new", a_text=NEW_A)
    (new_root / "hotel-price-intelligence/ml/scripts/init_dataset_build.py").write_bytes(b"print('init2')\n")
    result = rm.reproduce_pinned(rm.compute_manifest(new_root), old_manifest["code_sha256"],
                                 {"hotel-price-intelligence/ml/dataset_builder/a.py": old_a, "hotel-price-intelligence/ml/scripts/init_dataset_build.py": old_s})
    assert result["reproduced"] is True and len(result["tried"]) == 4
    assert result["matches"] == [{"hotel-price-intelligence/ml/dataset_builder/a.py": "LF", "hotel-price-intelligence/ml/scripts/init_dataset_build.py": "CRLF"}]


def test_reproduce_pinned_with_single_line_files_is_ambiguous_and_not_reported_as_reproduced(tmp_path):
    old_root = build_mini_repo(tmp_path / "old", a_text=b"X = 1")                                                       # khong co xuong dong => LF va CRLF cho cung byte
    new_root = build_mini_repo(tmp_path / "new", a_text=b"X = 2")
    result = rm.reproduce_pinned(rm.compute_manifest(new_root), rm.compute_manifest(old_root)["code_sha256"], {"hotel-price-intelligence/ml/dataset_builder/a.py": b"X = 1"})
    assert len(result["matches"]) == 2 and result["reproduced"] is False


def test_eol_variants():
    assert rm.eol_variants(b"a\r\nb\n") == {"LF": b"a\nb\n", "CRLF": b"a\r\nb\r\n"}

"""THUAN: tinh `builder_code_manifest` (code_identity.py) cho MOT cay ma bat ky MA KHONG import `dataset_builder` (package nay nap `backend/.env` luc import).

Cac ham that (`_sha256`, `_module_file`, `_excluded`, `_imports_of`, `backend_closure`, `builder_code_manifest`) va hang so (`EXCLUDED_BACKEND_PREFIXES`, `EXTRA_FILES`) duoc TRICH bang AST tu CHINH `code_identity.py`
cua cay duoc tinh (hoac cua `--logic-from`), roi exec trong namespace rieng voi ML_DIR/REPO_ROOT/BACKEND_DIR tro vao cay can tinh. Nhu vay khong co phep tinh nao duoc viet lai.

    python repair_manifest_v1.py --root <repo root> [--logic-from <repo root khac>] [--out file.json]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

NEEDED_FUNCTIONS = ("_sha256", "_module_file", "_excluded", "_imports_of", "backend_closure", "builder_code_manifest")
NEEDED_ASSIGNS = ("EXCLUDED_BACKEND_PREFIXES", "EXTRA_FILES")
CODE_IDENTITY = Path("hotel-price-intelligence") / "ml" / "dataset_builder" / "code_identity.py"


def compute_manifest(root: Path, *, logic_from: Path | None = None) -> dict[str, Any]:
    """Manifest cua cay `root` (thu muc chua `hotel-price-intelligence/`), dung logic cua `logic_from or root`."""
    root = Path(root)
    source_path = Path(logic_from or root) / CODE_IDENTITY
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in NEEDED_FUNCTIONS)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NEEDED_ASSIGNS for t in n.targets))]
    found = {n.name for n in body if isinstance(n, ast.FunctionDef)} | {t.id for n in body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    missing = (set(NEEDED_FUNCTIONS) | set(NEEDED_ASSIGNS)) - found
    if missing:
        raise RuntimeError(f"code_identity.py thieu: {sorted(missing)}")
    project = root / "hotel-price-intelligence"
    namespace: dict[str, Any] = {"ast": ast, "hashlib": hashlib, "json": json, "Path": Path, "Any": Any, "ML_DIR": project / "ml", "REPO_ROOT": root, "BACKEND_DIR": project / "backend"}
    # chu ky mac dinh cua builder_code_manifest/backend_closure tham chieu ML_DIR/REPO_ROOT/BACKEND_DIR luc DINH NGHIA => phai co san trong namespace truoc exec
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source_path), "exec"), namespace)      # noqa: S102
    return namespace["builder_code_manifest"]()


def diff_files(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    return {"changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]), "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--logic-from", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = compute_manifest(args.root, logic_from=args.logic_from)
    text = json.dumps(manifest, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"code_sha256": manifest["code_sha256"], "files": len(manifest["files"])}))
    return 0


def eol_variants(data: bytes) -> dict[str, bytes]:
    lf = data.replace(b"\r\n", b"\n")
    return {"LF": lf, "CRLF": lf.replace(b"\n", b"\r\n")}


def reproduce_pinned(current: dict[str, Any], pinned_code_sha256: str, candidates: dict[str, bytes]) -> dict[str, Any]:
    """Thu tai tao manifest CU da ghim: lay manifest cay hien tai, thay cac file trong `candidates` (rel -> bytes cua phien ban cu, vd `git show HEAD:rel`) bang MOI to hop kieu xuong dong (LF/CRLF);
    tra cac to hop cho ra dung `pinned_code_sha256`. Neu dung 1 to hop khop => (a) moi file KHAC khong doi ke tu luc dung dataset cu, (b) tap file doi so voi manifest ghim = dung `candidates`."""
    import itertools
    files, prefixes = dict(current["files"]), current["excluded_backend_prefixes"]
    rels = sorted(candidates)
    matches, tried = [], []
    for combo in itertools.product(("LF", "CRLF"), repeat=len(rels)):
        trial = dict(files)
        for rel, style in zip(rels, combo):
            trial[rel] = hashlib.sha256(eol_variants(candidates[rel])[style]).hexdigest()
        sha = hashlib.sha256(json.dumps({"files": trial, "excluded_backend_prefixes": list(prefixes)}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        tried.append({"eol": dict(zip(rels, combo)), "code_sha256": sha})
        if sha == pinned_code_sha256:
            matches.append(dict(zip(rels, combo)))
    return {"pinned": pinned_code_sha256, "matches": matches, "tried": tried, "reproduced": len(matches) == 1}


if __name__ == "__main__":
    raise SystemExit(main())

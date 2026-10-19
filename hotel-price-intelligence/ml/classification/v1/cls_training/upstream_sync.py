"""Sinh + kiem cac ban COPY toi thieu tu regression/shared (D9, GPT 02b §E): copy co manifest, co kiem drift, khong import runtime tu `regression/`.

Moi muc trong `SPECS` mo ta: file nguon (tuong doi `ml/`), cac ten top-level duoc trich NGUYEN VAN (AST `get_source_segment`), dong import do ta tu viet
(khong lay tu nguon), cac thay the chuoi duoc khai bao (hanh vi KHONG doi - chi doi duong dan import). Ban sinh ra co header ghi nguon + SHA-256 (da chuan hoa LF) cua nguon luc copy.
`render(spec)` thuan (khong ghi); `verify()` so ban dang nam tren dia voi ban render lai tu nguon HIEN TAI => nguon doi (drift) hoac ban copy bi sua tay deu bi bat.

Dung o may chinh (can nguon regression/shared). Goi Colab chi mang ban copy + UPSTREAM_MANIFEST.json, khong mang nguon.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

V1_DIR = Path(__file__).resolve().parents[1]
ML_DIR = V1_DIR.parents[1]
UPSTREAM_DIR = Path(__file__).resolve().parent / "upstream"
MANIFEST_PATH = V1_DIR / "UPSTREAM_MANIFEST.json"

SPECS: list[dict[str, Any]] = [
    {"dest": "cv.py", "source": "regression/training/cv.py", "names": ["CVError", "PurgedExpandingWindowSplit"],
     "imports": ["from __future__ import annotations", "", "from typing import Iterator", "", "import numpy as np", "import pandas as pd"], "replace": []},
    {"dest": "feature_spec.py", "source": "shared/dataset_builder/feature_spec.py",
     "names": ["HORIZONS", "HOTEL_SEEN_COLUMNS", "AUDIT_MATCH_COLUMNS", "FORBIDDEN_FEATURES", "LEAD_TIME_BUCKETS", "CITIES", "INFERENCE_MODES", "CATEGORY_DOMAINS"],
     "imports": ["from __future__ import annotations"], "replace": []},
    {"dest": "schema.py", "source": "regression/training/schema.py", "names": ["NON_FEATURE_GROUPS", "CATEGORICAL_COLUMNS", "SchemaError", "read_dictionary", "select_features"],
     "imports": ["from __future__ import annotations", "", "from pathlib import Path", "", "import pandas as pd", "", "from .feature_spec import AUDIT_MATCH_COLUMNS, FORBIDDEN_FEATURES"], "replace": []},
    {"dest": "encoder.py", "source": "regression/training/encoder.py", "names": ["_to_float", "categorical_in", "to_raw", "TreeEncoder"],
     "imports": ["from __future__ import annotations", "", "import numpy as np", "import pandas as pd", "",
                 "from .feature_spec import CATEGORY_DOMAINS", "from .schema import CATEGORICAL_COLUMNS"], "replace": []},
    {"dest": "ratio.py", "source": "regression/training/v3_contract.py",
     "names": ["STABLE_SORT", "DATE_COLUMNS", "RATIO_BLOCK_COLUMNS", "RATIO_BLOCK_DEFINITION", "RATIO_BLOCK_SHA256", "stable_sort", "add_ratio_block"],
     "imports": ["from __future__ import annotations", "", "import hashlib", "", "import numpy as np", "import pandas as pd"], "replace": []},
    {"dest": "n1_policy.py", "source": "shared/dataset_builder/n1_policy.py", "whole": True, "replace": []},
    {"dest": "provenance.py", "source": "regression/training/provenance.py",
     "names": ["CALENDAR_MANIFEST", "CALENDAR_SNAPSHOT", "CONTRACT_NAME", "KNOWN_HORIZONS", "DATASET_FILES", "_SHA", "DatasetVerificationError", "file_sha256", "verify_dataset",
               "PURPOSES", "SUFFICIENCY_STATUSES", "_DATE_KEYS", "_contract_content_problems", "_read_contract", "verify_frame"],
     "imports": ["from __future__ import annotations", "", "import hashlib", "import json", "import re", "from pathlib import Path", "from typing import Any", "", "import pandas as pd", "",
                 "from .n1_policy import snapshot_members"], "replace": []},
    {"dest": "frames.py", "source": "regression/training/runner.py", "names": ["SPLITS", "horizon_frames"],
     "imports": ["from __future__ import annotations", "", "from typing import Any", "", "import pandas as pd", "", "from .provenance import DatasetVerificationError"], "replace": []},
]


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_source(rel: str) -> str:
    return _lf((ML_DIR / rel).read_text(encoding="utf-8"))


def sha256_lf(text: str) -> str:
    return hashlib.sha256(_lf(text).encode("utf-8")).hexdigest()


def _top_level_segments(source: str, names: list[str]) -> list[tuple[str, str]]:
    tree = ast.parse(source)
    found: dict[str, str] = {}
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            targets = [node.name]
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            if name in names:
                segment = ast.get_source_segment(source, node)
                if segment is None:
                    raise RuntimeError(f"khong trich duoc {name}")
                found[name] = segment
    missing = [n for n in names if n not in found]
    if missing:
        raise RuntimeError(f"nguon thieu ten {missing}")
    ordered = [n for n in sorted(found, key=lambda n: source.index(found[n]))]
    return [(n, found[n]) for n in ordered]


def render(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    source = _read_source(spec["source"])
    src_sha = sha256_lf(source)
    header = [f'BAN COPY NGUYEN VAN (khong sua tay) tu `ml/{spec["source"]}`.',
              f"SHA-256 (chuan hoa LF) cua nguon luc copy: {src_sha}",
              "Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y."]
    if spec.get("whole"):
        header.append("Pham vi: toan bo file.")
        body = source
    else:
        header.append("Pham vi: " + ", ".join(spec["names"]) + ".")
        body = "\n\n\n".join(seg for _, seg in _top_level_segments(source, list(spec["names"]))) + "\n"
    if spec["replace"]:
        header.append("Thay the khai bao (chi doi duong dan import): " + "; ".join(f"{a!r} -> {b!r}" for a, b in spec["replace"]) + ".")
    for old, new in spec["replace"]:
        if old not in body:
            raise RuntimeError(f"{spec['dest']}: chuoi thay the {old!r} khong co trong nguon")
        body = body.replace(old, new)
    comment = "\n".join("# " + h for h in header) + "\n"      # comment (khong docstring): whole-file giu nguyen docstring + `from __future__` cua nguon
    if spec.get("whole"):
        text = comment + body
    else:
        text = comment + "\n".join(spec["imports"]) + "\n\n\n" + body
    info = {"dest": f"cls_training/upstream/{spec['dest']}", "source": f"ml/{spec['source']}", "scope": "whole_file" if spec.get("whole") else list(spec["names"]),
            "source_sha256_lf": src_sha, "replacements": [list(r) for r in spec["replace"]], "behavior_changed": False, "dest_sha256_lf": sha256_lf(text)}
    return text, info


def build_manifest() -> dict[str, Any]:
    entries = [render(spec)[1] for spec in SPECS]
    return {"manifest_version": 1, "note": "Ban copy toi thieu; khong import regression/shared luc chay. dest_sha256_lf/source_sha256_lf da chuan hoa xuong dong LF.", "entries": entries}


def write_all() -> None:
    UPSTREAM_DIR.mkdir(parents=True, exist_ok=True)
    (UPSTREAM_DIR / "__init__.py").write_text('"""Ban copy toi thieu tu regression/shared - xem UPSTREAM_MANIFEST.json."""\n', encoding="utf-8", newline="\n")
    for spec in SPECS:
        text, _ = render(spec)
        (UPSTREAM_DIR / spec["dest"]).write_text(text, encoding="utf-8", newline="\n")
    MANIFEST_PATH.write_text(json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def manifest_sha256() -> str:
    return sha256_lf(MANIFEST_PATH.read_text(encoding="utf-8"))


def verify_packaged() -> list[str]:
    """Kiem DICH so voi UPSTREAM_MANIFEST.json (di dong, KHONG can nguon regression/shared): moi file copy ton tai va co hash LF == manifest; khong co file .py la trong upstream/.
    Dung duoc trong Colab sach. Khong chung minh ban copy bang nguon hien tai - viec do la `verify_dev_sources()` (chi o may chinh)."""
    problems: list[str] = []
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"UPSTREAM_MANIFEST.json khong doc duoc: {exc}"]
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["UPSTREAM_MANIFEST.json khong co entries"]
    names = [e.get("dest") for e in entries]
    if len(set(names)) != len(names):
        problems.append("manifest co dest trung")
    listed = set()
    for e in entries:
        dest = str(e.get("dest"))
        listed.add(Path(dest).name)
        path = UPSTREAM_DIR / Path(dest).name
        if not path.is_file():
            problems.append(f"{dest}: thieu file")
        elif sha256_lf(path.read_text(encoding="utf-8")) != e.get("dest_sha256_lf"):
            problems.append(f"{dest}: hash LF != manifest (file bi sua)")
    if UPSTREAM_DIR.is_dir():
        extra = sorted(p.name for p in UPSTREAM_DIR.glob("*.py") if p.name != "__init__.py" and p.name not in listed)
        if extra:
            problems.append(f"upstream/ co file .py ngoai manifest: {extra}")
    return problems


def verify_dev_sources() -> list[str]:
    """Chi o may chinh: ban copy tren dia == ban render lai tu nguon HIEN TAI va khop manifest (bat drift nguon + sua tay). Can `ml/regression` va `ml/shared`."""
    problems: list[str] = []
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"UPSTREAM_MANIFEST.json khong doc duoc: {exc}"]
    declared = {e["dest"]: e for e in manifest.get("entries", [])}
    for spec in SPECS:
        dest = f"cls_training/upstream/{spec['dest']}"
        text, info = render(spec)
        on_disk_path = UPSTREAM_DIR / spec["dest"]
        if not on_disk_path.exists():
            problems.append(f"{dest}: thieu file copy")
            continue
        if sha256_lf(on_disk_path.read_text(encoding="utf-8")) != info["dest_sha256_lf"]:
            problems.append(f"{dest}: file copy tren dia != ban render tu nguon hien tai (nguon drift hoac copy bi sua tay)")
        entry = declared.get(dest)
        if entry is None:
            problems.append(f"{dest}: khong co trong manifest")
        elif entry != info:
            problems.append(f"{dest}: manifest != render hien tai (source_sha256_lf manifest {str(entry.get('source_sha256_lf'))[:12]} vs nguon {info['source_sha256_lf'][:12]})")
    extra = set(declared) - {f"cls_training/upstream/{s['dest']}" for s in SPECS}
    if extra:
        problems.append(f"manifest co muc thua: {sorted(extra)}")
    return problems


verify = verify_dev_sources          # ten cu (tuong thich test/lenh da dung)


if __name__ == "__main__":
    import sys

    if "--write" in sys.argv:
        write_all()
        print("da sinh lai ban copy + UPSTREAM_MANIFEST.json")
    mode = "packaged" if "--packaged" in sys.argv else "dev"
    problems = verify_packaged() if mode == "packaged" else verify_dev_sources()
    print(f"UPSTREAM OK ({mode})" if not problems else f"UPSTREAM LECH ({mode}):\n  " + "\n  ".join(problems))
    sys.exit(1 if problems else 0)

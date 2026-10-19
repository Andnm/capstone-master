"""Notebook Colab (GPT file 62 muc 5): nguon output-free, HORIZONS mac dinh de trong (lay whitelist cua dataset), cell chay duoc cu phap, khong dung duong dan live cua repo."""
from __future__ import annotations

import json
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "train_colab.ipynb"


def _cells():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def _code(cell) -> str:
    return "".join(cell["source"])


def test_notebook_source_is_output_free_and_every_code_cell_compiles():
    for index, cell in enumerate(c for c in _cells() if c["cell_type"] == "code"):
        assert cell.get("execution_count") is None and cell.get("outputs") == [], index
        text = "\n".join(line for line in _code(cell).splitlines() if not line.lstrip().startswith("!") and "google.colab" not in line)   # bo magic Colab
        compile(text, f"cell{index}", "exec")


def test_horizons_default_is_empty_so_single_horizon_packages_use_their_own_whitelist():
    params = _code(_cells()[1])
    assert 'HORIZONS = ""' in params and 'OFFICIAL = False' in params and "1,3,7,14" not in params
    train = _code(_cells()[4])
    assert "HORIZONS_FLAG" in train and "--horizons {HORIZONS}" not in train.replace("HORIZONS_FLAG = f\"--horizons {HORIZONS}\"", "")


def test_notebook_has_the_exact_only_summary_and_warns_not_to_mix_manifests():
    text = "\n".join(_code(c) if c["cell_type"] == "code" else "".join(c["source"]) for c in _cells())
    assert "match_strata" in text and "exact_only" in text and "Không trộn hai gói" in text and "exploratory" in text

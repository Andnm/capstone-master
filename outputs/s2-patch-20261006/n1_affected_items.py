"""N1: item nao (artifact HTML quet toan cohort 24/09) co option bi parser cu doi `breakfast_included` boi cau phu dinh? Ghi JSON {item_id: {options, negation_rows, changed_rows}}. CHI DOC."""
import gzip
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

import measure_facility_lines as m

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load(HERE / "parser_original_503db7d.py", "old")
new = load(HERE / "parser_patched.py", "new")
items = defaultdict(lambda: {"options": 0, "negation_rows": 0, "changed_rows": 0, "affirmative_rows": 0})
for page in sorted(m.ART.rglob("page.html.gz")):
    run_id, item_id = page.parent.parent.name, page.parent.name
    soup = BeautifulSoup(gzip.open(page, "rt", encoding="utf-8", errors="replace").read(), "html.parser")
    for row in soup.select("tr.js-rt-block-row"):
        lines = m.facility_lines(row)
        a, b = old.parse_room_conditions(lines), new.parse_room_conditions(lines)
        entry = items[f"{run_id}/{item_id}"]
        entry["options"] += 1
        if any(new._BREAKFAST_NEGATION_RE.search(line) for line in lines):
            entry["negation_rows"] += 1
        if a["breakfast_included"] != b["breakfast_included"]:
            entry["changed_rows"] += 1
        if b["breakfast_included"] is True:
            entry["affirmative_rows"] += 1
out = {k: v for k, v in items.items() if v["negation_rows"] or v["changed_rows"]}
(HERE / "n1_affected_items.json").write_text(json.dumps({"pages_scanned": len(items), "affected": out}, indent=1), encoding="utf-8")
print("items scanned", len(items), "items with negation/changed rows", len(out), "changed rows", sum(v["changed_rows"] for v in out.values()))

"""Parity cua patch N1 tren facility_lines THAT (artifact HTML fullscan 24/09): parser goc vs parser da va. CHI DOC.

Dieu kien PASS: chi `breakfast_included` duoc doi, chi o dong co cau phu dinh, va moi dong doi deu: cu=True -> moi=False.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

import measure_facility_lines as m

HERE = Path(__file__).resolve().parent


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load(HERE / "parser_original_503db7d.py", "old")
new = load(HERE / "parser_patched.py", "new")

pages = sorted(m.ART.rglob("page.html.gz"))
rows = changed = 0
diff_fields: Counter = Counter()
changed_hotels: set[str] = set()
bad: list = []
for page in pages:
    soup = BeautifulSoup(gzip.open(page, "rt", encoding="utf-8", errors="replace").read(), "html.parser")
    for row in soup.select("tr.js-rt-block-row"):
        lines = m.facility_lines(row)
        a, b = old.parse_room_conditions(lines), new.parse_room_conditions(lines)
        rows += 1
        fields = {k for k in a if a[k] != b[k]}
        if fields:
            changed += 1
            diff_fields.update(fields)
            changed_hotels.add(str(page.parent.parent.name) + "/" + page.parent.name)
            has_negation = any(new._BREAKFAST_NEGATION_RE.search(line) for line in lines)
            if fields != {"breakfast_included"} or not has_negation or not (a["breakfast_included"] is True and b["breakfast_included"] is False):
                bad.append((str(page), lines, fields))
print(f"pages={len(pages)} option_rows={rows} rows_changed_by_patch={changed} ({changed / rows:.2%})")
print(f"fields_changed={dict(diff_fields)} distinct_pages_with_change={len(changed_hotels)}")
print(f"violations_of_parity_contract={len(bad)}")
for item in bad[:5]:
    print(json.dumps({"page": item[0], "lines": item[1], "fields": sorted(item[2])}, ensure_ascii=False)[:400])

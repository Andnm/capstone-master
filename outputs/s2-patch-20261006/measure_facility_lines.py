"""Do dung luong `facility_lines` tho tren artifact HTML that (fullscan 24/09) - GPT review vong 1 muc S2 (7.2): P50/P95/P99/max byte + du phong GB/5M dong.

Mo phong `booking_scraper._get_facility_lines` bang BeautifulSoup (cung chuoi selector, cung bo loc dong/tien) tren moi `tr.js-rt-block-row`. CHI DOC artifact,
khong cham DB. Ket qua la xap xi (bs4 get_text ~ textContent): dung de uoc luong order-of-magnitude truoc DDL, khong thay cho do tren thu thap that.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import numpy as np
from bs4 import BeautifulSoup

ART = Path(r"D:\MSE\CAPSTONE\outputs\fullscan-20260924\run\crawl_artifacts")
SELECTORS = ['.hprt-table-cell-conditions .bui-list__item', '.hprt-conditions-bui .bui-list__item', '.bui-list--text .bui-list__item',
             '.hprt-conditions li', '.hprt-table-cell-conditions li', '.e2e-cancellation[data-testid="cancellation-subtitle"]',
             '.e2e-prepayment[data-testid="prepayment-subtitle"]', '.bui-list__description', '.hprt-conditions-bui li']
BROADER = ['.bui-list__description strong', '.hprt-table-cell-conditions strong', '.policy-title', '[data-testid="policy-title"]']
PRICE_ONLY = re.compile(r'^\s*\d+[\.,\d]*\s*(VND|USD|EUR)?\s*$')


def facility_lines(row) -> list[str]:
    lines: list[str] = []
    for selector in SELECTORS:
        elems = row.select(selector)
        if elems:
            for el in elems:
                text = re.sub(r'^[•\-–—]\s*', '', el.get_text().strip())
                if text and len(text) > 3:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line and len(line) > 3 and not PRICE_ONLY.search(line):
                            lines.append(line)
            break
    if not lines:
        for selector in BROADER:
            for el in row.select(selector):
                text = el.get_text().strip()
                if text and len(text) > 3 and text not in lines:
                    lines.append(text)
    return lines


def main() -> int:
    pages = sorted(ART.rglob("page.html.gz"))
    sizes: list[int] = []
    empty = 0
    for i, page in enumerate(pages):
        html = gzip.open(page, "rt", encoding="utf-8", errors="replace").read()
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("tr.js-rt-block-row"):
            lines = facility_lines(row)
            if not lines:
                empty += 1
            sizes.append(len(json.dumps(lines, ensure_ascii=False).encode("utf-8")))
        if (i + 1) % 200 == 0:
            print(f"  ... {i + 1}/{len(pages)} trang, {len(sizes)} dong", flush=True)
    arr = np.array(sizes)
    print(f"pages={len(pages)} option_rows={len(arr)} rows_without_lines={empty}")
    pct = {p: int(np.percentile(arr, p)) for p in (50, 95, 99)}
    print(f"bytes/option (JSON, utf-8): mean={arr.mean():.1f} P50={pct[50]} P95={pct[95]} P99={pct[99]} max={int(arr.max())}")
    for rows in (3_153_852, 5_000_000, 7_000_000):
        print(f"du phong {rows:>9,} option: mean-based {arr.mean() * rows / 1e9:.2f} GB | P95-based (cap) {pct[95] * rows / 1e9:.2f} GB | P99-based {pct[99] * rows / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

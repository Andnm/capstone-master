"""Ghi đè search context của scraper cho thí nghiệm paired-context (Claude, 2026-09-24).
Nằm NGOÀI repo; chỉ có tác dụng khi biến môi trường MINICRAWL_ADULTS được đặt (API và worker con đều thừa hưởng env).
Không sửa mã đã commit: chỉ cập nhật tại chỗ dict `_SCRAPE_QUERY` của app.scraper.url_utils trước khi bất kỳ URL nào được dựng."""
import os
import sys

_n = os.environ.get("MINICRAWL_ADULTS")
if _n:
    _backend = os.environ.get("MINICRAWL_BACKEND")
    if _backend and _backend not in sys.path:
        sys.path.insert(0, _backend)
    from app.scraper import url_utils  # noqa: E402

    _adults = int(_n)
    url_utils._SCRAPE_QUERY.update({"group_adults": str(_adults), "req_adults": str(_adults), "room1": ",".join(["A"] * _adults)})
    print(f"[minicrawl-sitecustomize pid={os.getpid()}] _SCRAPE_QUERY = {url_utils._SCRAPE_QUERY}", file=sys.stderr, flush=True)

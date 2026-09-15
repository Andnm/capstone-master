"""Read-only VPS scraper API client for the scheduled Excel finalizer.

The host and path prefix are deliberately fixed.  This helper never issues a
mutating HTTP method and emits compact JSON so the automation does not need to
navigate API URLs in a browser.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_ORIGIN = "https://hotel.projecthub.io.vn"
API_PREFIX = "/api/scraper"
PAGE_LIMIT = 200
TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
MAX_PAGE_WORKERS = 6


def api_get(path: str, query: dict[str, Any] | None = None) -> Any:
    if not path.startswith(API_PREFIX + "/"):
        raise ValueError("Refusing a path outside the fixed scraper API prefix")
    params = dict(query or {})
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        params["_cb"] = time.time_ns()
        url = API_ORIGIN + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "capstone-vps-finalizer/2.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"GET failed with HTTP {response.status}")
                return json.load(response)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            time.sleep(attempt)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {last_error}")


def normalize_items_page(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(payload, list):
        return payload, len(payload)
    if not isinstance(payload, dict):
        raise TypeError("Unexpected items response type")
    rows = payload.get("items")
    if rows is None:
        rows = payload.get("data")
    if not isinstance(rows, list):
        raise TypeError("Items response has no list in 'items' or 'data'")
    total = payload.get("total")
    if total is None and isinstance(payload.get("pagination"), dict):
        total = payload["pagination"].get("total")
    if not isinstance(total, int):
        raise TypeError("Items response has no integer total")
    return rows, total


def read_run(run_id: int, include_items: bool) -> dict[str, Any]:
    run = api_get(f"{API_PREFIX}/runs/{run_id}")
    if not isinstance(run, dict):
        raise TypeError("Unexpected run response type")
    result: dict[str, Any] = {"run": run}
    if not include_items:
        return result

    first_payload = api_get(
        f"{API_PREFIX}/runs/{run_id}/items",
        {"limit": PAGE_LIMIT, "offset": 0},
    )
    first_rows, expected_total = normalize_items_page(first_payload)
    if not first_rows and expected_total:
        raise RuntimeError("Items pagination returned an empty first page")

    pages: dict[int, list[dict[str, Any]]] = {0: first_rows}
    offsets = list(range(PAGE_LIMIT, expected_total, PAGE_LIMIT))

    def fetch_page(offset: int) -> tuple[int, list[dict[str, Any]]]:
        payload = api_get(
            f"{API_PREFIX}/runs/{run_id}/items",
            {"limit": PAGE_LIMIT, "offset": offset},
        )
        rows, page_total = normalize_items_page(payload)
        if page_total != expected_total:
            raise RuntimeError(
                f"Items page total changed at offset {offset}: "
                f"{page_total} != {expected_total}"
            )
        if not rows and offset < expected_total:
            raise RuntimeError(f"Empty items page at offset {offset}")
        return offset, rows

    if offsets:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(MAX_PAGE_WORKERS, len(offsets))
        ) as executor:
            futures = {executor.submit(fetch_page, offset): offset for offset in offsets}
            for future in concurrent.futures.as_completed(futures):
                offset, rows = future.result()
                pages[offset] = rows
                print(
                    f"items_page offset={offset} count={len(rows)}",
                    file=sys.stderr,
                    flush=True,
                )

    item_ids: set[Any] = set()
    read_count = 0
    saved_options_count = 0
    duplicate_ids: list[Any] = []

    for offset in sorted(pages):
        rows = pages[offset]
        for row in rows:
            item_id = row.get("id")
            if item_id in item_ids:
                duplicate_ids.append(item_id)
            item_ids.add(item_id)
            saved_options_count += int(row.get("saved_options_count") or 0)
        read_count += len(rows)

    if read_count > expected_total:
        raise RuntimeError("Items pagination exceeded page.total")

    run_total = run.get("total")
    if not isinstance(run_total, int):
        run_total = run.get("total_items")
    result["items_summary"] = {
        "page_total": expected_total,
        "run_total": run_total,
        "read_count": read_count,
        "unique_id_count": len(item_ids),
        "duplicate_ids": duplicate_ids,
        "saved_options_count": saved_options_count,
        "valid": (
            not duplicate_ids
            and read_count == expected_total
            and len(item_ids) == expected_total
            and (run_total is None or run_total == expected_total)
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", type=int)
    group.add_argument("--list-runs", action="store_true")
    parser.add_argument("--include-items", action="store_true")
    args = parser.parse_args()

    try:
        if args.list_runs:
            payload = api_get(f"{API_PREFIX}/runs", {"limit": 100, "offset": 0})
        else:
            if args.run_id <= 0:
                raise ValueError("run-id must be positive")
            payload = read_run(args.run_id, args.include_items)
        json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, urllib.error.URLError) as exc:
        json.dump({"error": type(exc).__name__, "message": str(exc)}, sys.stderr)
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

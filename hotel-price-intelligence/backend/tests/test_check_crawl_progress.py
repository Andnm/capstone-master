from datetime import datetime

from scripts.check_crawl_progress import format_vietnam_time, render_progress


def test_format_vietnam_time_converts_utc_naive_value_to_ict():
    assert format_vietnam_time(datetime(2026, 9, 3, 17, 22, 44)) == (
        "04/09/2026 00:22:44 ICT"
    )


def test_render_progress_contains_only_progress_and_latest_item_lines():
    output = render_progress(
        {"id": 30, "status": "running", "processed": 3944, "total": 4248},
        {"id": 87910, "crawled_at": datetime(2026, 9, 3, 17, 22, 44)},
    )

    assert output.splitlines() == [
        "Run #30: 3944/4248 (92.8%) — running",
        "Item gần nhất: #87910 lúc 04/09/2026 00:22:44 ICT",
    ]


def test_render_progress_handles_no_finished_item():
    output = render_progress(
        {"id": 31, "status": "queued", "processed": 0, "total": 100},
        None,
    )

    assert output.splitlines()[1] == "Item gần nhất: chưa có item nào hoàn tất"

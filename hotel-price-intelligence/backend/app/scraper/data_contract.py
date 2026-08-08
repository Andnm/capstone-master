"""Quy ước crawl có version để mọi observation truy ngược được ngữ cảnh tạo ra nó."""
from datetime import datetime, timezone
import subprocess
from typing import Any, Dict

from app.core.config import settings


def utc_now_naive() -> datetime:
    """MySQL DATETIME lưu UTC không timezone; application luôn truyền UTC-naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def default_crawl_context(save_artifacts: bool = False) -> Dict[str, Any]:
    return {
        "adults": 2,
        "children": 0,
        "rooms": 1,
        "stay_nights": 1,
        "currency": "VND",
        "locale": "vi-VN",
        "timezone": "UTC",
        "display_timezone": settings.DISPLAY_TIMEZONE,
        "login_state": "anonymous",
        "genius_state": "disabled",
        "save_artifacts": bool(save_artifacts),
    }


def current_git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, timeout=3
        ).strip()
    except Exception:
        return None

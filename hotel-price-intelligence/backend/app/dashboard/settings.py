"""Cấu hình riêng của dịch vụ dashboard. KHÔNG tự nạp `.env`, KHÔNG import `app.core.*`. Chỉ đọc biến môi trường `DASHBOARD_*` tường minh (dev/test)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SERVING_ROOT = Path(__file__).resolve().parents[3] / "data" / "serving" / "v1"     # hotel-price-intelligence/data/serving/v1
DEFAULT_CORS_ORIGINS = ("http://localhost:3100", "http://127.0.0.1:3100")
FORBIDDEN_PORT = 8000                                                                          # cổng API cào vận hành: dịch vụ này KHÔNG được bind


@dataclass(frozen=True)
class Settings:
    serving_root: Path
    cors_origins: tuple[str, ...]
    host: str = "127.0.0.1"
    port: int = 8100


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    root = Path(env.get("DASHBOARD_SERVING_ROOT", str(DEFAULT_SERVING_ROOT)))
    origins_raw = env.get("DASHBOARD_CORS_ORIGINS")
    origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip()) if origins_raw else DEFAULT_CORS_ORIGINS
    if any(o == "*" for o in origins):
        raise ValueError("CORS wildcard bị cấm (credentials)")
    host = env.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(env.get("DASHBOARD_PORT", "8100"))
    if port == FORBIDDEN_PORT:
        raise ValueError("cổng 8000 là API cào vận hành; dịch vụ dashboard không được dùng")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("dịch vụ dashboard chỉ bind loopback")
    return Settings(serving_root=root, cors_origins=origins, host=host, port=port)

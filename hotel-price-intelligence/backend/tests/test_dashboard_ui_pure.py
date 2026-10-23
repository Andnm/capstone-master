"""Test thuần của logic UI (U1/U2 của GPT 14) chạy bằng Node trên MÃ THẬT `dashboard-frontend/lib/*.ts` (transpile bằng TypeScript compiler, fetch/timer giả),
cộng guard cấu trúc được kiểm trên PHẢN HỒI THẬT do API sinh từ snapshot tổng hợp (pydantic → JSON). Không mạng, không backend ngoài app in-process."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard_fixtures as F
from app.dashboard.app import create_app
from app.dashboard.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "dashboard-frontend"
SCRIPT = FRONTEND / "scripts" / "ui-pure-tests.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None or not SCRIPT.exists() or not (FRONTEND / "node_modules" / "typescript").exists(), reason="thiếu node/typescript của dashboard-frontend")
P = "/api/dashboard/v1"


def run_node(*extra: str):
    return subprocess.run(["node", str(SCRIPT), *extra], cwd=FRONTEND, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def dump(directory: Path, endpoint: str, label: str, body) -> None:
    (directory / f"{endpoint}__{label}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_ui_pure_logic_and_api_guard_pass_on_real_serialized_responses(tmp_path):
    out_dir = tmp_path / "responses"
    out_dir.mkdir()
    for variant, classifier in (("plain", False), ("classifier", True)):
        root = tmp_path / f"serving-{variant}"
        F.write_snapshot(root, classifier_available=classifier)
        client = F.SyncClient(create_app(Settings(serving_root=root, cors_origins=())))
        get = lambda path, **params: client.get(P + path, params=params).json()   # noqa: E731
        dump(out_dir, "config", variant, get("/config"))
        dump(out_dir, "hotels", variant, get("/hotels"))
        dump(out_dir, "model-info", variant, get("/model-info"))
        for label, hotel, day in (("normal", "anchor-hotel", "2026-10-12"), ("lead1", "anchor-hotel", "2026-10-05"), ("cold_null_span", "noname-slug", "2026-10-12")):
            dump(out_dir, "forecast", f"{variant}_{label}", get("/forecast", hotelId=hotel, checkIn=day))
        for label, hotel in (("ok", "anchor-hotel"), ("insufficient", "lonely"), ("no_review", "norev"), ("cold", "noname-slug")):
            dump(out_dir, "hotelier", f"{variant}_{label}", get("/hotelier", hotelId=hotel, checkIn="2026-10-12"))
    result = run_node("--responses", str(out_dir))
    assert result.returncode == 0, (result.stdout + result.stderr)[-1500:]
    assert "ui-pure-tests: OK" in result.stdout and "gồm phản hồi API thật" in result.stdout


def test_ui_pure_logic_alone_passes():
    result = run_node()
    assert result.returncode == 0, (result.stdout + result.stderr)[-1500:]
    assert "ui-pure-tests: OK" in result.stdout

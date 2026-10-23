"""MIN3/A7 (GPT 10): cô lập chạy thật bằng AUDIT HOOK trong tiến trình con — chặn mở `.env`, kết nối cổng 3306/8000 hoặc ngoài loopback, import mô-đun vận hành/DB/ML.
Kèm negative control chứng minh hook thật sự chặn mã đọc `.env` / import mysql / kết nối 3306 (hook chặn TRƯỚC khi kết nối nên KHÔNG chạm DB thật)."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

HOOK = textwrap.dedent('''
    import os, sys
    FORBIDDEN_IMPORT = ("app.core", "app.scraper", "app.warehouse", "app.database", "app.api", "mysql", "sklearn", "joblib", "xgboost", "pyarrow", "numpy", "pandas", "selenium", "dotenv")
    BLOCKED_PORTS = {3306, 8000}
    def hook(event, args):
        if event == "open":
            raw = args[0] if args else ""
            path = os.fsdecode(raw) if isinstance(raw, (str, bytes, os.PathLike)) else ""
            base = os.path.basename(path.replace("\\\\", "/")).lower()
            if base == ".env" or base.startswith(".env.") or base.endswith(".env"):
                raise RuntimeError("AUDIT_BLOCK open_env " + path)
        elif event == "socket.connect":
            address = args[1] if len(args) > 1 else None
            if isinstance(address, tuple) and len(address) >= 2:
                host, port = address[0], address[1]
                if port in BLOCKED_PORTS or str(host) not in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
                    raise RuntimeError(f"AUDIT_BLOCK connect {host}:{port}")
        elif event == "import":
            name = args[0]
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORT):
                raise RuntimeError("AUDIT_BLOCK import " + name)
    sys.addaudithook(hook)
''')


def run(body: str, *, env_extra: dict | None = None):
    code = HOOK + "\n" + textwrap.dedent(body)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env)


def test_api_runs_all_endpoints_under_audit_hook_without_env_db_or_heavy_imports(tmp_path):
    body = f'''
        import json, sys
        from pathlib import Path
        sys.path.insert(0, {str(TESTS)!r})
        sys.path.insert(0, {str(BACKEND)!r})
        import dashboard_fixtures as F
        root = Path({str(tmp_path / "serving")!r})
        F.write_snapshot(root)
        from app.dashboard.app import create_app
        from app.dashboard.settings import Settings
        client = F.SyncClient(create_app(Settings(serving_root=root, cors_origins=())))
        P = "/api/dashboard/v1"
        statuses = {{}}
        for path, params in (("/health", {{}}), ("/config", {{}}), ("/hotels", {{}}), ("/forecast", {{"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}}),
                             ("/hotelier", {{"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}}), ("/model-info", {{}}), ("/forecast", {{"hotelId": "ghost", "checkIn": "2026-10-12"}})):
            statuses[path + str(params)] = client.get(P + path, params=params).status_code
        print("STATUSES " + json.dumps(statuses))
    '''
    out = run(body)
    assert out.returncode == 0, out.stderr[-1500:]
    statuses = json.loads(out.stdout.split("STATUSES ", 1)[1])
    assert sorted(set(statuses.values())) == [200, 404]


@pytest.mark.parametrize("snippet, expect", [
    ("open('.env')", "AUDIT_BLOCK open_env"),
    ("open(r'C:\\\\somewhere\\\\backend\\\\.env', 'rb')", "AUDIT_BLOCK open_env"),
    ("import mysql.connector", "AUDIT_BLOCK import mysql"),
    ("import sklearn", "AUDIT_BLOCK import sklearn"),
    ("import app.core.database", "AUDIT_BLOCK import app.core"),
    ("import socket; socket.create_connection(('127.0.0.1', 3306), timeout=0.2)", "AUDIT_BLOCK connect 127.0.0.1:3306"),
    ("import socket; socket.create_connection(('127.0.0.1', 8000), timeout=0.2)", "AUDIT_BLOCK connect 127.0.0.1:8000"),
])
def test_negative_controls_hook_really_blocks_forbidden_access(snippet, expect):
    out = run(f"import sys\nsys.path.insert(0, {str(BACKEND)!r})\n{snippet}\n")
    assert out.returncode != 0 and expect in out.stderr, out.stderr[-800:]


def test_settings_do_not_read_env_files(tmp_path, monkeypatch):
    body = f'''
        import sys
        sys.path.insert(0, {str(BACKEND)!r})
        from app.dashboard.settings import load_settings
        s = load_settings({{"DASHBOARD_SERVING_ROOT": {str(tmp_path)!r}}})
        print("ROOT " + str(s.serving_root))
    '''
    out = run(body)
    assert out.returncode == 0 and "ROOT" in out.stdout, out.stderr[-800:]

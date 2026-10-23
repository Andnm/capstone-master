"""C13 — ranh giới import & cô lập: dashboard API KHÔNG dùng DB/ML/vận hành; ml_serving KHÔNG dùng DB/scraper/warehouse; không đọc .env; không route nạp file/upload/scrape."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DASHBOARD = sorted((BACKEND / "app" / "dashboard").rglob("*.py"))
SERVING = sorted((BACKEND / "app" / "ml_serving").rglob("*.py"))
ENTRY = BACKEND / "dashboard_main.py"

OPERATIONAL = ("app.core", "app.scraper", "app.warehouse", "app.database", "app.api", "app.schemas", "main")
DASHBOARD_FORBIDDEN = OPERATIONAL + ("mysql", "sklearn", "joblib", "xgboost", "pyarrow", "numpy", "pandas", "scipy", "selenium")
SERVING_FORBIDDEN = OPERATIONAL + ("mysql", "selenium")


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                                                   # import tương đối: chỉ gói con cùng thư mục (ok)
                continue
            names.add(node.module or "")
    return names


def violations(files, forbidden):
    found = []
    for path in files:
        for name in imported_roots(path):
            for bad in forbidden:
                if name == bad or name.startswith(bad + "."):
                    found.append(f"{path.relative_to(BACKEND)}: {name}")
    return found


def test_dashboard_has_no_forbidden_imports():
    assert DASHBOARD, "app/dashboard chưa có file"
    assert violations(DASHBOARD, DASHBOARD_FORBIDDEN) == []


def test_dashboard_entry_has_no_forbidden_imports_when_present():
    if ENTRY.exists():
        assert violations([ENTRY], DASHBOARD_FORBIDDEN) == []


def test_ml_serving_has_no_operational_or_db_imports():
    assert violations(SERVING, SERVING_FORBIDDEN) == []


def code_tokens(path: Path) -> set[str]:
    """Định danh + chuỗi hằng KHÔNG phải docstring (docstring được phép NÓI về .env/subprocess; mã thì không dùng)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node, clean=False) is not None:
            docstrings.add(id(node.body[0].value))
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            tokens.add(node.value)
        elif isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            tokens.update(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                tokens.add(node.module)
    return tokens


def test_no_env_file_is_read_and_no_upload_or_download_routes():
    forbidden_names = {"load_dotenv", "dotenv", "UploadFile", "File", "pickle", "requests", "urlopen", "Popen", "subprocess"}
    for path in DASHBOARD + SERVING + ([ENTRY] if ENTRY.exists() else []):
        tokens = code_tokens(path)
        rel = path.relative_to(BACKEND)
        assert not any(t.endswith(".env") or t == ".env" for t in tokens), f"{rel} đọc .env"
        assert not (forbidden_names & tokens), f"{rel}: {sorted(forbidden_names & tokens)}"
        if path.name != "registry.py":                                         # loader mặc định (joblib.load) CHỈ ở registry, sau hash/runtime check
            assert "joblib" not in tokens, f"{rel} dùng joblib ngoài registry"


def _imports_in_subprocess(modules: list[str], forbidden: tuple[str, ...]) -> list[str]:
    code = ("import sys, importlib\n"
            f"for m in {modules!r}: importlib.import_module(m)\n"
            f"bad = sorted({{n for n in sys.modules for f in {forbidden!r} if n == f or n.startswith(f + '.')}})\n"
            "print('BAD:' + ','.join(bad))\n")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    line = [l for l in out.stdout.splitlines() if l.startswith("BAD:")][-1]
    return [x for x in line[4:].split(",") if x]


def test_runtime_import_trap_dashboard_modules_do_not_pull_db_or_ml():
    modules = ["app.dashboard." + p.stem for p in DASHBOARD if p.stem != "__init__"]
    assert modules
    assert _imports_in_subprocess(modules, DASHBOARD_FORBIDDEN) == []


def test_runtime_import_trap_registry_and_snapshot_do_not_open_db_or_scraper():
    bad = _imports_in_subprocess(["app.ml_serving.registry", "app.ml_serving.snapshot", "app.ml_serving.inference"], SERVING_FORBIDDEN)
    assert bad == []


FIT_CALLS = {"fit", "fit_transform", "partial_fit", "fit_predict"}


def fit_call_sites(files, base: Path = BACKEND) -> list[str]:
    found = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FIT_CALLS:
                found.append(f"{path.relative_to(base)}:{node.lineno}: .{node.func.attr}()")
    return found


def test_inference_only_no_fit_call_anywhere_in_serving_dashboard_or_their_tests():
    """Bộ kiểm của lớp serving là INFERENCE-ONLY: không `.fit/.fit_transform/.partial_fit/.fit_predict` trong mã serving, mã dashboard hay chính các test của chúng (GPT 18: một test từng fit để dựng Booster).
    Quét AST (không tính chuỗi/chú thích), nên việc nhắc tên trong tài liệu không bị coi là gọi hàm."""
    tests = sorted((BACKEND / "tests").glob("test_ml_serving_*.py")) + sorted((BACKEND / "tests").glob("test_dashboard_*.py")) + [BACKEND / "tests" / "dashboard_fixtures.py"]
    assert fit_call_sites(SERVING + DASHBOARD + [ENTRY] + tests) == []


def test_fit_scanner_negative_control(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("model.fit(x, y)\nvalue = 'model.fit(x)'\nother.fit_transform(x)\n", encoding="utf-8")
    assert fit_call_sites([sample], base=tmp_path) == ["sample.py:1: .fit()", "sample.py:3: .fit_transform()"]        # chuỗi 'model.fit(x)' không bị đếm

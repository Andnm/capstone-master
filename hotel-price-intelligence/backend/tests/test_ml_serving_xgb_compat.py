"""Tương thích nạp Booster Linux→Windows (`xgb_compat`) — KHÔNG fit, không train: trình tách UBJSON có cấu trúc, wheel đóng gói được GHIM bằng hash RECORD trong registry (inventory + chính sách bytecode),
patch `__setstate__` chỉ sống trong khối `with` (không lồng, không đồng thời), và buffer THẬT của bundle champion (Linux/GPU) trên xgboost Windows.
Phần cần xgboost/bundle thật bỏ qua nếu thiếu artifact cục bộ; phần còn lại dùng buffer UBJSON tổng hợp, thư viện giả và module `xgboost` giả."""
import base64
import hashlib
import marshal
import os
import struct
import subprocess
import sys
import threading
import types
from importlib.util import MAGIC_NUMBER
from pathlib import Path

import pytest

from app.ml_serving import xgb_compat as X
from app.ml_serving.errors import IntegrityError
from app.ml_serving.registry import WORKSPACE_ROOT

BACKEND = Path(__file__).resolve().parents[1]
LIBS = WORKSPACE_ROOT / "hotel-price-intelligence" / "data" / "serving" / "libs" / "xgboost-3.4.1"


# ---- bộ ghi UBJSON tối thiểu, đúng bố cục XGBoost (khóa = độ dài kiểu-L + chuỗi; object không có số đếm; mảng có số đếm) ----
def length(n: int) -> bytes:
    return b"L" + struct.pack(">q", n)


def key(name: str) -> bytes:
    raw = name.encode()
    return length(len(raw)) + raw


def string(text: str) -> bytes:
    raw = text.encode()
    return b"S" + length(len(raw)) + raw


def obj(**items: bytes) -> bytes:
    return b"{" + b"".join(key(k) + v for k, v in items.items()) + b"}"


def typed_floats(values: list[float]) -> bytes:
    return b"[$d#" + length(len(values)) + b"".join(struct.pack(">f", v) for v in values)


def counted_ints(values: list[int]) -> bytes:
    return b"[#" + length(len(values)) + b"".join(b"i" + struct.pack(">b", v) for v in values)


MODEL = obj(learner=obj(attributes=obj(best=string("1")), weights=typed_floats([0.5, -1.25, 3.0]), version=counted_ints([3, 4, 1])), version=counted_ints([3, 4, 1]))
CONFIG = obj(learner=obj(generic_param=obj(rng_state=string("285477126 1632178144 600"), device=string("cuda:0"))), version=counted_ints([3, 4, 1]))
BUFFER = b"{" + key("Config") + CONFIG + key("Model") + MODEL + b"}"


def test_split_sections_finds_exact_config_and_model_spans():
    spans = X.split_sections(BUFFER)
    assert set(spans) == {"Config", "Model"}
    assert BUFFER[spans["Config"][0]:spans["Config"][1]] == CONFIG
    assert BUFFER[spans["Model"][0]:spans["Model"][1]] == MODEL
    assert X.extract_model_section(BUFFER) == MODEL
    assert X.extract_model_section(bytearray(BUFFER)) == MODEL


def test_model_bytes_do_not_depend_on_the_config_rng_text():
    """Cùng Model nhưng rng_state khác định dạng (thập phân vs thập lục phân) ⇒ phần Model tách ra giống hệt: đúng lý do chỉ nạp Model."""
    hexified = obj(learner=obj(generic_param=obj(rng_state=string("1e0b3e2 6cf0f828 258"), device=string("cpu"))), version=counted_ints([3, 4, 1]))
    other = b"{" + key("Config") + hexified + key("Model") + MODEL + b"}"
    assert X.extract_model_section(other) == X.extract_model_section(BUFFER) == MODEL


@pytest.mark.parametrize("bad", [
    BUFFER[:-1],                                                         # thiếu } cuối
    BUFFER[: len(BUFFER) // 2],                                          # cắt giữa chừng
    BUFFER + b"\x00",                                                    # byte thừa
    b"[" + BUFFER[1:],                                                   # không phải object
    b"{" + key("Config") + CONFIG + b"}",                                # thiếu Model
    b"{" + key("Model") + MODEL + key("Model") + MODEL + b"}",           # khóa trùng
    b"{" + key("Config") + CONFIG + key("Model") + MODEL + key("Extra") + MODEL + b"}",     # khóa lạ
    b"{" + key("Config") + CONFIG + key("Model") + b"?" + b"}",          # marker lạ
])
def test_malformed_buffers_fail_closed(bad):
    with pytest.raises(X.CompatError):
        X.extract_model_section(bad)


def test_a_declared_length_past_the_end_is_rejected_not_read():
    bad = b"{" + key("Config") + b"S" + length(10**9) + b"x" + b"}"
    with pytest.raises(X.CompatError):
        X.split_sections(bad)


# ---- thư viện giả: RECORD là nguồn sự thật hash, pin RECORD nằm ở registry ----
def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


TAG = "py3-none-win_amd64"


def write_record(root: Path, lines: list[str]) -> str:
    record = root / "fakelib-1.0.dist-info" / "RECORD"
    record.parent.mkdir(exist_ok=True)
    record.write_text("\n".join(lines + ["fakelib-1.0.dist-info/RECORD,,"]) + "\n", encoding="utf-8")
    return hashlib.sha256(record.read_bytes()).hexdigest()


def make_fake_wheel(root: Path, files: dict[str, bytes], *, tag: str = TAG) -> str:
    """Ghi file + RECORD + WHEEL; trả sha256 của RECORD (= pin do người review đặt vào registry)."""
    lines = []
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{name},sha256={_digest(data)},{len(data)}")
    wheel = root / "fakelib-1.0.dist-info" / "WHEEL"
    wheel.parent.mkdir(exist_ok=True)
    wheel.write_text(f"Wheel-Version: 1.0\nTag: {tag}\n", encoding="utf-8")
    lines.append(f"fakelib-1.0.dist-info/WHEEL,sha256={_digest(wheel.read_bytes())},{wheel.stat().st_size}")       # như wheel thật: mọi file dist-info đều nằm trong RECORD
    return write_record(root, lines)


BASE = {"fakelib/__init__.py": b"x = 1\n", "fakelib/lib.dll": b"\x00\x01" * 100}


def verify(root: Path, pin: str, tag: str = TAG) -> dict:
    return X.verify_vendored_wheel(root, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=tag)


def test_vendored_wheel_verifies_pin_hashes_sizes_and_inventory(tmp_path):
    pin = make_fake_wheel(tmp_path, {**BASE, "fakelib/__pycache__/x.cpython-313.pyc": b"bytecode-not-hashed"})
    info = verify(tmp_path, pin)
    assert info["filesVerified"] == 3 and info["wheelTag"] == TAG and info["recordSha256"] == pin


def test_changing_a_file_AND_its_record_together_is_caught_by_the_pin(tmp_path):
    """Phản ví dụ của GPT 18 W1: đổi file rồi cập nhật RECORD cho khớp. RECORD tự khai không còn đủ — phải khớp pin đã review."""
    pin = make_fake_wheel(tmp_path, BASE)
    changed = b"x = 2\n"
    (tmp_path / "fakelib" / "__init__.py").write_bytes(changed)
    wheel = tmp_path / "fakelib-1.0.dist-info" / "WHEEL"
    new_pin = write_record(tmp_path, [f"fakelib/__init__.py,sha256={_digest(changed)},{len(changed)}", f"fakelib/lib.dll,sha256={_digest(BASE['fakelib/lib.dll'])},200",
                                      f"fakelib-1.0.dist-info/WHEEL,sha256={_digest(wheel.read_bytes())},{wheel.stat().st_size}"])
    assert new_pin != pin
    assert verify(tmp_path, new_pin)["filesVerified"] == 3                                  # tự nhất quán ⇒ chỉ pin mới chặn được
    with pytest.raises(IntegrityError, match="khác bản đã ghim"):
        verify(tmp_path, pin)


def test_changing_only_a_file_is_caught_by_hash(tmp_path):
    pin = make_fake_wheel(tmp_path, BASE)
    (tmp_path / "fakelib" / "lib.dll").write_bytes(b"tampered")
    with pytest.raises(IntegrityError, match="kích thước|không khớp RECORD"):
        verify(tmp_path, pin)
    (tmp_path / "fakelib" / "lib.dll").write_bytes(b"\x00\x02" * 100)                       # cùng kích thước, khác nội dung
    with pytest.raises(IntegrityError, match="sha256 file thư viện không khớp"):
        verify(tmp_path, pin)


@pytest.mark.parametrize("bad_pin", [None, "", "abc", "A" * 64, "g" * 64, 123])
def test_missing_or_malformed_expected_digest_is_refused(tmp_path, bad_pin):
    make_fake_wheel(tmp_path, BASE)
    with pytest.raises(IntegrityError, match="thiếu/sai định dạng"):
        verify(tmp_path, bad_pin)


def test_wheel_tag_must_match_the_pinned_tag(tmp_path):
    pin = make_fake_wheel(tmp_path, BASE, tag="py3-none-manylinux_2_28_x86_64")
    with pytest.raises(IntegrityError, match="thẻ wheel"):
        verify(tmp_path, pin)
    assert verify(tmp_path, pin, tag="py3-none-manylinux_2_28_x86_64")["wheelTag"] == "py3-none-manylinux_2_28_x86_64"


@pytest.mark.parametrize("rel", ["evil.py", "fakelib/extra.py", "fakelib/native.pyd", "fakelib/native.so", "fakelib/lib2.dll", "sitecustomize.pth", "fakelib/legacy.pyc", "fakelib/__pycache__/notbytecode.py",
                                 "zzz/pkg/__init__.py"])
def test_importable_or_any_file_outside_the_inventory_is_refused(tmp_path, rel):
    pin = make_fake_wheel(tmp_path, BASE)
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_bytes(b"VALUE = 'shadow'\n")
    with pytest.raises(IntegrityError, match="ngoài inventory"):
        verify(tmp_path, pin)


def test_record_entries_must_be_hashed_sized_unique_and_inside_the_library(tmp_path):
    make_fake_wheel(tmp_path, BASE)
    wheel = tmp_path / "fakelib-1.0.dist-info" / "WHEEL"
    good = [f"fakelib/__init__.py,sha256={_digest(BASE['fakelib/__init__.py'])},6", f"fakelib/lib.dll,sha256={_digest(BASE['fakelib/lib.dll'])},200",
            f"fakelib-1.0.dist-info/WHEEL,sha256={_digest(wheel.read_bytes())},{wheel.stat().st_size}"]
    for lines, message in (
        (good + ["../outside.py,sha256=AAAA,1"], "ngoài thư viện"),
        (good + ["/abs/outside.py,sha256=AAAA,1"], "ngoài thư viện"),
        (good + ["C:/x/y.py,sha256=AAAA,1"], "ngoài thư viện"),
        (good + ["pkg\\\\evil.py,sha256=AAAA,1"], "ngoài thư viện"),
        (good + ["fakelib/unhashed.py,,"], "sha256/kích thước"),
        ([good[0].rsplit(",", 1)[0] + ",999", good[1]], "kích thước"),
        (good + [good[0]], "trùng mục"),
    ):
        pin = write_record(tmp_path, lines)
        with pytest.raises(IntegrityError, match=message):
            verify(tmp_path, pin)


def test_symlink_inside_the_library_is_refused(tmp_path):
    pin = make_fake_wheel(tmp_path, BASE)
    try:
        os.symlink(tmp_path / "fakelib" / "lib.dll", tmp_path / "fakelib" / "link.dll")
    except OSError:
        pytest.skip("không tạo được symlink (thiếu quyền Windows)")
    with pytest.raises(IntegrityError, match="symlink|ngoài inventory"):
        verify(tmp_path, pin)


def test_missing_record_is_refused(tmp_path):
    pin = make_fake_wheel(tmp_path, BASE)
    with pytest.raises(IntegrityError, match="thiếu RECORD"):
        X.verify_vendored_wheel(tmp_path, "fakelib", "9.9", expected_record_sha256=pin, expected_wheel_tag=TAG)


@pytest.fixture
def clean_sys(monkeypatch):
    """Khôi phục sys.path/sys.modules/chính sách bytecode sau test kích hoạt thư viện giả."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)                                  # trạng thái xuất phát xác định (monkeypatch trả lại giá trị thật sau test)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    monkeypatch.setattr(X, "_ACTIVATED_LIBS", set())
    monkeypatch.setattr(X, "_POLICY_PREFIX", None)
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


def test_failed_verification_touches_neither_sys_path_nor_imports_nor_bytecode_policy(tmp_path, clean_sys):
    """Bẫy: `fakelib/__init__.py` ghi file đánh dấu nếu được import. Mọi thất bại phải dừng TRƯỚC import/sys.path/chính sách."""
    marker = tmp_path / "IMPORTED.marker"
    pin = make_fake_wheel(tmp_path, {"fakelib/__init__.py": f"open({str(marker)!r}, 'w').write('x')\n".encode(), "fakelib/lib.dll": b"\x00" * 10})
    path_before, modules_before, policy_before = list(sys.path), set(sys.modules), (sys.dont_write_bytecode, sys.pycache_prefix)
    for kwargs in ({"expected_record_sha256": "0" * 64, "expected_wheel_tag": TAG}, {"expected_record_sha256": pin, "expected_wheel_tag": "wrong"}, {"expected_record_sha256": "", "expected_wheel_tag": TAG}):
        with pytest.raises(IntegrityError):
            X.activate_vendored_lib(tmp_path, "fakelib", "1.0", **kwargs)
    (tmp_path / "fakelib" / "evil.py").write_text("print('x')\n")
    with pytest.raises(IntegrityError, match="ngoài inventory"):
        X.activate_vendored_lib(tmp_path, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)
    new_modules = {m for m in set(sys.modules) - modules_before if not m.startswith("encodings.")}                  # `encodings.*` do chính test ghi file (locale), không phải thư viện
    assert sys.path == path_before and not new_modules and (sys.dont_write_bytecode, sys.pycache_prefix) == policy_before
    assert not marker.exists() and "fakelib" not in sys.modules


def test_successful_activation_sets_path_once_and_the_bytecode_policy(tmp_path, clean_sys):
    pin = make_fake_wheel(tmp_path, BASE)
    info = X.activate_vendored_lib(tmp_path, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)
    assert sys.path[0] == str(tmp_path.resolve()) and info["bytecodePolicy"] == {"dontWriteBytecode": True, "pycachePrefixSet": True, "policyOwnedPrefix": True, "replacedExistingPrefix": False}
    assert sys.dont_write_bytecode is True and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists()
    X.activate_vendored_lib(tmp_path, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)
    assert sys.path.count(str(tmp_path.resolve())) == 1


def test_library_already_imported_from_elsewhere_is_refused(tmp_path, clean_sys, monkeypatch):
    pin = make_fake_wheel(tmp_path, BASE)
    elsewhere = types.ModuleType("fakelib")
    elsewhere.__file__ = str(Path(__file__).resolve())
    monkeypatch.setitem(sys.modules, "fakelib", elsewhere)
    with pytest.raises(IntegrityError, match="đã được import từ nơi khác"):
        X.activate_vendored_lib(tmp_path, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)


def _forged_pyc(source: Path, code_text: str) -> bytes:
    stat = source.stat()
    header = MAGIC_NUMBER + struct.pack("<I", 0) + struct.pack("<I", int(stat.st_mtime) & 0xFFFFFFFF) + struct.pack("<I", stat.st_size & 0xFFFFFFFF)
    return header + marshal.dumps(compile(code_text, str(source), "exec"))


def _run(code: str, *, cwd: Path, prefix: Path | None = None) -> str:
    """Chạy Python con với `-B` (CHỈ cấm GHI pyc — không ghi cache vào repo; vẫn ĐỌC pyc nên đối chứng cache giả còn nguyên). `prefix` ⇒ `-X pycache_prefix=` mô phỏng prefix có sẵn của môi trường."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
    flags = ["-B"] + ([f"-Xpycache_prefix={prefix}"] if prefix is not None else [])
    done = subprocess.run([sys.executable, *flags, "-c", code], cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr[-800:]
    return done.stdout.strip()


def test_bytecode_policy_ignores_a_forged_in_tree_pyc_while_plain_python_would_load_it(tmp_path):
    """Chính sách bytecode có HIỆU LỰC thật: `fakemod.py` (đã hash) nói VALUE=1; `__pycache__/` chứa pyc GIẢ nói VALUE=2 với header khớp mtime/size của nguồn.
    Python thường nạp pyc giả (đối chứng); sau `activate_vendored_lib` thì không."""
    lib = tmp_path / "lib"
    lib.mkdir()
    pin = make_fake_wheel(lib, {"fakemod.py": b"VALUE = 1\n"})
    source = lib / "fakemod.py"
    pyc_dir = lib / "__pycache__"
    pyc_dir.mkdir()
    (pyc_dir / f"fakemod.{sys.implementation.cache_tag}.pyc").write_bytes(_forged_pyc(source, "VALUE = 2\n"))
    control = _run(f"import sys; sys.path.insert(0, {str(lib)!r}); import fakemod; print(fakemod.VALUE)", cwd=tmp_path)
    assert control == "2"                                                                    # mối nguy là thật: pyc giả không hash được nạp
    guarded = _run(f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n"
                   "from app.ml_serving import xgb_compat as X\n"
                   f"X.activate_vendored_lib({str(lib)!r}, 'fakelib', '1.0', expected_record_sha256={pin!r}, expected_wheel_tag={TAG!r})\n"
                   "import fakemod; print(fakemod.VALUE)", cwd=tmp_path)
    assert guarded == "1"


def test_a_forged_cache_under_an_EXISTING_pycache_prefix_is_not_loaded_either(tmp_path):
    """GPT 20 W1a: không nạp `__pycache__` trong cây nguồn KHÔNG đủ — Python còn đọc cache dưới `sys.pycache_prefix` có sẵn. Chính sách phải không tin prefix có sẵn.
    Đối chứng (không kích hoạt): prefix ngoài thư viện chứa pyc GIẢ (VALUE=2, header khớp nguồn) ⇒ Python nạp 2. Có kích hoạt ⇒ 1, prefix đã bị thay bằng cây chưa tồn tại của chính sách, không có `__pycache__` mới."""
    import importlib.util

    lib, prefix = tmp_path / "lib", tmp_path / "external-cache"
    lib.mkdir()
    prefix.mkdir()
    pin = make_fake_wheel(lib, {"fakemod.py": b"VALUE = 1\n"})
    source = lib / "fakemod.py"
    saved = sys.pycache_prefix
    sys.pycache_prefix = str(prefix)
    try:
        cache_path = Path(importlib.util.cache_from_source(str(source)))                      # đúng đường dẫn mà importlib sẽ đọc dưới prefix
    finally:
        sys.pycache_prefix = saved
    assert prefix in cache_path.parents
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(_forged_pyc(source, "VALUE = 2\n"))
    control = _run(f"import sys; sys.path.insert(0, {str(lib)!r}); import fakemod; print(fakemod.VALUE)", cwd=tmp_path, prefix=prefix)
    assert control == "2"                                                                    # mối nguy là thật
    guarded = _run(f"import os, sys; sys.path.insert(0, {str(BACKEND)!r})\n"
                   "from app.ml_serving import xgb_compat as X\n"
                   f"info = X.activate_vendored_lib({str(lib)!r}, 'fakelib', '1.0', expected_record_sha256={pin!r}, expected_wheel_tag={TAG!r})\n"
                   "import fakemod\n"
                   f"print(fakemod.VALUE, info['bytecodePolicy']['replacedExistingPrefix'], sys.pycache_prefix != {str(prefix)!r}, os.path.exists(sys.pycache_prefix), os.path.isdir({str(lib / '__pycache__')!r}))",
                   cwd=tmp_path, prefix=prefix)
    assert guarded == "1 True True False False"


def test_policy_replaces_any_existing_prefix_with_a_fresh_nonexistent_tree_it_owns(tmp_path, clean_sys, monkeypatch):
    existing = tmp_path / "somebody-elses-cache"
    existing.mkdir()
    monkeypatch.setattr(sys, "pycache_prefix", str(existing))
    monkeypatch.setattr(X.tempfile, "gettempdir", lambda: str(tmp_path))
    info = X.enforce_bytecode_policy()
    assert info["replacedExistingPrefix"] is True and sys.pycache_prefix != str(existing) and Path(sys.pycache_prefix).parent == tmp_path and not Path(sys.pycache_prefix).exists()
    assert sys.dont_write_bytecode is True
    first = sys.pycache_prefix
    assert X.enforce_bytecode_policy()["replacedExistingPrefix"] is False and sys.pycache_prefix == first          # bật nhiều lần: giữ cây của chính sách
    sys.pycache_prefix = str(existing)                                                                              # bị đổi đi nơi khác giữa chừng ⇒ đặt lại
    assert X.enforce_bytecode_policy()["replacedExistingPrefix"] is True and sys.pycache_prefix == first
    Path(first).mkdir()                                                                                              # cây của chính sách bỗng tồn tại (có thể chứa cache không kiểm) ⇒ từ chối
    with pytest.raises(IntegrityError, match="đã tồn tại"):
        X.enforce_bytecode_policy()


def test_policy_never_picks_a_predictable_or_pre_existing_prefix(tmp_path, clean_sys, monkeypatch):
    monkeypatch.setattr(X.tempfile, "gettempdir", lambda: str(tmp_path))
    names = iter(["a" * 32, "b" * 32, "c" * 32])
    monkeypatch.setattr(X.uuid, "uuid4", lambda: types.SimpleNamespace(hex=next(names)))
    for taken in ("a" * 32, "b" * 32):
        (tmp_path / f"serving-no-pyc-{taken}").mkdir()                                                               # hai tên đầu đã tồn tại (kẻ khác đặt trước)
    X.enforce_bytecode_policy()
    assert sys.pycache_prefix == str(tmp_path / f"serving-no-pyc-{'c' * 32}") and not Path(sys.pycache_prefix).exists()
    monkeypatch.setattr(X, "_POLICY_PREFIX", None)
    monkeypatch.setattr(X.uuid, "uuid4", lambda: types.SimpleNamespace(hex="d" * 32))
    (tmp_path / f"serving-no-pyc-{'d' * 32}").mkdir()
    with pytest.raises(IntegrityError, match="chưa tồn tại"):                                                        # mọi lần thử đều trùng ⇒ từ chối, không đoán
        X.enforce_bytecode_policy()


def _fake_module(name, file):
    module = types.ModuleType(name)
    if file is not None:
        module.__file__ = str(file)
    return module


def test_module_origin_uses_path_containment_not_a_string_prefix(tmp_path, clean_sys, monkeypatch):
    """GPT 20 W1b: `…\\lib-shadow\\…` có tiền tố chuỗi giống `…\\lib` nhưng KHÔNG nằm trong `lib`."""
    lib, shadow = tmp_path / "lib", tmp_path / "lib-shadow"
    lib.mkdir()
    (shadow / "fakelib").mkdir(parents=True)
    pin = make_fake_wheel(lib, BASE)
    state_before = (list(sys.path), sys.dont_write_bytecode, sys.pycache_prefix, X._POLICY_PREFIX)
    for origin, description in ((shadow / "fakelib" / "__init__.py", "thư mục anh em có chung tiền tố"), (lib / ".." / "lib-shadow" / "fakelib" / "__init__.py", "thoát bằng .."), (None, "thiếu __file__"),
                                (Path(__file__), "nơi khác hẳn")):
        monkeypatch.setitem(sys.modules, "fakelib", _fake_module("fakelib", origin))
        with pytest.raises(IntegrityError, match="đã được import từ nơi khác hoặc không rõ nguồn"):
            X.activate_vendored_lib(lib, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)
        assert (list(sys.path), sys.dont_write_bytecode, sys.pycache_prefix, X._POLICY_PREFIX) == state_before, description          # lỗi xảy ra TRƯỚC khi đổi policy/sys.path
    monkeypatch.setitem(sys.modules, "fakelib", _fake_module("fakelib", lib / "fakelib" / "__init__.py"))               # đúng trong root ⇒ chấp nhận
    info = X.activate_vendored_lib(lib, "fakelib", "1.0", expected_record_sha256=pin, expected_wheel_tag=TAG)
    assert info["filesVerified"] == 3 and sys.path[0] == str(lib.resolve())


# ---- context manager: không lồng, không đồng thời, luôn trả lại hàm gốc (module xgboost GIẢ — không cần xgboost, không fit) ----
@pytest.fixture
def fake_xgboost(monkeypatch):
    class FakeBooster:
        def __setstate__(self, state):
            self.state = state

    core = types.ModuleType("xgboost.core")
    core.Booster = FakeBooster
    package = types.ModuleType("xgboost")
    package.core = core
    monkeypatch.setitem(sys.modules, "xgboost", package)
    monkeypatch.setitem(sys.modules, "xgboost.core", core)
    return FakeBooster, FakeBooster.__setstate__


def test_context_patches_inside_and_restores_after_normal_and_failing_exit(fake_xgboost):
    booster, original = fake_xgboost
    with X.model_section_only_unpickle():
        assert booster.__setstate__ is not original
    assert booster.__setstate__ is original
    with pytest.raises(RuntimeError):
        with X.model_section_only_unpickle():
            raise RuntimeError("boom")
    assert booster.__setstate__ is original
    with X.model_section_only_unpickle():                                                   # khóa đã được trả ⇒ vào lại được
        pass


def test_nested_entry_is_refused_and_does_not_leave_the_patch(fake_xgboost):
    booster, original = fake_xgboost
    with X.model_section_only_unpickle():
        with pytest.raises(X.CompatError, match="lồng nhau hoặc chạy đồng thời"):
            with X.model_section_only_unpickle():
                pass
        assert booster.__setstate__ is not original                                         # khối ngoài vẫn đang hiệu lực
    assert booster.__setstate__ is original


def test_concurrent_entry_from_another_thread_is_refused_and_interleaving_cannot_leave_the_patch(fake_xgboost):
    booster, original = fake_xgboost
    inside, release, outcome = threading.Event(), threading.Event(), {}

    def holder():
        with X.model_section_only_unpickle():
            inside.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert inside.wait(10)
    try:
        with pytest.raises(X.CompatError, match="lồng nhau hoặc chạy đồng thời"):
            with X.model_section_only_unpickle():
                outcome["entered"] = True
    finally:
        release.set()
        thread.join(10)
    assert "entered" not in outcome and booster.__setstate__ is original                      # lịch xen kẽ A-vào, B-vào, A-ra, B-ra không còn có thể xảy ra
    with X.model_section_only_unpickle():
        pass
    assert booster.__setstate__ is original


# ---- buffer THẬT của champion (Linux/GPU) trên xgboost Windows — KHÔNG fit ----
def _real_bundle_ready():
    from app.ml_serving.registry import load_registry

    registry = load_registry()
    entry = registry.bundle_entry("cls-h1-xgb-06")
    return registry, entry, (LIBS / "xgboost-3.4.1.dist-info" / "RECORD").exists() and (WORKSPACE_ROOT / entry["path"]).exists()


@pytest.mark.skipif(not _real_bundle_ready()[2], reason="thiếu bundle phân loại/xgboost đóng gói cục bộ")
def test_real_champion_buffer_is_linux_format_rejected_by_windows_unserialize_and_loadable_model_only(monkeypatch):
    import joblib

    from app.ml_serving import classify

    registry, entry, _ = _real_bundle_ready()
    classify.activate_classification(registry)                                               # xác minh pin RECORD + hash mã TRƯỚC khi import
    from xgboost import core

    original = core.Booster.__setstate__
    captured: list[bytes] = []

    def capture(self, state):                                                                # nạp pickle thật nhưng chỉ GIỮ buffer, không dựng Booster
        captured.append(bytes(state["handle"]))
        self.handle = None

    monkeypatch.setattr(core.Booster, "__setstate__", capture)
    assert hashlib.sha256((WORKSPACE_ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    joblib.load(WORKSPACE_ROOT / entry["path"])
    monkeypatch.setattr(core.Booster, "__setstate__", original)
    (buffer,) = captured
    spans = X.split_sections(buffer)
    config = buffer[spans["Config"][0]:spans["Config"][1]]
    marker = key("rng_state") + b"S"
    at = config.index(marker) + len(marker)
    count = struct.unpack_from(">q", config, at + 1)[0]
    tokens = config[at + 9:at + 9 + count].decode().split()
    assert len(tokens) == 625 and all(t.isdigit() for t in tokens) and max(int(t, 16) for t in tokens) >= 2**32          # thập phân kiểu libstdc++; đọc như thập lục phân thì vượt 32 bit: nguyên nhân gốc, trên artifact thật
    if sys.platform == "win32":
        with pytest.raises(core.XGBoostError):
            core.Booster.__new__(core.Booster).__setstate__({"handle": bytearray(buffer)})
    with X.model_section_only_unpickle():
        restored = core.Booster.__new__(core.Booster)
        restored.__setstate__({"handle": bytearray(buffer)})
    assert core.Booster.__setstate__ is original
    assert restored.num_features() == 36 and restored.num_boosted_rounds() == 200            # 200 vòng × 3 lớp = 600 cây (n_estimators của champion)

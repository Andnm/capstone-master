"""Tương thích nạp `xgboost.Booster` đã pickle trên Linux/GPU (Colab) vào xgboost Windows (inference-only).

Vì sao cần: `Booster.__getstate__` serialize CẢ `Config` lẫn `Model`. Trong `Config.learner.generic_param.rng_state` libstdc++ (Linux) ghi trạng thái mt19937 dạng THẬP PHÂN còn
MSVC (Windows) đọc/ghi dạng THẬP LỤC PHÂN, nên `XGBoosterUnserializeFromBuffer` trên Windows báo `input stream corrupted` dù phần `Model` hoàn toàn hợp lệ (đã đo: cây, `base_score`,
`num_feature` đều nạp được bằng `load_model`). `rng_state` chỉ dùng khi HUẤN LUYỆN (lấy mẫu hàng/cột), không ảnh hưởng suy luận.

Giải pháp hẹp, kiểm chứng được (KHÔNG sửa dữ liệu bundle, KHÔNG đoán/chuyển đổi RNG):
  * tách đúng khối `Model` khỏi buffer bằng trình đọc UBJSON có cấu trúc (buffer phải được tiêu thụ hết, mỗi khóa cấp cao đúng một lần);
  * nạp riêng khối đó bằng `Booster.load_model` (định dạng liên nền tảng chính thức);
  * chỉ áp dụng bên trong context manager, trả `__setstate__` về nguyên trạng khi thoát (kể cả khi lỗi); context manager KHÔNG tái nhập và KHÔNG chạy đồng thời — vào lần thứ hai (lồng hoặc từ thread khác) ⇒ `CompatError`,
    vì patch là trạng thái toàn tiến trình (hai context xen kẽ sẽ để lại hàm patch). Loader này chỉ dành cho quy trình OFFLINE TUẦN TỰ (builder); API JSON-only không bao giờ import module này;
  * độ đúng được chứng minh bằng full replay so với xác suất đã lưu từ lúc huấn luyện (xem `classify.replay_classifier`) cho ĐÚNG các bundle đã ghim/môi trường đã đo — không phải bảo đảm di động của pickle xgboost nói chung.

Thư viện đóng gói sẵn (`activate_vendored_lib`) được ghim bằng sha256 của RECORD TRONG REGISTRY đã review, so TRƯỚC khi đụng `sys.path`; từ RECORD đã ghim kiểm kích thước + sha256 từng file, và mọi file trên đĩa phải nằm trong inventory
(ngoài `RECORD` và bytecode trong `__pycache__`). Bytecode không có hash ⇒ chính sách RÕ: không bao giờ được nạp (luôn chuyển `sys.pycache_prefix` sang cây cache MỚI do chính sách sở hữu, chưa tồn tại — không tin prefix có sẵn — + `sys.dont_write_bytecode`), nên `.pyc` trong thư mục thư viện bị bỏ qua, không phải "đã hash".
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import re
import struct
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

from .errors import IntegrityError, ServingError

_INT_FORMATS = {b"i": ">b", b"U": ">B", b"I": ">h", b"l": ">i", b"L": ">q"}
_FLOAT_FORMATS = {b"d": ">f", b"D": ">d"}


class CompatError(ServingError):
    """Buffer Booster không đúng cấu trúc UBJSON mong đợi (Config + Model), hoặc thư viện đóng gói sai hash."""
    code = "xgb_compat"


class _Reader:
    """Trình duyệt UBJSON chỉ để ĐỊNH VỊ ranh giới giá trị (không giải mã số liệu cây). Mọi marker lạ/đọc quá cuối buffer ⇒ CompatError."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _need(self, n: int) -> None:
        if n < 0 or self.pos + n > len(self.data):
            raise CompatError("buffer UBJSON bị cắt/đọc quá cuối")

    def _byte(self) -> bytes:
        self._need(1)
        value = self.data[self.pos:self.pos + 1]
        self.pos += 1
        return value

    def _peek(self) -> bytes:
        return self.data[self.pos:self.pos + 1]

    def _int(self, marker: bytes) -> int:
        fmt = _INT_FORMATS.get(marker)
        if fmt is None:
            raise CompatError(f"marker số nguyên lạ: {marker!r}")
        size = struct.calcsize(fmt)
        self._need(size)
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return int(value)

    def _length(self) -> int:
        value = self._int(self._byte())
        if value < 0:
            raise CompatError("độ dài âm")
        return value

    def key(self) -> str:
        n = self._length()
        self._need(n)
        text = self.data[self.pos:self.pos + n].decode("utf-8", "strict")
        self.pos += n
        return text

    def skip_value(self, marker: bytes | None = None) -> None:
        marker = self._byte() if marker is None else marker
        if marker == b"{":
            self._skip_container(b"}", keyed=True)
        elif marker == b"[":
            self._skip_container(b"]", keyed=False)
        elif marker == b"S":
            n = self._length()
            self._need(n)
            self.pos += n
        elif marker in _INT_FORMATS:
            self._int(marker)
        elif marker in _FLOAT_FORMATS:
            size = struct.calcsize(_FLOAT_FORMATS[marker])
            self._need(size)
            self.pos += size
        elif marker in (b"T", b"F", b"Z"):
            return
        else:
            raise CompatError(f"marker UBJSON lạ: {marker!r} tại {self.pos - 1}")

    def _skip_container(self, closer: bytes, *, keyed: bool) -> None:
        element_type: bytes | None = None
        if self._peek() == b"$":
            self.pos += 1
            element_type = self._byte()
        if self._peek() == b"#":
            self.pos += 1
            count = self._length()
            if element_type in _FLOAT_FORMATS or element_type in _INT_FORMATS:
                width = struct.calcsize((_FLOAT_FORMATS | _INT_FORMATS)[element_type])
                self._need(width * count)
                self.pos += width * count
                return
            for _ in range(count):
                if keyed:
                    self.key()
                self.skip_value(element_type)
            return
        if element_type is not None:
            raise CompatError("container có kiểu nhưng thiếu số phần tử")
        while self._peek() != closer:
            if not self._peek():
                raise CompatError("container không đóng")
            if keyed:
                self.key()
            self.skip_value()
        self.pos += 1


def split_sections(buffer: bytes | bytearray) -> dict[str, tuple[int, int]]:
    """Khoảng byte [start, end) của từng khóa cấp cao trong buffer serialize của Booster. Buffer phải là đúng MỘT object, được tiêu thụ hết, khóa không trùng."""
    data = bytes(buffer)
    reader = _Reader(data)
    if reader._byte() != b"{":
        raise CompatError("buffer không bắt đầu bằng object UBJSON")
    spans: dict[str, tuple[int, int]] = {}
    while reader._peek() != b"}":
        if not reader._peek():
            raise CompatError("object cấp cao không đóng")
        name = reader.key()
        start = reader.pos
        reader.skip_value()
        if name in spans:
            raise CompatError(f"khóa cấp cao trùng: {name!r}")
        spans[name] = (start, reader.pos)
    reader.pos += 1
    if reader.pos != len(data):
        raise CompatError(f"còn {len(data) - reader.pos} byte thừa sau object cấp cao")
    return spans


def extract_model_section(buffer: bytes | bytearray) -> bytes:
    spans = split_sections(buffer)
    if set(spans) != {"Config", "Model"}:
        raise CompatError(f"khóa cấp cao khác {{Config, Model}}: {sorted(spans)}")
    start, end = spans["Model"]
    return bytes(buffer)[start:end]


_PATCH_LOCK = threading.Lock()


@contextlib.contextmanager
def model_section_only_unpickle() -> Iterator[None]:
    """Trong khối `with`: `Booster.__setstate__` chỉ nạp khối `Model` của buffer (xem docstring module). Thoát khối ⇒ trả lại hàm gốc dù có lỗi.
    Không tái nhập, không đồng thời: lần vào thứ hai khi khối đầu chưa thoát (lồng hoặc thread khác) ⇒ `CompatError` (patch là trạng thái toàn tiến trình)."""
    if not _PATCH_LOCK.acquire(blocking=False):
        raise CompatError("model_section_only_unpickle không hỗ trợ lồng nhau hoặc chạy đồng thời (patch toàn tiến trình); chỉ dùng trong quy trình offline tuần tự")
    try:
        from xgboost import core                                 # noqa: PLC0415 - chỉ import SAU khi caller đã xác minh thư viện (verify_vendored_wheel)

        original = core.Booster.__setstate__

        def patched(self: Any, state: dict) -> None:
            buffer = state.get("handle")
            if buffer is None:
                return original(self, state)
            model = extract_model_section(buffer)
            holder = core.Booster()
            holder.load_model(bytearray(model))
            new_state = dict(state)
            new_state["handle"] = holder.handle
            holder.handle = None                                  # handle chuyển cho đối tượng đang dựng; holder.__del__ không được giải phóng nó
            self.__dict__.update(new_state)

        core.Booster.__setstate__ = patched
        try:
            yield
        finally:
            core.Booster.__setstate__ = original
    finally:
        _PATCH_LOCK.release()


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _record_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")


def _is_bytecode(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 2 and parts[-2] == "__pycache__" and parts[-1].endswith(".pyc")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def verify_vendored_wheel(libs_dir: Path, distribution: str, version: str, *, expected_record_sha256: str, expected_wheel_tag: str) -> dict[str, Any]:
    """Xác minh thư viện đóng gói sẵn theo thứ tự fail-closed (không import, không đụng sys.path):
      1. `expected_record_sha256` (từ registry đã review) đúng định dạng và == sha256(RECORD đang nằm trong thư mục) — chốt danh tính, không tin RECORD tự khai;
      2. thẻ wheel (`WHEEL: Tag`) == `expected_wheel_tag`;
      3. từ RECORD đã ghim: tên không tuyệt đối/không `..`, không symlink/junction, kích thước + sha256 từng file khớp; mục không hash chỉ được là RECORD và bytecode trong `__pycache__`;
      4. INVENTORY: mọi file trên đĩa phải có trong RECORD (có hash) — trừ RECORD và `__pycache__/*.pyc` (bytecode bị bỏ qua theo chính sách, xem docstring module). File lạ (kể cả .py/.pyd/.dll/.pth) ⇒ lỗi."""
    if not isinstance(expected_record_sha256, str) or not HEX64.match(expected_record_sha256):
        raise IntegrityError(f"thiếu/sai định dạng sha256 RECORD đã ghim cho {distribution}=={version}")
    root = Path(libs_dir).resolve()
    dist_info = root / f"{distribution}-{version}.dist-info"
    record = dist_info / "RECORD"
    if not record.is_file() or _is_link(record) or _is_link(dist_info) or _is_link(root):
        raise IntegrityError(f"thiếu RECORD của {distribution}=={version} tại {libs_dir}")
    record_bytes = record.read_bytes()
    record_sha = hashlib.sha256(record_bytes).hexdigest()
    if record_sha != expected_record_sha256:
        raise IntegrityError(f"RECORD của {distribution}=={version} khác bản đã ghim trong registry ({record_sha[:16]}… != {expected_record_sha256[:16]}…)")
    wheel = dist_info / "WHEEL"
    tag = next((ln.split(":", 1)[1].strip() for ln in wheel.read_text(encoding="utf-8").splitlines() if ln.startswith("Tag:")), None) if wheel.is_file() else None
    if tag != expected_wheel_tag:
        raise IntegrityError(f"thẻ wheel {tag!r} != đã ghim {expected_wheel_tag!r}")
    record_rel = f"{distribution}-{version}.dist-info/RECORD"
    hashed: dict[str, tuple[str, int]] = {}
    ignored_bytecode = 0
    for line in record_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.rsplit(",", 2)
        if len(parts) != 3:
            raise IntegrityError(f"dòng RECORD sai định dạng: {line!r}")
        name, algo_hash, size_text = parts
        if name.startswith("/") or "\\" in name or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
            raise IntegrityError(f"RECORD trỏ ra ngoài thư viện: {name!r}")
        if name == record_rel:
            continue
        if _is_bytecode(name):
            ignored_bytecode += 1
            continue
        algo, _, digest = algo_hash.partition("=")
        if algo != "sha256" or not digest or not size_text.isdigit():
            raise IntegrityError(f"mục RECORD không có sha256/kích thước hợp lệ: {name!r}")
        if name in hashed:
            raise IntegrityError(f"RECORD trùng mục: {name!r}")
        hashed[name] = (digest, int(size_text))
    if not hashed:
        raise IntegrityError("RECORD không có file nào để kiểm")
    for name, (digest, size) in hashed.items():
        target = root / name
        if not target.is_file() or _is_link(target) or root not in target.resolve().parents:
            raise IntegrityError(f"thiếu file thư viện: {name!r}")
        if target.stat().st_size != size:
            raise IntegrityError(f"kích thước file thư viện không khớp RECORD: {name!r}")
        if _record_digest(target) != digest:
            raise IntegrityError(f"sha256 file thư viện không khớp RECORD: {name!r}")
    unlisted: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for entry in list(dirnames):
            if _is_link(Path(dirpath) / entry):
                raise IntegrityError(f"symlink/junction trong thư viện: {(Path(dirpath) / entry).relative_to(root).as_posix()!r}")
        for filename in filenames:
            path = Path(dirpath) / filename
            rel = path.relative_to(root).as_posix()
            if _is_link(path):
                raise IntegrityError(f"symlink trong thư viện: {rel!r}")
            if rel == record_rel or rel in hashed or _is_bytecode(rel):
                continue
            unlisted.append(rel)
    if unlisted:
        raise IntegrityError(f"file ngoài inventory RECORD ({len(unlisted)}): {sorted(unlisted)[:5]}")
    return {"distribution": distribution, "version": version, "filesVerified": len(hashed), "bytecodeIgnored": ignored_bytecode, "wheelTag": tag, "recordSha256": record_sha}


_POLICY_PREFIX: str | None = None


def _fresh_prefix() -> str:
    """Cây cache MỚI, tên không đoán được, và CHƯA TỒN TẠI (kiểm tường minh; thử lại tối đa 5 lần rồi từ chối)."""
    for _ in range(5):
        candidate = os.path.join(tempfile.gettempdir(), f"serving-no-pyc-{uuid.uuid4().hex}")
        if not os.path.exists(candidate):
            return candidate
    raise IntegrityError("không chọn được cây cache bytecode chưa tồn tại")


def enforce_bytecode_policy() -> dict[str, Any]:
    """Bytecode không có hash ⇒ KHÔNG BAO GIỜ được nạp, bất kể nguồn nào: nó có thể nằm trong `__pycache__` của cây nguồn HOẶC dưới một `sys.pycache_prefix` đã có sẵn (do môi trường/`PYTHONPYCACHEPREFIX`).
    Vì vậy KHÔNG tin prefix có sẵn: luôn chuyển sang cây cache do CHÍNH policy sở hữu — tên ngẫu nhiên, chưa tồn tại, không ai ghi vào (`dont_write_bytecode`) — nên mọi import sau đó compile từ nguồn.
    Gọi nhiều lần: giữ cây của policy; nếu cây đó đã bị tạo ra (có thể chứa cache không kiểm) ⇒ từ chối; nếu `sys.pycache_prefix` bị đổi đi nơi khác ⇒ đặt lại. Hiệu lực toàn tiến trình (builder offline).
    Không phải sandbox chống kẻ đã chiếm tiến trình; chỉ bảo đảm cache không kiểm hash không vòng được qua kiểm nguồn."""
    global _POLICY_PREFIX
    previous = sys.pycache_prefix
    sys.dont_write_bytecode = True
    if _POLICY_PREFIX is not None and os.path.exists(_POLICY_PREFIX):
        raise IntegrityError(f"cây cache của chính sách bytecode đã tồn tại (có thể chứa cache không kiểm hash): {_POLICY_PREFIX}")
    if _POLICY_PREFIX is None:
        _POLICY_PREFIX = _fresh_prefix()
    sys.pycache_prefix = _POLICY_PREFIX
    return {"dontWriteBytecode": True, "pycachePrefixSet": True, "policyOwnedPrefix": True, "replacedExistingPrefix": previous is not None and previous != _POLICY_PREFIX}


_ACTIVATED_LIBS: set[str] = set()


def activate_vendored_lib(libs_dir: Path, distribution: str, version: str, *, expected_record_sha256: str, expected_wheel_tag: str) -> dict[str, Any]:
    """Xác minh (pin RECORD → thẻ → hash/kích thước → inventory) rồi mới đặt chính sách bytecode và đưa thư mục vào ĐẦU sys.path (một lần). Mọi bước trước đó fail ⇒ KHÔNG đổi sys.path/sys.modules.
    Nếu `distribution` đã được import từ nơi khác ⇒ từ chối (không chạy hai bản)."""
    info = verify_vendored_wheel(libs_dir, distribution, version, expected_record_sha256=expected_record_sha256, expected_wheel_tag=expected_wheel_tag)
    resolved = str(Path(libs_dir).resolve())
    loaded = sys.modules.get(distribution)
    if loaded is not None:
        origin = getattr(loaded, "__file__", None)
        # containment theo ĐƯỜNG DẪN sau resolve (không phải tiền tố chuỗi: `lib-shadow` không nằm trong `lib`); thiếu `__file__` ⇒ không chứng minh được nguồn ⇒ từ chối
        if not origin or not Path(origin).resolve().is_relative_to(Path(resolved)):
            raise IntegrityError(f"{distribution} đã được import từ nơi khác hoặc không rõ nguồn: {origin}")
    info["bytecodePolicy"] = enforce_bytecode_policy()
    if resolved not in _ACTIVATED_LIBS and resolved not in sys.path:
        sys.path.insert(0, resolved)
        _ACTIVATED_LIBS.add(resolved)
    return info

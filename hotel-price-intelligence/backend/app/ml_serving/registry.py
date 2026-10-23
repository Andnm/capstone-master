"""Registry + nạp bundle FAIL-CLOSED (inference-only; không `.fit`, không DB, không import `app.core`/`app.scraper`/`app.warehouse`/`app.database`).

`registry.json` (git-tracked) ghim: đường dẫn bundle (tương đối workspace, chỉ trong vùng cho phép), sha256, danh tính (tên/horizon/matrix/target/claim/feature),
môi trường đã train và môi trường ĐÃ KIỂM (`validatedRuntime`), danh sách mã phụ thuộc (sha256 từng file) và `nullableFeatures`.

Thứ tự nạp (mỗi bước fail ⇒ dừng, KHÔNG có bước nào bị bỏ qua):
  1. đường dẫn nằm trong vùng cho phép (resolve + containment; không tuyệt đối, không `..`, không symlink thoát);
  2. sha256 file bundle == registry — TRƯỚC khi `joblib.load` (pickle = thực thi mã);
  3. sha256 mọi mã phụ thuộc (`codeDependencies`) == registry;
  4. runtime hiện tại == `validatedRuntime` (python so major.minor; sklearn/numpy/pandas/joblib so chính xác);
     nếu `trainedEnvironment` khác `validatedRuntime` thì bắt buộc `compatibility.status == dev_compatibility_empirically_checked` có `evidence`;
  5. `loader(path)` (mặc định `joblib.load`, import lười SAU các bước trên);
  6. danh tính bundle khớp registry.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import IdentityError, IntegrityError, RegistryError, RuntimeMismatchError

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]          # D:\MSE\CAPSTONE (cố định theo vị trí file; KHÔNG đọc từ env/request)
REGISTRY_PATH = Path(__file__).with_name("registry.json")
SCHEMA = "ml-serving-registry-1"
COMPAT_STATUS = "dev_compatibility_empirically_checked"
RUNTIME_PACKAGES: tuple[str, ...] = ("scikit-learn", "numpy", "pandas", "joblib")
ROLES = ("regression_primary", "classification_reference")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def allowed_roots(workspace_root: Path) -> tuple[Path, ...]:
    """Chỉ ba vùng được đọc: mã/bundle dưới `hotel-price-intelligence/ml`, dataset dưới `outputs/datasets` và thư viện đóng gói sẵn (hash theo RECORD) dưới `hotel-price-intelligence/data/serving/libs`."""
    return ((workspace_root / "hotel-price-intelligence" / "ml").resolve(), (workspace_root / "outputs" / "datasets").resolve(),
            (workspace_root / "hotel-price-intelligence" / "data" / "serving" / "libs").resolve())


def resolve_contained(workspace_root: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Giải đường dẫn tương đối workspace; từ chối tuyệt đối, `..`, và mọi đích (sau resolve, kể cả symlink) nằm ngoài vùng cho phép."""
    if not isinstance(relative, str) or not relative:
        raise IntegrityError("đường dẫn rỗng")
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise IntegrityError(f"đường dẫn không được tuyệt đối/chứa '..': {relative!r}")
    resolved = (workspace_root / candidate).resolve()
    if not any(resolved == root or root in resolved.parents for root in allowed_roots(workspace_root)):
        raise IntegrityError(f"đường dẫn nằm ngoài vùng cho phép: {relative!r}")
    if must_exist and not resolved.exists():
        raise IntegrityError(f"không tồn tại: {relative!r}")
    return resolved


def extra_packages(*mappings: Mapping[str, Any]) -> tuple[str, ...]:
    """Gói ngoài `RUNTIME_PACKAGES` mà một mục registry khai báo (vd. `xgboost` của bundle phân loại), theo thứ tự chữ cái."""
    return tuple(sorted({k for m in mappings for k in m if k != "python" and k not in RUNTIME_PACKAGES}))


def current_runtime(extra: Sequence[str] = ()) -> dict[str, str | None]:
    runtime: dict[str, str | None] = {"python": platform.python_version()}
    for package in (*RUNTIME_PACKAGES, *extra):
        try:
            runtime[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            runtime[package] = None
    return runtime


def _minor(version: str) -> str:
    return ".".join(str(version).split(".")[:2])


def runtime_drift(validated: Mapping[str, str], actual: Mapping[str, str | None]) -> list[str]:
    """Danh sách lệch giữa `validated` và `actual` (rỗng = khớp). Python so major.minor, còn lại chính xác. Thiếu package trong actual là lệch."""
    drift: list[str] = []
    if _minor(validated.get("python", "")) != _minor(actual.get("python") or ""):
        drift.append(f"python: validated {validated.get('python')} != actual {actual.get('python')}")
    for package in (*RUNTIME_PACKAGES, *extra_packages(validated)):
        if package not in validated:
            drift.append(f"{package}: thiếu trong validatedRuntime")
        elif validated[package] != actual.get(package):
            drift.append(f"{package}: validated {validated[package]} != actual {actual.get(package)}")
    return drift


def check_runtime(validated: Mapping[str, str], actual: Mapping[str, str | None] | None = None) -> dict[str, str | None]:
    actual = dict(actual) if actual is not None else current_runtime(extra_packages(validated))
    drift = runtime_drift(validated, actual)
    if drift:
        raise RuntimeMismatchError("runtime ngoài validatedRuntime (không tự cấp tương thích): " + "; ".join(drift))
    return actual


def trained_vs_validated_drift(trained: Mapping[str, str], validated: Mapping[str, str]) -> list[str]:
    drift: list[str] = []
    if _minor(trained.get("python", "")) != _minor(validated.get("python", "")):
        drift.append(f"python {trained.get('python')} -> {validated.get('python')}")
    for package in (*RUNTIME_PACKAGES, *(p for p in extra_packages(validated) if p in trained)):
        if trained.get(package) != validated.get(package):
            drift.append(f"{package} {trained.get(package)} -> {validated.get(package)}")
    return drift


@dataclass(frozen=True)
class Registry:
    raw: dict[str, Any]
    workspace_root: Path

    @property
    def nullable_features(self) -> frozenset[str]:
        return frozenset(self.raw["nullableFeatures"])

    @property
    def code_dependencies(self) -> list[dict[str, str]]:
        return list(self.raw["codeDependencies"])

    @property
    def classification_code_dependencies(self) -> list[dict[str, str]]:
        return list(self.raw.get("classificationCodeDependencies", []))

    @property
    def extra_libs(self) -> list[dict[str, str]]:
        return list(self.raw.get("extraLibs", []))

    def bundle_entry(self, bundle_id: str) -> dict[str, Any]:
        for entry in self.raw["bundles"]:
            if entry["id"] == bundle_id:
                return entry
        raise RegistryError(f"bundle không có trong registry: {bundle_id!r}")

    def entries(self, role: str, horizon: int | None = None) -> list[dict[str, Any]]:
        return [e for e in self.raw["bundles"] if e["role"] == role and (horizon is None or e["horizon"] == horizon)]


def _require(mapping: Mapping[str, Any], keys: Sequence[str], where: str) -> None:
    missing = [k for k in keys if k not in mapping]
    if missing:
        raise RegistryError(f"{where}: thiếu khóa {missing}")


def load_registry(path: Path = REGISTRY_PATH, workspace_root: Path = WORKSPACE_ROOT) -> Registry:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"không đọc được registry: {exc}") from exc
    if raw.get("schema") != SCHEMA:
        raise RegistryError(f"schema registry lạ: {raw.get('schema')!r}")
    _require(raw, ("codeDependencies", "nullableFeatures", "bundles"), "registry")
    for dep in (*raw["codeDependencies"], *raw.get("classificationCodeDependencies", [])):
        _require(dep, ("path", "sha256"), "codeDependencies[]")
        if not HEX64.match(dep["sha256"]):
            raise RegistryError(f"sha256 mã phụ thuộc sai định dạng: {dep['path']}")
    for lib in raw.get("extraLibs", []):
        _require(lib, ("distribution", "version", "path", "recordSha256", "wheelTag"), "extraLibs[]")
        if not HEX64.match(str(lib["recordSha256"])) or not lib["wheelTag"]:
            raise RegistryError(f"extraLibs[{lib['distribution']}]: recordSha256 phải là sha256 hex 64 ký tự và wheelTag không rỗng (pin do người review đặt, không tự điền)")
    seen: set[str] = set()
    for entry in raw["bundles"]:
        _require(entry, ("id", "role", "horizon", "path", "sha256", "datasetVersion", "modelName", "claimLevel", "trainedEnvironment", "validatedRuntime", "compatibility"), "bundles[]")
        if entry["id"] in seen:
            raise RegistryError(f"bundle id trùng: {entry['id']}")
        seen.add(entry["id"])
        if entry["role"] not in ROLES:
            raise RegistryError(f"role lạ: {entry['role']!r}")
        if not HEX64.match(entry["sha256"]):
            raise RegistryError(f"sha256 bundle sai định dạng: {entry['id']}")
    return Registry(raw=raw, workspace_root=Path(workspace_root).resolve())


def verify_code_dependencies(registry: Registry, dependencies: Sequence[Mapping[str, str]] | None = None) -> None:
    """Hash từng file mã phụ thuộc (đường dẫn trong vùng cho phép). Sai ⇒ IntegrityError TRƯỚC khi import mã đó. Mặc định kiểm mã hồi quy."""
    for dep in (registry.code_dependencies if dependencies is None else dependencies):
        path = resolve_contained(registry.workspace_root, dep["path"])
        if sha256_file(path) != dep["sha256"]:
            raise IntegrityError(f"mã phụ thuộc đổi so với registry: {dep['path']}")


_ACTIVATED: set[str] = set()


def activate_code_paths(registry: Registry) -> None:
    """Xác minh hash mã rồi mới thêm `ml/regression`, `ml/shared` vào sys.path (một lần). Thư mục cố định, không lấy từ env/request."""
    verify_code_dependencies(registry)
    for rel in ("hotel-price-intelligence/ml/regression", "hotel-price-intelligence/ml/shared"):
        path = str(resolve_contained(registry.workspace_root, rel))
        if path not in sys.path and path not in _ACTIVATED:
            sys.path.insert(0, path)
            _ACTIVATED.add(path)


def check_bundle_policy(entry: Mapping[str, Any]) -> list[str]:
    """Quy tắc tương thích: trainedEnvironment ≠ validatedRuntime chỉ được khi có compatibility hợp lệ (có evidence). Trả danh sách drift đã khai báo."""
    drift = trained_vs_validated_drift(entry["trainedEnvironment"], entry["validatedRuntime"])
    if drift:
        compat = entry.get("compatibility") or {}
        if compat.get("status") != COMPAT_STATUS or not compat.get("evidence"):
            raise RuntimeMismatchError(f"bundle {entry['id']}: trainedEnvironment khác validatedRuntime ({'; '.join(drift)}) nhưng thiếu compatibility hợp lệ")
    return drift


def default_loader() -> Callable[[Path], Any]:
    """Trình nạp mặc định (joblib.load) — chỉ trả về SAU khi caller đã kiểm hash/runtime. Duy nhất nơi trong gói ml_serving nhắc tới joblib."""
    import joblib

    return joblib.load


def load_bundle(registry: Registry, bundle_id: str, *, loader: Callable[[Path], Any] | None = None,
                runtime: Mapping[str, str | None] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Nạp 1 bundle theo thứ tự fail-closed (xem docstring module). Trả (bundle_dict, thông tin nạp: sha, runtime, drift đã khai báo, warnings)."""
    entry = registry.bundle_entry(bundle_id)
    if entry["role"] != "regression_primary":
        raise RegistryError(f"load_bundle chỉ nạp bundle hồi quy; bundle phân loại dùng classify.load_classification_bundle: {bundle_id!r}")
    path = resolve_contained(registry.workspace_root, entry["path"])
    actual_sha = sha256_file(path)
    if actual_sha != entry["sha256"]:
        raise IntegrityError(f"sha256 bundle {bundle_id} không khớp registry (file đổi?)")
    verify_code_dependencies(registry)
    actual_runtime = check_runtime(entry["validatedRuntime"], runtime)
    drift = check_bundle_policy(entry)
    if loader is None:
        loader = default_loader()                                  # import lười joblib: chỉ SAU khi hash + runtime đã qua
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bundle = loader(path)
    verify_identity(entry, bundle)
    info = {"bundleId": bundle_id, "sha256": actual_sha, "runtime": actual_runtime, "declaredDrift": drift,
            "loadWarnings": sorted({f"{w.category.__name__}" for w in caught})}
    return bundle, info


def verify_identity(entry: Mapping[str, Any], bundle: Mapping[str, Any]) -> None:
    """Danh tính nội dung bundle == registry. Thiếu khóa/lệch ⇒ IdentityError."""
    checks = [("name", "modelName"), ("horizon", "horizon"), ("matrix", "matrix"), ("target_transform", "targetTransform"), ("claim_level", "claimLevel"),
              ("n_features_in", "nFeaturesIn"), ("feature_list_sha256", "featureListSha256"), ("effective_feature_list_sha256", "effectiveFeatureListSha256")]
    for bundle_key, entry_key in checks:
        if entry_key not in entry:
            raise IdentityError(f"registry thiếu {entry_key} cho {entry.get('id')}")
        if bundle.get(bundle_key) != entry[entry_key]:
            raise IdentityError(f"{entry['id']}: {bundle_key}={bundle.get(bundle_key)!r} != registry {entry[entry_key]!r}")
    if "model" not in bundle or "features" not in bundle or "routing_policy" not in bundle:
        raise IdentityError(f"{entry['id']}: bundle thiếu model/features/routing_policy")

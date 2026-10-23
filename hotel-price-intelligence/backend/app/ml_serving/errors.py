"""Lỗi của lớp serving (inference-only). Mọi lỗi là fail-closed: builder dừng, không ghi snapshot, không phục vụ model chưa qua kiểm."""
from __future__ import annotations


class ServingError(Exception):
    code = "serving_error"


class RegistryError(ServingError):
    """registry.json thiếu/sai cấu trúc, hoặc mục không có trong registry."""
    code = "registry_error"


class IntegrityError(ServingError):
    """SHA256 file (bundle, mã phụ thuộc, dự đoán đã lưu, samples) không khớp registry/checksum; hoặc đường dẫn thoát vùng cho phép."""
    code = "integrity_error"


class RuntimeMismatchError(ServingError):
    """Môi trường chạy không nằm trong `validatedRuntime` đã đo."""
    code = "runtime_mismatch"


class IdentityError(ServingError):
    """Nội dung bundle (tên, horizon, matrix, transform, feature) không khớp danh tính trong registry."""
    code = "bundle_identity"


class InputContractError(ServingError):
    """Đầu vào inference vi phạm hợp đồng (category lạ, NaN ở feature bắt buộc, giá <= 0, thiếu cột, mode lạ)."""
    code = "input_contract"


class ReplayError(ServingError):
    """Dự đoán lại từ bundle không khớp dự đoán đã lưu trong tolerance, hoặc thiếu/thừa hàng."""
    code = "replay"


class SnapshotError(ServingError):
    """Snapshot không phát hành được (đầu vào hai dataset lệch, ghi đè snapshotId có sẵn, hash không khớp)."""
    code = "snapshot"

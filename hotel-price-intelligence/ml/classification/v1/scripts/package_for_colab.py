"""Đóng gói MỘT thư mục giao hàng tự đủ để chạy trên Colab (người dùng upload lên Drive và chạy notebook). KHÔNG fit, KHÔNG build dataset, chỉ ĐỌC dataset/snapshot bất biến.

  python package_for_colab.py --delivery 20261019_d1 [--datasets-root ...] [--snapshot-dir ...] [--out-root .../classification/colab/v1]

Đầu ra `<out-root>/<delivery>/` (không ghi đè; đã tồn tại ⇒ lỗi; tên `delivery` phải là basename an toàn, kiểm TRƯỚC preflight và trước khi tạo gì):
  cls_v1_colab.ipynb        notebook mỏng (chỉ gọi CLI; code thật nằm trong module .py); ID ô xác định
  HANDOFF.md                hướng dẫn cho người dùng + cảnh báo trung thực
  PACKAGE_MANIFEST.json     sha256/bytes từng file upload + danh tính (runtime code, config, upstream, pin dataset/snapshot, môi trường ghim) + digest notebook/HANDOFF
  SHA256SUMS.txt            digest ngoài của MỌI file giao (trừ chính nó) để đối chiếu bàn giao
  upload/cls_v1_code.zip    cây v1 (py/yaml/json KHÔNG tests) + requirements-colab.txt  -> /content/cls_v1
  upload/datasets.zip       dataset nf1 + nf3 bất biến (nguyên thư mục)                 -> /content/datasets
  upload/baseline_snapshot.zip   snapshot baseline regression (4 file)                  -> /content/snapshot
Tập file code trong zip == tập file mà `code_manifest(exclude_tests=True, suffixes=(.py,.yaml,.json))` liệt kê ⇒ `runtime_code_sha256` trên Colab bằng máy chính (notebook kiểm).
Trước khi ghi: drift upstream (packaged + dev), vân tay ứng viên, pin dataset (14 entry), pin snapshot; sau khi ghi: kiểm lại từng zip từ đầu (giải nén sạch + danh tính + đủ entry + không tests/pycache/.env).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

V1_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1_DIR))

from cls_training import upstream_sync  # noqa: E402
from cls_training.candidates import verify_candidate_set  # noqa: E402
from cls_training.codeid import code_manifest  # noqa: E402
from cls_training.config import dataset_pin, load_config, snapshot_pin  # noqa: E402
from cls_training.data import verify_all_entries  # noqa: E402
from cls_training.snapshot import snapshot_paths  # noqa: E402
from cls_training.transaction import canonical_bytes, checked_root, child_path, sha256_file  # noqa: E402

ML_DIR = V1_DIR.parents[1]
WORKSPACE = ML_DIR.parents[1]
SCHEMA = "cls-package-1"
ZIP_TIME = (2026, 10, 19, 0, 0, 0)
RUNTIME_SUFFIXES = (".py", ".yaml", ".json")
PACKAGE_FILES = ("upload/cls_v1_code.zip", "upload/datasets.zip", "upload/baseline_snapshot.zip")
DELIVERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_NAMES = (".env", ".pyc", "__pycache__", ".pytest_cache", ".git")


class PackageError(RuntimeError):
    pass


def safe_delivery_name(name: str) -> str:
    if not isinstance(name, str) or not DELIVERY_RE.match(name) or ".." in name:
        raise PackageError(f"tên giao hàng không an toàn: {name!r} (chỉ [A-Za-z0-9_.-], bắt đầu bằng chữ/số, ≤64 ký tự, không '..', không phân tách đường dẫn/ổ đĩa)")
    return name


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_zip(path: Path, entries: dict[str, bytes]) -> None:
    """Zip xác định: thứ tự tên sắp xếp, thời gian cố định, quyền cố định, deflate. Cùng nội dung ⇒ cùng bytes."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, entries[name])


def requirements_text(cfg: dict[str, Any]) -> str:
    pins = cfg["environment"]["expected_versions"]
    lines = ["# Phiên bản GHIM (fail-closed: lệch ⇒ train_cls.py dừng). Lấy từ configs/train.yaml:environment.expected_versions.", *[f"{pkg}=={ver}" for pkg, ver in pins.items()], ""]
    return "\n".join(lines)


def code_entries(cfg: dict[str, Any]) -> dict[str, bytes]:
    files = code_manifest(exclude_tests=True, suffixes=RUNTIME_SUFFIXES)["files"]
    entries = {rel: (V1_DIR / rel).read_bytes() for rel in files}
    entries["requirements-colab.txt"] = requirements_text(cfg).encode("utf-8")
    return entries


def dataset_entries(datasets_root: Path, cfg: dict[str, Any]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for h in cfg["horizons"]:
        pin = dataset_pin(cfg, h)
        root = datasets_root / pin["dataset_version"]
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out[f"{pin['dataset_version']}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
    return out


def snapshot_entries(snapshot_dir: Path, cfg: dict[str, Any]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for h in cfg["horizons"]:
        for path in snapshot_paths(snapshot_dir, h):
            out[path.name] = path.read_bytes()
    return out


def preflight(cfg: dict[str, Any], datasets_root: Path, snapshot_dir: Path) -> dict[str, Any]:
    problems = [*[f"[packaged] {p}" for p in upstream_sync.verify_packaged()], *[f"[dev] {p}" for p in upstream_sync.verify_dev_sources()]]
    if problems:
        raise PackageError("UPSTREAM LỆCH: " + "; ".join(problems))
    verify_candidate_set(cfg)
    pins: dict[str, Any] = {"datasets": {}, "snapshots": {}}
    for h in cfg["horizons"]:
        dp, sp = dataset_pin(cfg, h), snapshot_pin(cfg, h)
        root = datasets_root / dp["dataset_version"]
        if sha256_file(root / "output_checksums.json") != dp["output_checksums_sha256"] or sha256_file(root / "dataset_contract.json") != dp["dataset_contract_sha256"]:
            raise PackageError(f"h{h}: dataset {dp['dataset_version']} khác pin trong cấu hình")
        n, bad = verify_all_entries(root)
        if bad or n != dp["n_entries"]:
            raise PackageError(f"h{h}: kiểm {n} entry dataset, lỗi {bad}")
        pq, mf = snapshot_paths(snapshot_dir, h)
        if sha256_file(pq) != sp["snapshot_file_sha256"] or sha256_file(mf) != sp["manifest_sha256"]:
            raise PackageError(f"h{h}: snapshot khác pin trong cấu hình")
        pins["datasets"][f"h{h}"] = {"dataset_version": dp["dataset_version"], "output_checksums_sha256": dp["output_checksums_sha256"], "entries_verified": n}
        pins["snapshots"][f"h{h}"] = {"snapshot_file_sha256": sp["snapshot_file_sha256"], "manifest_sha256": sp["manifest_sha256"]}
    return pins


# ------------------------------------------------------------------ notebook + handoff

def _cell(index: int, kind: str, source: str) -> dict[str, Any]:
    lines = source.strip("\n").split("\n")
    cell_id = hashlib.sha256(f"{index}:{kind}:{source.strip()}".encode("utf-8")).hexdigest()[:12]      # ID xác định theo thứ tự + nội dung (nbformat 4.5)
    cell: dict[str, Any] = {"cell_type": kind, "id": cell_id, "metadata": {}, "source": [ln + "\n" for ln in lines[:-1]] + [lines[-1]]}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def notebook(manifest_name: str = "PACKAGE_MANIFEST.json") -> dict[str, Any]:
    sources: list[tuple[str, str]] = [
        ("markdown", """
# Classification v1 — hướng giá up/down/stable (h1, h3) trên Google Colab (GPU)

Notebook **mỏng**: chỉ kiểm gói, kiểm môi trường, gọi CLI `train_cls.py`; code thật nằm trong các module `.py` đã được kiểm hash. **Dev/exploratory**: test của dataset đã lộ cho regression,
cửa sổ ngày ngắn (h3 chỉ ~4 ngày validation/test) → kết quả KHÔNG phải số liệu cuối, KHÔNG có cổng triển khai, KHÔNG có mục tiêu accuracy 80%.

Thứ tự: ô 1 (tham số + mount) → ô 2 (kiểm hash + giải nén sạch) → ô 3 (môi trường: nếu lệch sẽ cài đúng phiên bản rồi BẮT BUỘC Restart session, chạy lại từ ô 1; **không có cờ cho phép lệch**)
→ ô 4 (self-test XGBoost THẬT + smoke toàn bộ đường huấn luyện trên dữ liệu tổng hợp) → ô 5 (huấn luyện h1 rồi h3, lần lượt) → ô 6 (tóm tắt). Runtime → Change runtime type → **GPU**.
Mọi log của ô 4 và ô 5 được ghi thêm vào `<DRIVE_DIR>/logs/` (đừng xóa; gửi kèm khi báo kết quả).
"""),
        ("code", f"""
# 1) Tham số — chỉ sửa DRIVE_DIR nếu bạn để file ở chỗ khác
DRIVE_DIR = "/content/drive/MyDrive/hotel_cls_v1"     # chứa: {manifest_name} và thư mục upload/ (3 zip) — xem HANDOFF.md
HORIZONS = ""                                         # trống = lần lượt h1 rồi h3 (khuyến nghị); hoặc "1" / "3" để chạy riêng từng cái
DEVICE = "cuda"                                       # xgboost phải chạy GPU thật; không có GPU ⇒ dừng (không âm thầm chạy CPU)

from google.colab import drive
drive.mount("/content/drive")
!nvidia-smi -L
"""),
        ("code", f"""
# 2) Kiểm toàn vẹn (SHA-256 so với {manifest_name}) rồi giải nén SẠCH (xóa bản cũ trước, không để file cũ sót lại)
import hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path

manifest = json.loads(Path(f"{{DRIVE_DIR}}/{manifest_name}").read_text(encoding="utf-8"))
assert manifest["schema"] == "cls-package-1", manifest["schema"]
for name, info in manifest["files"].items():
    data = Path(f"{{DRIVE_DIR}}/{{name}}").read_bytes()
    assert len(data) == info["bytes"] and hashlib.sha256(data).hexdigest() == info["sha256"], f"SHA-256/kích thước lệch: {{name}}"
    print("OK", name, f"{{info['bytes'] / 1e6:.1f}} MB")
for path in ("/content/cls_v1", "/content/datasets", "/content/snapshot"):
    shutil.rmtree(path, ignore_errors=True)
zipfile.ZipFile(f"{{DRIVE_DIR}}/upload/cls_v1_code.zip").extractall("/content/cls_v1")
zipfile.ZipFile(f"{{DRIVE_DIR}}/upload/datasets.zip").extractall("/content/datasets")
zipfile.ZipFile(f"{{DRIVE_DIR}}/upload/baseline_snapshot.zip").extractall("/content/snapshot")
sys.path.insert(0, "/content/cls_v1")
from cls_training.codeid import code_manifest
runtime = code_manifest(Path("/content/cls_v1"), exclude_tests=True, suffixes=(".py", ".yaml", ".json"))["code_sha256"]
assert runtime == manifest["identity"]["runtime_code_sha256"], f"mã trên Colab khác mã đã đóng gói: {{runtime}}"
print("runtime_code_sha256 khớp:", runtime)
"""),
        ("code", """
# 3) Môi trường: phiên bản PHẢI bằng bản ghim trong configs/train.yaml. Lệch ⇒ cài đúng phiên bản rồi Restart session và chạy lại từ ô 1 (không có cờ cho phép lệch).
import importlib.metadata as md
import yaml

expected = yaml.safe_load(Path("/content/cls_v1/configs/train.yaml").read_text(encoding="utf-8"))["environment"]["expected_versions"]
actual = {}
for pkg in expected:
    try:
        actual[pkg] = md.version(pkg)
    except md.PackageNotFoundError:
        actual[pkg] = None
print("đã ghim:", expected)
print("hiện có:", actual)
mismatch = {p: (expected[p], actual[p]) for p in expected if expected[p] != actual[p]}
if mismatch:
    subprocess.run([sys.executable, "-m", "pip", "-q", "install", *[f"{p}=={v}" for p, v in expected.items()]], check=True)
    raise RuntimeError(f"Lệch phiên bản {mismatch}: ĐÃ cài đúng phiên bản. BẮT BUỘC: Runtime → Restart session, rồi chạy lại từ ô 1.")
print("OK: môi trường khớp bản ghim.")
"""),
        ("code", """
# 4) Self-test thư viện XGBoost THẬT + GPU THẬT, rồi smoke toàn bộ đường huấn luyện trên dữ liệu tổng hợp (lỗi ⇒ DỪNG, không âm thầm bỏ XGBoost hay chạy CPU)
import time
Path(f"{DRIVE_DIR}/logs").mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
r = subprocess.run([sys.executable, "/content/cls_v1/scripts/train_cls.py", "--selftest-only", "--datasets-root", "/content/datasets", "--snapshot-dir", "/content/snapshot",
                    "--out-root", "/content/out", "--device", DEVICE], capture_output=True, text=True)
log = f"exit={r.returncode}\\n--- stdout ---\\n{r.stdout}\\n--- stderr ---\\n{r.stderr}\\n"
Path(f"{DRIVE_DIR}/logs/selftest_{stamp}.log").write_text(log, encoding="utf-8")
print(log[-6000:])
assert r.returncode == 0, f"self-test/smoke thất bại (exit {r.returncode}) — KHÔNG chạy tiếp; gửi toàn bộ log ở trên (và trong {DRIVE_DIR}/logs) cho Claude"
"""),
        ("code", """
# 5) Huấn luyện + đánh giá (TEST chỉ chạm sau khi khóa champion, chỉ cho danh sách đã khóa). Mỗi lần một run mới; kết quả chép lên Drive có kiểm hash SAU TỪNG horizon.
RUN_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
cmd = [sys.executable, "/content/cls_v1/scripts/train_cls.py", "--datasets-root", "/content/datasets", "--snapshot-dir", "/content/snapshot", "--out-root", "/content/out",
       "--drive-dir", f"{DRIVE_DIR}/models/v1", "--device", DEVICE, "--run-id", RUN_ID]
if str(HORIZONS).strip():
    cmd += ["--horizons", *str(HORIZONS).split()]
logf = open(f"{DRIVE_DIR}/logs/train_{RUN_ID}.log", "w", encoding="utf-8")
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in proc.stdout:
    print(line, end="")
    logf.write(line)
    logf.flush()
rc = proc.wait()
logf.write(f"exit={rc}\\n")
logf.close()
print("exit code:", rc, {0: "HOÀN TẤT (mọi horizon contract_complete)", 3: "CHƯA HOÀN TẤT (incomplete/no_valid_model) — KHÔNG phải kết quả cuối", 2: "LỖI (fail-closed)"}.get(rc, "?"))
assert rc in (0, 3), "huấn luyện thất bại; gửi log cho Claude"
"""),
        ("code", """
# 6) Tóm tắt (đọc report/analysis của từng run vừa tạo; không tính lại). Dev/exploratory — claim_level không phải cổng triển khai.
import pandas as pd
from cls_training.summary import summarize_run

status = json.loads(Path(f"/content/out/train_status_{RUN_ID}.json").read_text(encoding="utf-8"))
print("outcome:", status["outcome"], "| horizon chưa hoàn tất:", status["incomplete_horizons"])
for res in status["horizons"]:
    s = summarize_run(res["run_dir"])
    print(f"=== h{s['horizon']} {res['dataset_version']} | status={s['status']} contract_complete={s['contract_complete']} champion={s['champion']} claim_level={s['claim_level']} device={s['device_ledger']} elapsed={s['elapsed_seconds']}s")
    for w in s["warnings"]:
        print("  ! ", w)
    display(pd.DataFrame([{**{"id": f["id"], "family": f["family"], "weight": f["weight"], "champion": f["is_champion"], "cv_f1": f["cv_pooled_macro_f1"]},
                           "val_f1": f["validation"]["macro_f1"], "val_mcc": f["validation"]["mcc"], "test_f1": f["test"]["macro_f1"], "test_mcc": f["test"]["mcc"]} for f in s["finalists"]]))
    display(pd.DataFrame([{"comparator": p["id"], "val_f1": p["validation"]["macro_f1"], "test_f1": p["test"]["macro_f1"], "scored_on_test": p["scored_on_test"]} for p in s["panel"]]))
    print("claim:", json.dumps(s["claim"], ensure_ascii=False, default=str))
    print("ablations:", json.dumps(s["ablations"], ensure_ascii=False, default=str))
    print("tập trung (test, leave-one-out):", s.get("test_concentration"))
"""),
        ("markdown", """
## Mang gì về cho Claude

Tải **nguyên thư mục** `<DRIVE_DIR>/models/v1/<dataset>/run_<thời-gian>/` của từng horizon (report, predictions, bundle, analysis, run_manifest, COMPLETE), file `train_status_<thời-gian>.json` (trong `/content/out/`; chép lên Drive nếu muốn) và thư mục `<DRIVE_DIR>/logs/`
về `D:\\MSE\\CAPSTONE\\hotel-price-intelligence\\ml\\classification\\models\\v1\\` — giữ nguyên mọi lần chạy cũ, không ghi đè — rồi báo Claude đường dẫn. **Đừng chỉ gửi ảnh chụp bảng tóm tắt.**
Nếu exit code là 2/3 hoặc self-test thất bại, gửi toàn bộ đầu ra ở ô 4/5.
"""),
    ]
    cells = [_cell(i, kind, src) for i, (kind, src) in enumerate(sources)]
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python"},
                                         "accelerator": "GPU", "colab": {"provenance": []}}, "nbformat": 4, "nbformat_minor": 5}


def handoff_text(partial: dict[str, Any], delivery: str) -> str:
    files, idt = partial["files"], partial["identity"]
    env = ", ".join(f"{k}=={v}" for k, v in idt["environment_pins"].items())
    rows = "\n".join(f"| `{name}` | {info['bytes'] / 1e6:.1f} MB | `{info['sha256']}` |" for name, info in files.items())
    return f"""# HANDOFF — classification v1 (giao hàng `{delivery}`)

Phân loại hướng giá **up / down / stable** cho **h1 (`ds_20261010_nf1`)** và **h3 (`ds_20261010_nf3`)**. **Dev/exploratory**: test đã lộ cho regression, cửa sổ ngày ngắn, không có mục tiêu accuracy 80%,
không có cổng triển khai. Kết quả tốt hay xấu đều hợp lệ; mục đích là đo xem classifier có hơn các đối chứng (luôn-stable, momentum, hướng suy ra từ regression) trên validation hay không.

## Làm theo thứ tự này

1. Tạo **một thư mục Drive** `MyDrive/hotel_cls_v1` (tức `DRIVE_DIR = /content/drive/MyDrive/hotel_cls_v1`) và upload đúng cấu trúc này, giữ nguyên tên file:
   ```
   hotel_cls_v1/
     PACKAGE_MANIFEST.json
     upload/cls_v1_code.zip
     upload/datasets.zip
     upload/baseline_snapshot.zip
   ```
   Notebook `cls_v1_colab.ipynb` mở trực tiếp từ máy (File → Upload notebook) hoặc để cạnh thư mục trên. `HANDOFF.md` và `SHA256SUMS.txt` chỉ để bạn/Claude đối chiếu, không cần upload.
2. Mở notebook trên Google Colab (Pro), **Runtime → Change runtime type → GPU**.
3. Chạy lần lượt **ô 1 → ô 6**. Ô 3 có thể cài đúng phiên bản thư viện rồi báo Restart session: làm **Runtime → Restart session** rồi chạy lại từ ô 1. **Không có cờ để bỏ qua lệch phiên bản** — đây là cố ý.
4. Ô 4 (self-test XGBoost thật trên GPU + smoke toàn bộ đường huấn luyện trên dữ liệu tổng hợp) phải qua. Nếu thất bại thì dừng ở đó và gửi toàn bộ log cho Claude (không cần thử lại nhiều lần).
5. Ô 5 chạy **h1 rồi h3, lần lượt** (soft cap 2 giờ/horizon, thường ngắn hơn nhiều). Mỗi horizon lưu thư mục `run_<thời-gian>` lên `<DRIVE_DIR>/models/v1/<dataset>/` **ngay khi horizon đó xong** (sau khi kiểm lại hash).
   Exit code 0 = hoàn tất; **3 = chưa hoàn tất (không phải kết quả cuối; vẫn gửi về)**; 2 = lỗi. Log self-test/smoke/huấn luyện được ghi vào `<DRIVE_DIR>/logs/` — giữ lại toàn bộ.
   Nếu runtime Colab bị reset giữa chừng: chạy lại ô 1–5; horizon đã xong trên Drive được khôi phục không huấn luyện lại. **Gián đoạn giữa một lần fit KHÔNG resume được giữa fit** — horizon đang chạy dở sẽ chạy lại từ đầu với run mới.
6. Ô 6 in tóm tắt. Tải nguyên thư mục `run_...` của từng horizon, `train_status_*.json` và `logs/` về `D:\\MSE\\CAPSTONE\\hotel-price-intelligence\\ml\\classification\\models\\v1\\`, rồi báo Claude đường dẫn.

## Phải nhớ khi đọc kết quả

- Macro-F1 3 lớp cố định là chỉ số chính (stable chiếm ~79–87%); accuracy đơn lẻ không dùng để kết luận. Luôn so với 3 đối chứng; đối chứng có thể **không được chấm trên test** (sẽ ghi rõ, không điền số thay thế).
- `claim_level` tối đa là `dev_exploratory_validation_gain` (chỉ trên validation, và chỉ khi run `complete`); CI được tính sau lựa chọn mô hình, chỉ mang tính thăm dò. Không kết luận "không overfit / không leakage" từ một test đã lộ.
- Xác suất **chưa hiệu chỉnh**; không có calibrator/ngưỡng/tier trong v1. Cửa sổ ngày ngắn (đặc biệt h3) được báo bằng `short_window` trong report.
- Self-test XGBoost thật trên GPU là **bằng chứng đầu tiên** cho đường XGBoost: test cục bộ chỉ dùng XGBoost giả (fake có Booster giả); thư viện thật và GPU thật chưa từng chạy với mã này.
- Môi trường ghim (fail-closed): {env}. scikit-learn/numpy/pandas là bản bộ test cục bộ đã chạy; xgboost là bản Colab của dự án đã chạy cho regression (chưa được mã classification chạy).

## File trong gói

| File (trong `upload/`) | Kích thước | SHA-256 |
|---|---|---|
{rows}

`runtime_code_sha256` = `{idt['runtime_code_sha256']}` · `config_sha256` = `{idt['config_sha256']}` · `candidate_set_sha256` = `{idt['candidate_set_sha256']}`
"""


# ------------------------------------------------------------------ build + verify

def sha256sums_text(dest: Path) -> str:
    lines = [f"{sha256_file(dest / rel)}  {rel}" for rel in ("PACKAGE_MANIFEST.json", "cls_v1_colab.ipynb", "HANDOFF.md", *PACKAGE_FILES)]
    return "\n".join(lines) + "\n"


def build(delivery: str, out_root: Path, datasets_root: Path, snapshot_dir: Path) -> Path:
    safe_delivery_name(delivery)                                             # TRƯỚC preflight và trước khi tạo bất cứ thứ gì
    cfg = load_config()
    pins = preflight(cfg, datasets_root, snapshot_dir)
    root = checked_root(out_root)
    final = child_path(root, delivery)                                       # con TRỰC TIẾP của root đã resolve, không reparse
    if final.exists():
        raise PackageError(f"{final} đã tồn tại; không ghi đè (xóa thủ công bản cũ khi đã chốt bản mới)")
    dest = child_path(root, f".building_{delivery}")
    try:
        dest.mkdir()                                                         # độc quyền: đã có ⇒ không phải của ta ⇒ không xóa
    except FileExistsError as exc:
        raise PackageError(f"{dest} đã tồn tại (lần đóng gói khác đang chạy hoặc bỏ dở); kiểm tra và xử lý thủ công") from exc
    try:
        _build_into(dest, delivery, cfg, pins, datasets_root, snapshot_dir)
        report = verify_delivery(dest, label=delivery)
        os.replace(dest, final)                                              # chỉ gói đã qua verify mới xuất hiện dưới tên giao hàng
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)                              # chỉ thư mục tạm do CHÍNH ta vừa tạo
        raise
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return final


def _build_into(dest: Path, delivery: str, cfg: dict[str, Any], pins: dict[str, Any], datasets_root: Path, snapshot_dir: Path) -> None:
    (dest / "upload").mkdir()
    zips = {"upload/cls_v1_code.zip": code_entries(cfg), "upload/datasets.zip": dataset_entries(datasets_root, cfg), "upload/baseline_snapshot.zip": snapshot_entries(snapshot_dir, cfg)}
    files: dict[str, Any] = {}
    for name, entries in zips.items():
        write_zip(dest / name, entries)
        files[name] = {"sha256": sha256_file(dest / name), "bytes": (dest / name).stat().st_size, "n_entries": len(entries)}
    rt = code_manifest(exclude_tests=True, suffixes=RUNTIME_SUFFIXES)
    identity = {"runtime_code_sha256": rt["code_sha256"], "runtime_code_n_files": rt["n_files"], "config_sha256": cfg["config_sha256"],
                "candidate_set_sha256": cfg["tuning"]["candidate_set_sha256"], "upstream_manifest_sha256_lf": upstream_sync.manifest_sha256(),
                "environment_pins": cfg["environment"]["expected_versions"], "horizons": cfg["horizons"], **pins}
    partial = {"files": files, "identity": identity}
    nb_bytes = (json.dumps(notebook(), ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    handoff_bytes = handoff_text(partial, delivery).encode("utf-8")
    (dest / "cls_v1_colab.ipynb").write_bytes(nb_bytes)
    (dest / "HANDOFF.md").write_bytes(handoff_bytes)
    manifest = {"schema": SCHEMA, "delivery": delivery, "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files, "identity": identity,
                "companions": {"cls_v1_colab.ipynb": {"sha256": _sha(nb_bytes), "bytes": len(nb_bytes)}, "HANDOFF.md": {"sha256": _sha(handoff_bytes), "bytes": len(handoff_bytes)}},
                "caveats": ["dev/exploratory: test đã lộ cho regression", "XGBoost thật + GPU thật chưa từng chạy với mã này; self-test + smoke ở ô 4 là bằng chứng đầu tiên",
                            "môi trường fail-closed: lệch phiên bản ⇒ dừng (không có cờ cho phép lệch)"]}
    (dest / "PACKAGE_MANIFEST.json").write_bytes(canonical_bytes(manifest))
    (dest / "SHA256SUMS.txt").write_text(sha256sums_text(dest), encoding="utf-8", newline="\n")      # digest NGOÀI, không tự tham chiếu (loại chính nó)


def _forbidden(name: str) -> bool:
    parts = Path(name).parts
    return any(p in FORBIDDEN_NAMES or p.endswith(".pyc") or p == "tests" for p in parts) or Path(name).name == ".env"


def verify_delivery(dest: Path, label: str | None = None) -> dict[str, Any]:
    """Kiểm lại gói đã ghi NHƯ Colab sẽ dùng: hash/kích thước, digest ngoài, notebook/HANDOFF khớp manifest, giải nén sạch vào thư mục tạm, runtime hash == manifest, upstream packaged,
    cấu hình nạp/validate được, ĐỦ entry dataset (14 + output_checksums.json == pin), ĐỦ 4 file snapshot == pin, tập file đúng chính xác, không tests/pycache/.env/.git."""
    manifest = json.loads((dest / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    for name, info in {**manifest["files"], **manifest["companions"]}.items():
        p = dest / name
        if not p.is_file() or sha256_file(p) != info["sha256"] or p.stat().st_size != info["bytes"]:
            problems.append(f"{name}: hash/kích thước khác manifest")
    expected_sums = sha256sums_text(dest)
    if not (dest / "SHA256SUMS.txt").is_file() or (dest / "SHA256SUMS.txt").read_text(encoding="utf-8") != expected_sums:
        problems.append("SHA256SUMS.txt thiếu hoặc không khớp các file giao")
    extra = sorted(str(p.relative_to(dest).as_posix()) for p in dest.rglob("*") if p.is_file()
                   and p.relative_to(dest).as_posix() not in {*PACKAGE_FILES, "PACKAGE_MANIFEST.json", "cls_v1_colab.ipynb", "HANDOFF.md", "SHA256SUMS.txt"})
    if extra:
        problems.append(f"file ngoài danh sách giao: {extra}")
    if problems:
        raise PackageError("; ".join(problems))
    ident = manifest["identity"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, sub in (("upload/cls_v1_code.zip", "cls_v1"), ("upload/datasets.zip", "datasets"), ("upload/baseline_snapshot.zip", "snapshot")):
            with zipfile.ZipFile(dest / name) as zf:
                bad = zf.testzip()
                if bad:
                    raise PackageError(f"{name}: entry hỏng {bad}")
                for n in zf.namelist():
                    if n.startswith("/") or ".." in Path(n).parts or "\\" in n or ":" in n:
                        raise PackageError(f"{name}: đường dẫn không an toàn {n}")
                    if _forbidden(n):
                        raise PackageError(f"{name}: chứa file không được giao: {n}")
                zf.extractall(root / sub)
        code = root / "cls_v1"
        rt = code_manifest(code, exclude_tests=True, suffixes=RUNTIME_SUFFIXES)
        if rt["code_sha256"] != ident["runtime_code_sha256"]:
            raise PackageError("runtime_code_sha256 sau khi giải nén != manifest")
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import json; "
                 "from cls_training import upstream_sync as U; from cls_training.config import load_config; from cls_training.candidates import verify_candidate_set; "
                 "cfg = load_config(); verify_candidate_set(cfg); print(json.dumps({'upstream': U.verify_packaged(), 'config_sha256': cfg['config_sha256'], "
                 "'dataset_pins': [p for p in cfg['dataset_pins']], 'snapshot_pins': [p for p in cfg['baseline_snapshot_pins']]}))")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(code)], capture_output=True, text=True)
        if r.returncode != 0:
            raise PackageError(f"nạp gói đã giải nén thất bại: {r.stderr.strip()[-600:]}")
        got = json.loads(r.stdout.strip().splitlines()[-1])
        if got["upstream"] or got["config_sha256"] != ident["config_sha256"]:
            raise PackageError(f"gói giải nén không khớp danh tính: {got['upstream']} {got['config_sha256']}")
        want_datasets = {p["dataset_version"] for p in got["dataset_pins"]}
        have_datasets = {p.name for p in (root / "datasets").iterdir()}
        if have_datasets != want_datasets:
            raise PackageError(f"datasets.zip có {sorted(have_datasets)} != pin {sorted(want_datasets)}")
        for pin in got["dataset_pins"]:
            ddir = root / "datasets" / pin["dataset_version"]
            if sha256_file(ddir / "output_checksums.json") != pin["output_checksums_sha256"] or sha256_file(ddir / "dataset_contract.json") != pin["dataset_contract_sha256"]:
                raise PackageError(f"{pin['dataset_version']}: output_checksums/dataset_contract khác pin")
            n, bad = verify_all_entries(ddir)
            if bad or n != pin["n_entries"]:
                raise PackageError(f"{pin['dataset_version']}: kiểm {n}/{pin['n_entries']} entry, lỗi {bad}")
        want_snap = {name for h in ident["horizons"] for name in (snapshot_paths(Path("."), h)[0].name, snapshot_paths(Path("."), h)[1].name)}
        have_snap = {p.name for p in (root / "snapshot").iterdir()}
        if have_snap != want_snap or len(want_snap) != 4:
            raise PackageError(f"baseline_snapshot.zip có {sorted(have_snap)} != 4 file kỳ vọng {sorted(want_snap)}")
        for pin in got["snapshot_pins"]:
            pq, mf = snapshot_paths(root / "snapshot", int(pin["horizon"]))
            if sha256_file(pq) != pin["snapshot_file_sha256"] or sha256_file(mf) != pin["manifest_sha256"]:
                raise PackageError(f"h{pin['horizon']}: snapshot trong gói khác pin")
    return {"verified": True, "delivery": label or dest.name, "files": {n: i["sha256"] for n, i in manifest["files"].items()}, "companions": {n: i["sha256"] for n, i in manifest["companions"].items()},
            "runtime_code_sha256": ident["runtime_code_sha256"], "config_sha256": ident["config_sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delivery", required=True, help="tên thư mục giao hàng (basename an toàn), vd 20261019_d1")
    ap.add_argument("--datasets-root", default=str(WORKSPACE / "outputs" / "datasets"))
    ap.add_argument("--snapshot-dir", default=str(ML_DIR / "classification" / "results" / "v1" / "baseline_snapshot"))
    ap.add_argument("--out-root", default=str(ML_DIR / "classification" / "colab" / "v1"))
    args = ap.parse_args(argv)
    dest = build(args.delivery, Path(args.out_root), Path(args.datasets_root), Path(args.snapshot_dir))
    print(f"đã đóng gói: {dest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"PACKAGE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)

"""Dung THU MUC BAN GIAO MOI, tu du, cho replay bundle train-v3 (nguoi dung chi keo cac tep trong thu muc con len Drive).

    python build_replay_delivery.py --colab-root outputs/colab --models-root outputs/models/v3 --out outputs/colab/replay_v3_r3_20261008

Chi COPY / dong goi (khong move/xoa/ghi de artifact nao; thu muc dau ra phai MOI). Moi nguon duoc xac minh: goi r3 khop hash GPT da duyet (file 20), run qua `verify_run_dir`, hash run_manifest khop
file 23/24. Zip co thu tu/ngay co dinh => hash tai lap duoc. Mac dinh kiem `APPROVED`; `--no-approved-check` CHI cho kiem thu tren du lieu tong hop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXED_TIME = (2026, 10, 8, 0, 0, 0)

# GPT PASS PACKAGE FOR COLAB (file 20 muc 2) + run_manifest da doi chieu o file 23/24.
APPROVED = {
    "ds_20261006_dev1b_v3_r3/ml_train_pkg_20261008_063424.zip": "15138d4b965adf1d627a5e58f2df44ae56656a415b70d5c0442329b49bcb6ea4",
    "ds_20261006_dev1b_v3_r3/COLAB_MANIFEST.json": "908b01ddfddf7b7f152c93332e2c53c1fd49e51ea7bd21f23e975a5c6959f278",
    "ds_20261006_dev1b_v3_r3/dataset_ds_20261006_dev1b.zip": "ac950726698dc731309c406140aafcb8ee0cfbc25c901d58aa4503cd1cc23b64",
    "ds_20261006_dev3b_v3_r3/ml_train_pkg_20261008_063426.zip": "14005e93e956668b6c616f817e2e8e4c642ff705c0083e567f8c4f7e517e0455",
    "ds_20261006_dev3b_v3_r3/COLAB_MANIFEST.json": "64debfec3ba31a7ea9d6be01a3dc6a739c4d5b5ac4e72a9cae96ae1497fb9d3a",
    "ds_20261006_dev3b_v3_r3/dataset_ds_20261006_dev3b.zip": "143b944d4afafd3beff54adec099cb740977e65fb8f0550dbcee1dcfddd3b860",
    "ds_20261006_dev1b/run_20261008_054459/run_manifest.json": "df97229cfeedc46669d52a66139867bd37f6e3495fbaa938ad302dfe2178730d",
    "ds_20261006_dev3b/run_20261008_055008/run_manifest.json": "2f1c05dd6653c2825b910cd4d9b234afd6f59cba97ee69cca0c9c9e40b7aaf7d",
}
REAL_CASES = [
    {"label": "dev1b_h1", "pkg_dir": "ds_20261006_dev1b_v3_r3", "run": "ds_20261006_dev1b/run_20261008_054459", "dataset": "ds_20261006_dev1b", "horizon": 1},
    {"label": "dev3b_h3", "pkg_dir": "ds_20261006_dev3b_v3_r3", "run": "ds_20261006_dev3b/run_20261008_055008", "dataset": "ds_20261006_dev3b", "horizon": 3},
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_zip(path: Path, entries: list[tuple[str, Path]]) -> None:
    """Zip tai lap duoc: thu tu ten, ngay co dinh, cung muc nen; ten trong zip tuong doi (khong '..', khong tuyet doi)."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for arcname, source in sorted(entries, key=lambda e: e[0]):
            assert not arcname.startswith("/") and ".." not in Path(arcname).parts, arcname
            info = zipfile.ZipInfo(arcname, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, Path(source).read_bytes())


NOTEBOOK_SAFE_EXTRACT = '''import hashlib, json, os, pathlib, platform, subprocess, sys, time, zipfile
from importlib import metadata


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(zip_path, dest):
    """Giải nén vào thư mục MỚI; mọi thành viên phải nằm TRONG thư mục đích (chặn ../ và đường dẫn tuyệt đối)."""
    dest = pathlib.Path(dest).resolve()
    if dest.exists():
        raise RuntimeError(f"thư mục đích đã tồn tại: {dest} (cần thư mục MỚI, không ghi đè)")
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = pathlib.PurePosixPath(name).parts
            if name.startswith(("/", "\\\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise RuntimeError(f"zip có đường dẫn không an toàn: {name!r}")
            target = (dest / name).resolve()
            if target != dest and dest not in target.parents:
                raise RuntimeError(f"zip có thành viên ra ngoài thư mục đích: {name!r}")
        dest.mkdir(parents=True)
        archive.extractall(dest)
    return dest
'''


def build_notebook() -> dict:
    def code(text: str, cid: str) -> dict:
        return {"cell_type": "code", "id": cid, "metadata": {}, "execution_count": None, "outputs": [], "source": text.strip("\n").splitlines(keepends=True)}

    def md(text: str, cid: str) -> dict:
        return {"cell_type": "markdown", "id": cid, "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}

    cells = [
        md("""
# Replay bundle train-v3 (r3) — kiểm tra bundle tái lập đúng dự báo đã lưu

Notebook này **chỉ suy luận (inference)**: nạp các bundle `.joblib` của run đã chấm và tái lập `h*_predictions_v3.parquet` trên validation + TEST. **Không fit, không tune, không chọn lại model.**
Chỉ dùng **đúng thư mục này** (bỏ qua mọi thư mục/notebook khác trong `outputs/colab/`). Mỗi lần chạy một horizon: `dev1b_h1/` rồi `dev3b_h3/`.

Kết quả (`replay_report.json`) nằm trong Drive: `<DRIVE_DIR>/replay_out/<replay_id>/`. Replay chỉ chứng minh *bundle tái lập được dự báo đã lưu* — **không** chứng minh feature đúng thời điểm, không chứng minh model tốt/không overfit.
""", "a1"),
        code('''
# 1) Tham số — sửa 3 dòng đầu theo horizon đang chạy
DRIVE_DIR = "/content/drive/MyDrive/replay_v3_r3/dev1b_h1"      # thư mục Drive chứa đúng các tệp của MỘT thư mục con (dev1b_h1 hoặc dev3b_h3)
DATASET_VERSION = "ds_20261006_dev1b"                           # dev1b_h1 -> ds_20261006_dev1b ; dev3b_h3 -> ds_20261006_dev3b
RUN_ID = "run_20261008_054459"                                  # dev1b_h1 -> run_20261008_054459 ; dev3b_h3 -> run_20261008_055008
COMPAT_PROBE = False                                            # False (mặc định): runtime phải KHỚP phiên bản đã ghi trong run. True chỉ để thăm dò tương thích (KHÔNG phải exact-runtime replay)

from google.colab import drive
drive.mount("/content/drive")
!nvidia-smi -L
''', "b1"),
        code(NOTEBOOK_SAFE_EXTRACT + '''
# 2) Toàn vẹn các tệp trên Drive (SHA-256 so với DELIVERY_MANIFEST.json) + kiểm môi trường TRƯỚC khi giải nén/nạp bất cứ thứ gì
manifest = json.load(open(f"{DRIVE_DIR}/DELIVERY_MANIFEST.json", encoding="utf-8"))
assert manifest["dataset"] == DATASET_VERSION and manifest["run_id"] == RUN_ID, "DATASET_VERSION/RUN_ID không khớp thư mục này"
for name, info in manifest["files"].items():
    assert sha256_file(f"{DRIVE_DIR}/{name}") == info["sha256"], f"SHA-256 lệch: {name}"
    print("OK", name)
colab = json.load(open(f"{DRIVE_DIR}/COLAB_MANIFEST.json", encoding="utf-8"))
for name, info in colab["archives"].items():
    assert sha256_file(f"{DRIVE_DIR}/{name}") == info["sha256"], f"SHA-256 lệch so với COLAB_MANIFEST: {name}"
run_zip = f"{DRIVE_DIR}/{manifest['run_archive']}"
with zipfile.ZipFile(run_zip) as archive:
    recorded_manifest = json.loads(archive.read(f"{RUN_ID}/run_manifest.json"))
    horizon = int(recorded_manifest["expected_horizons"][0])
    recorded = json.loads(archive.read(f"{RUN_ID}/h{horizon}_report.json"))["environment"]["actual"]
keys = ["scikit-learn", "xgboost", "numpy", "pandas", "joblib", "pyarrow", "pyyaml"]
def current(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None
mismatch = {k: (recorded.get(k), current(k)) for k in keys if recorded.get(k) != current(k)}
py_ok = ".".join(str(recorded["python"]).split(".")[:2]) == ".".join(platform.python_version().split(".")[:2])
print("Python ghi trong run:", recorded["python"], "| hiện tại:", platform.python_version())
if (mismatch or not py_ok) and not COMPAT_PROBE:
    if not py_ok:
        raise RuntimeError("Python major.minor lệch bản đã ghi — không cài lại được. Đặt COMPAT_PROBE=True để chỉ thăm dò tương thích (KHÔNG phải exact-runtime).")
    pins = " ".join(f"{k}=={v[0]}" for k, v in mismatch.items() if v[0])
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pins.split()], check=True)
    raise RuntimeError(f"Môi trường lệch {mismatch}. Đã cài đúng phiên bản; hãy Runtime -> Restart session rồi chạy lại từ ô 1 (hành vi có chủ đích).")
print("runtime khớp run" if not mismatch and py_ok else f"COMPAT_PROBE: runtime lệch {mismatch}")
''', "c1"),
        code('''
# 3) Giải nén SẠCH vào thư mục MỚI (mỗi lần chạy một thư mục làm việc mới; không ghi đè)
WORK = f"/content/replay_work_{time.strftime('%Y%m%d_%H%M%S')}"
code_zip = next(f"{DRIVE_DIR}/{n}" for n in manifest["files"] if n.startswith("ml_train_pkg_"))
safe_extract(code_zip, f"{WORK}/code")                                   # -> {WORK}/code/ml
safe_extract(f"{DRIVE_DIR}/dataset_{DATASET_VERSION}.zip", f"{WORK}/datasets")
safe_extract(run_zip, f"{WORK}/runs")                                    # -> {WORK}/runs/<RUN_ID>
safe_extract(f"{DRIVE_DIR}/replay_tools.zip", f"{WORK}/tools")           # -> {WORK}/tools/replay_tools
print(os.listdir(WORK))
''', "d1"),
        code('''
# 4) Replay (chỉ suy luận). Dung sai khóa trong replay_config.json (không có tham số dòng lệnh để đổi)
OUT_ROOT = f"{DRIVE_DIR}/replay_out"
flag = "--compat-probe" if COMPAT_PROBE else ""
!python {WORK}/tools/replay_tools/replay_bundles_v3.py --code-root {WORK}/code --run-dir {WORK}/runs/{RUN_ID} --dataset-dir {WORK}/datasets/{DATASET_VERSION} --colab-manifest {DRIVE_DIR}/COLAB_MANIFEST.json --output-root {OUT_ROOT} {flag}
''', "e1"),
        code('''
# 5) Tóm tắt — đọc replay_report.json mới nhất; PASS_EXACT_RUNTIME = bounded numeric + runtime khớp; bitwise_exact là kết luận RIÊNG
import glob
latest = sorted(glob.glob(f"{OUT_ROOT}/replay_*/replay_report.json"))[-1]
rep = json.load(open(latest, encoding="utf-8"))
print(latest); print(json.dumps(rep["verdict"], ensure_ascii=False, indent=1))
for problem in rep.get("problems", []):
    print("VẤN ĐỀ:", problem)
for h, hrep in rep.get("horizons", {}).items():
    for split, srep in hrep["splits"].items():
        for model, st in srep["models"].items():
            print(f"h{h} {split:10s} {model:22s} n={st['n']} failed={st['failed_rows']} exact={st['exact_match_rows']} max|Δ|={st['max_abs_delta_vnd']} agg_pass={st['aggregates_pass']}")
    print("không replay (không có bundle):", hrep["saved_columns_not_replayed"])
    print("XGB device khi replay:", {n: b.get("xgb_actual_device_during_replay") for n, b in hrep["bundles"].items() if "xgb_actual_device_during_replay" in b})
''', "f1"),
    ]
    return {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"gpuType": "T4", "provenance": []}, "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


HANDOFF = """# Replay bundle train-v3 (r3) — hướng dẫn chạy (CHỈ dùng thư mục này)

**Trạng thái:** gói replay này **chưa được GPT duyệt**; đừng chạy cho tới khi Claude báo "gói replay đã được duyệt" (sau khi GPT review code + gói).
Đây là kiểm tra **kỹ thuật**: bundle `.joblib` của run Colab đã chấm có tái lập đúng `h*_predictions_v3.parquet` không (validation + TEST), chỉ suy luận — không fit/tune/chọn lại model.

## Có gì trong thư mục
| Mục | Là gì |
|---|---|
| `replay_v3_r3.ipynb` | Notebook Colab (file Jupyter bạn mở) |
| `dev1b_h1/` | Mọi tệp để upload cho horizon 1 (kéo cả thư mục con lên Drive) |
| `dev3b_h3/` | Mọi tệp để upload cho horizon 3 |
| `DELIVERY_MANIFEST.json` | SHA-256 của mọi tệp trong thư mục này |

## Cách chạy (hai lần: h1 rồi h3)
1. Drive: tạo `MyDrive/replay_v3_r3/` rồi kéo **nguyên thư mục con** vào: `dev1b_h1/` → `MyDrive/replay_v3_r3/dev1b_h1/`; `dev3b_h3/` → `MyDrive/replay_v3_r3/dev3b_h3/`.
2. Mở `replay_v3_r3.ipynb` trên Colab, Runtime → GPU (giống lần train).
3. Ô 1 đặt: h1 → `DRIVE_DIR=".../replay_v3_r3/dev1b_h1"`, `DATASET_VERSION="ds_20261006_dev1b"`, `RUN_ID="run_20261008_054459"`; h3 → `.../dev3b_h3`, `ds_20261006_dev3b`, `run_20261008_055008`. Giữ `COMPAT_PROBE=False`.
4. Chạy ô 1→5. Nếu ô 2 báo "Restart session": Runtime → Restart session rồi chạy lại từ ô 1 (có chủ đích: đưa phiên bản thư viện về đúng bản lúc train).
5. Kết quả: `<DRIVE_DIR>/replay_out/replay_<thời gian>/replay_report.json` (+ `.sha256`). Tải thư mục `replay_out` về `D:\\MSE\\CAPSTONE\\outputs\\replay\\<dev1b_h1|dev3b_h3>\\` rồi báo Claude.

## Cách đọc kết quả
- `PASS_EXACT_RUNTIME`: runtime khớp phiên bản lúc train, thiết bị XGBoost khi replay đọc được **và** mọi dòng val/TEST nằm trong dung sai (|replay−saved| ≤ 0,01 VND + 1e−7·|saved|). `bitwise_exact` là kết luận RIÊNG (mọi dòng trùng từng bit).
- `PASS_BOUNDED_DEVICE_UNVERIFIED`: số khớp trong dung sai và runtime khớp, nhưng không đọc được thiết bị XGBoost khi replay — **không** ghi là exact-runtime.
- `PASS_COMPAT_PROBE_ONLY`: chỉ khi bạn đặt `COMPAT_PROBE=True` và runtime lệch — **không** phải exact-runtime replay.
- `FAIL`: giữ nguyên báo cáo, đừng chạy lại với dung sai khác (không có tham số đổi dung sai). Báo Claude.
- Replay **không** chứng minh feature đúng thời điểm dự báo, không chứng minh model tốt hay không overfit.
"""


def build(colab_root: Path, models_root: Path, out: Path, *, cases: list[dict], check_approved: bool) -> dict:
    sys.path.insert(0, str(HERE.parent / "ml"))
    from training.run_transaction import verify_run_dir  # noqa: PLC0415

    if out.exists():
        raise SystemExit(f"thu muc dau ra da ton tai: {out} (khong ghi de)")
    sources: dict[str, Path] = {}
    for case in cases:
        pkg_dir = colab_root / case["pkg_dir"]
        for path in sorted(pkg_dir.iterdir()):
            sources[f"{case['pkg_dir']}/{path.name}"] = path
        run = models_root / case["run"]
        problems = verify_run_dir(run)
        if problems:
            raise SystemExit(f"run {run} khong hop le: {problems}")
        sources[f"{case['run']}/run_manifest.json"] = run / "run_manifest.json"
    if check_approved:
        for key, expected in APPROVED.items():
            actual = sha256_file(sources[key]) if key in sources else None
            if actual != expected:
                raise SystemExit(f"nguon khong khop hash da duoc duyet/doi chieu: {key}: {actual} != {expected}")
    out.mkdir(parents=True)
    tools_dir = out / "_build_tmp_replay_tools"
    tools_dir.mkdir()
    for name in ("replay_bundles_v3.py", "replay_config.json"):
        (tools_dir / name).write_bytes((HERE / name).read_bytes())
    manifest_tools = {"tool_version": json.loads((HERE / "replay_config.json").read_text(encoding="utf-8"))["version"],
                      "files": {name: sha256_file(tools_dir / name) for name in ("replay_bundles_v3.py", "replay_config.json")}}
    (tools_dir / "REPLAY_MANIFEST.json").write_text(json.dumps(manifest_tools, indent=2, sort_keys=True), encoding="utf-8")
    top: dict[str, dict] = {}
    for case in cases:
        folder = out / case["label"]
        folder.mkdir()
        pkg_dir, run = colab_root / case["pkg_dir"], models_root / case["run"]
        files: dict[str, dict] = {}
        for path in sorted(pkg_dir.iterdir()):
            (folder / path.name).write_bytes(path.read_bytes())
        run_id = run.name
        run_zip = folder / f"run_{case['dataset']}_{run_id}.zip"
        write_zip(run_zip, [(f"{run_id}/{p.relative_to(run).as_posix()}", p) for p in sorted(run.rglob("*")) if p.is_file()])
        write_zip(folder / "replay_tools.zip", [(f"replay_tools/{p.name}", p) for p in sorted(tools_dir.iterdir())])
        for path in sorted(folder.iterdir()):
            files[path.name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        (folder / "DELIVERY_MANIFEST.json").write_text(json.dumps({"dataset": case["dataset"], "run_id": run_id, "horizon": case["horizon"], "run_archive": run_zip.name, "files": files},
                                                                   indent=2, sort_keys=True), encoding="utf-8")
        top[case["label"]] = files
    for path in sorted(tools_dir.iterdir()):
        path.unlink()
    tools_dir.rmdir()
    (out / "replay_v3_r3.ipynb").write_text(json.dumps(build_notebook(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "HANDOFF_replay.md").write_text(HANDOFF, encoding="utf-8")
    listing = {p.relative_to(out).as_posix(): sha256_file(p) for p in sorted(out.rglob("*")) if p.is_file()}        # top-level DELIVERY_MANIFEST chua ton tai o thoi diem nay
    (out / "DELIVERY_MANIFEST.json").write_text(json.dumps({"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": listing, "tool_manifest": manifest_tools}, indent=2, sort_keys=True), encoding="utf-8")
    return {"out": str(out), "files": listing}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--colab-root", required=True, type=Path)
    parser.add_argument("--models-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--no-approved-check", action="store_true")
    args = parser.parse_args()
    result = build(args.colab_root, args.models_root, args.out, cases=REAL_CASES, check_approved=not args.no_approved_check)
    for name, sha in result["files"].items():
        print(f"{sha}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

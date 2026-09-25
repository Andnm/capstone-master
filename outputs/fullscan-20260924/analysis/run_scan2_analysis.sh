#!/usr/bin/env bash
# Chạy toàn bộ phân tích cho lần quét toàn cohort thứ hai (mặc định run #3 trong DB fullscan; run #1 = lần 1, TẮT). Claude, 2026-09-25.
# Ba phân tích độc lập nhau nên chạy song song; đầu ra lưu cạnh script. Chỉ ĐỌC (DB cô lập + artifact).
# Dùng:  bash outputs/fullscan-20260924/analysis/run_scan2_analysis.sh [RUN_QUÉT_2=3]
set -u
cd /d/MSE/CAPSTONE/hotel-price-intelligence/backend || exit 1
export PYTHONIOENCODING=utf-8
PY=./venv/Scripts/python.exe
A=../../outputs/fullscan-20260924/analysis
RUN=${1:-3}
$PY $A/analyze_fullscan.py "$RUN"        > "$A/fullscan_run${RUN}_output.txt"          2>&1 &
$PY $A/compare_scans.py 1 "$RUN"         > "$A/compare_scans_1_${RUN}_output.txt"      2>&1 &
$PY $A/analyze_gap_transfer.py "$RUN"    > "$A/gap_transfer_run${RUN}_output.txt"      2>&1 &
wait
for f in fullscan_run${RUN}_output.txt compare_scans_1_${RUN}_output.txt gap_transfer_run${RUN}_output.txt; do
  echo "== $f: $(wc -l < "$A/$f") dòng; lỗi: $(grep -c -E 'Traceback|Error' "$A/$f")"
done

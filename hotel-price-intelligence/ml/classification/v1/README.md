# classification / v1 — phân loại hướng giá up/down/stable (h1, h3)

Thế hệ thiết kế thứ nhất, **giai đoạn hiện tại: đã xong đợt dev h1/h3 — GPT `PASS CODE` (16), `PASS COLAB PACKAGE` (18, delivery `../colab/v1/20261019_d1/` đóng băng), `PASS RESULTS` (20); không fit trên máy chính**. Kế hoạch, hợp đồng nhãn và giao thức đánh giá được GPT `PASS PLAN`
trong thread `discuss/classification-model-20261019/` (`artifacts/plan/v1/`). Phiên bản = thư mục: không đặt `*_v2.*` cạnh nhau; thế hệ mới ⇒ `../v2/`.

```text
v1/
  cls_training/        package (import cls_training); upstream/ = bản copy tối thiểu từ regression/shared, KHÔNG import chúng lúc chạy
                       labels · metrics · config · data · features · candidates · selection · runner · artifact · analysis · bootstrap · panel · snapshot · transaction · drive · pipeline · selftest
  configs/train.yaml   cấu hình + pin dữ liệu/snapshot (config_sha256)
  scripts/             preflight_real_data.py, export_baseline_snapshot.py, train_cls.py (Colab), analyze_results.py (phân tích lại run đã tải về), package_for_colab.py (đóng gói giao hàng)
  tests/               test (cls_testenv.py = hằng số/marker; conftest.py = bootstrap riêng của v1)
  UPSTREAM_MANIFEST.json   nguồn/hash/hàm đã copy (sinh + kiểm bằng cls_training/upstream_sync.py)
```

## Lệnh (từ thư mục `ml/`, Python = `C:\Users\ADMIN\anaconda3\python.exe`)

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest classification/v1/tests -q                      # toàn bộ test (số lượng xem báo cáo thread) (cần outputs/datasets cho test dữ liệu thật; tự skip nếu thiếu)
python -B classification/v1/scripts/preflight_real_data.py --out classification/results/v1/preflight/preflight_report.json   # KHÔNG fit
python -B classification/v1/scripts/train_cls.py --help                                   # huan luyen that CHI tren Colab (may chinh tu choi); self-test XGBoost that chay truoc khi nap dataset; ma thoat 0/3/2
python -B classification/v1/scripts/analyze_results.py --run-dir RUN --snapshot-dir SNAP --out-dir OUT   # phan tich lai run da tai ve (khong fit; --datasets-root de doi chieu day du)
python -B classification/v1/scripts/export_baseline_snapshot.py     # đọc regression, ghi snapshot vào classification/results/v1/baseline_snapshot (xuất lại ⇒ cập nhật pin)
python -B classification/v1/cls_training/upstream_sync.py --packaged   # di động: bản copy == UPSTREAM_MANIFEST.json (không cần nguồn regression/shared)
python -B classification/v1/cls_training/upstream_sync.py              # máy chính: bản copy == render lại từ nguồn hiện tại (bắt drift); thêm --write để sinh lại có chủ ý
```

## Quy tắc

- Không fit mô hình thật trên máy chính; fit đầy đủ chỉ ở Colab (người dùng chạy). Không build lại dataset; chỉ đọc dataset bất biến qua kiểm toàn vẹn + pin.
- Nhãn luôn `(y − c)/c` với ngưỡng ±0,02 so sánh nghiêm ngặt (xem `cls_training/labels.py`); không dùng `y/c − 1`.
- Dự đoán regression có NaN trên split nghĩa là **chưa được chấm**, không phải `stable` (`cls_training/snapshot.py`, `panel.py`).
- Thay đổi nội dung nguồn upstream ⇒ `upstream_sync.verify_dev_sources()` báo drift; cập nhật phải sinh lại có chủ ý và được review. Preflight mặc định kiểm chế độ `packaged` (di động).
- Cấu hình được validate khi nạp: mỗi horizon đúng một dataset pin và một snapshot pin; không có đường chạy không pin.
- Một horizon chỉ được coi là **thành công** (cache/resume) khi `status=complete`, `contract_complete=true`, đủ artifact, analysis và validator artifact dự đoán (`artifact.py`) đều đạt; run incomplete/no_valid_model được giữ để audit nhưng không bao giờ là cache. Chạy lại luôn dùng `run_id` mới; không tự xoá/ghi đè.
- Hạn chót mềm dùng chung từ đầu horizon, kiểm trước **mọi** fit được lên lịch; fit đang chạy được phép vượt, nhưng vượt hạn ⇒ `contract_complete=false`.
- Thiết bị XGBoost đọc từ `Booster.save_config()`; thư viện XGBoost thật chỉ được chứng minh bằng self-test trên Colab (test cục bộ dùng fake có Booster giả).
- **Gói Colab = một thư mục giao hàng** `../colab/v1/<delivery>/` do `scripts/package_for_colab.py --delivery <tên>` tạo (không ghi đè; tên an toàn; build vào thư mục tạm rồi verify rồi mới đổi tên): `cls_v1_colab.ipynb`, `HANDOFF.md`, `PACKAGE_MANIFEST.json`, `SHA256SUMS.txt`, `upload/{cls_v1_code,datasets,baseline_snapshot}.zip`.
  Đổi bất kỳ file runtime nào (`.py/.yaml/.json` ngoài tests) ⇒ `runtime_code_sha256` đổi ⇒ phải đóng gói lại và xin review lại; bản giao cũ chỉ được xóa khi đã chốt bản mới (và đã lưu hash).

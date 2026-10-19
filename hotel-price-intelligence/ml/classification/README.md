# Classification

Phân loại hướng giá up/down/stable (stretch goal). Phiên bản = thư mục: **[`v1/`](v1/README.md)** là thế hệ đầu — **đã hoàn tất đợt dev h1/h3** (GPT `PASS CODE` 16, `PASS COLAB PACKAGE` 18, `PASS RESULTS` 20; kết quả
**dev/exploratory, không có claim**). Kế hoạch + hợp đồng nhãn + giao thức đánh giá: thread `discuss/classification-model-20261019/artifacts/plan/v1/`. Thế hệ sau ⇒ `v2/`; khi `v2` được duyệt, `v1` bị xóa sau khi bằng chứng tối thiểu có checksum đã được lưu.

Dùng lại `../shared/dataset_builder` và dataset bất biến, không nhân đôi pipeline feature/label. Artifact sinh ra (gitignore, không phải backup):

| Thư mục | Nội dung |
| --- | --- |
| `v1/` | mã, cấu hình, test (package `cls_training`) |
| `colab/v1/20261019_d1/` | gói giao hàng đã `PASS COLAB PACKAGE` — **không sửa/xóa/rebuild tại chỗ**; sửa gì ⇒ delivery mới |
| `models/v1/` | kết quả Colab đã mang về: `ds_20261010_nf{1,3}/run_20261019T153707Z/` (nguyên thư mục run) + `logs/` |
| `results/v1/` | `baseline_snapshot/` (snapshot regression đã ghim), `preflight/preflight_report.json`, `DEV_RESULTS_SUMMARY.md` (tóm tắt + checksum bằng chứng) |

Đọc kết quả: `results/v1/DEV_RESULTS_SUMMARY.md`. Lần Colab sau dùng Drive `MyDrive/CAPSTONE/Classification/...`.

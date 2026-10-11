# Q0 dossier — xác nhận luồng cleanup bao ngoài (88), ghi TRƯỚC khi `start` (2026-10-09T10:33:38Z)

Tôi (Claude) xác nhận điều kiện bắt buộc của `88-gpt-pass-targeted-renewal87-and-one-explicit-q0-workflow-approval.md`:

- Toàn bộ chuỗi `start (handshake)` → [rc0] `evidence` → [rc0] `q0` chạy trong MỘT driver (`run_q0_workflow.py`, SHA `4e4f93286ac190196aae1bf5f60778e4948e7ad4c48e1f5452b9f7d23d95154d`) với khối `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = phát STOP + chờ có hạn `stopped` verified-dead (hoặc `refused` xác nhận chưa từng spawn; không ngụy tạo `publisher_stopped`/verified-dead khi chưa launch); (2) `collect` (SHA artifact). `collect` không che failure: mã thoát = stage nonzero đầu tiên / cleanup không xác nhận / ngoại lệ driver.
- Áp dụng kể cả khi `start`/`evidence`/Q0 ném lỗi, bị cancel hoặc trả nonzero, và cả đoạn `log(supervisor_launched)` nằm giữa `Popen` và `start_handshake` (trạng thái `pending` ⇒ STOP + xác nhận kết thúc có hạn).
- Một attempt mới duy nhất sau pre-start refusal 83; không stage nào chạy lại; không attempt thứ ba. Q0 actual invocation tối đa một lần. Bắt đầu trước 11:00:00Z (18:00 VN), không quá 11:30:00Z.
- Pin 88 (kiểm trước khi start; lệch ⇒ không chạy): supervisor `1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42`; operator `a35fbccc86dcbd57effb986d220b3f52aced5f7bb9fe9ebb0c89d1c055187d70`; 82a `26083fed93bcd7bf1dca62f5c66726b0c83be3b03bf1de51da90cd2bd70c92ca`; 82c `935b8c7c54e0a6ff89881ae15b78babdec2a7aa6c5a857ad1dd54aa181e392c0`; profile/nguồn/interpreter/plan-file/entry/runner do `operator_q0.preconditions()` kiểm.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.

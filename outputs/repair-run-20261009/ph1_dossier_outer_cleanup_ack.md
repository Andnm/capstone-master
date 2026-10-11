# Ph1 dossier — xác nhận luồng cleanup bao ngoài (90a), ghi TRƯỚC khi `archive_q0_live`/`start` (2026-10-09T11:09:04Z)

Tôi (Claude) xác nhận điều kiện bắt buộc của 90a/90b và ruling thực thi của GPT:

- Chuỗi `archive_q0_live` (một lần) → `start (handshake)` → [rc0] `evidence` → [rc0] `ph1` chạy trong MỘT driver (`run_ph1_workflow.py`, SHA `0192533ee5cb0ee0a7693e9af3baeaaa5f88a77274bd5c18b369b24c855d12ec`) với `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = STOP + chờ có hạn `stopped` verified-dead (hoặc `refused` xác nhận chưa từng spawn; không ngụy tạo `publisher_stopped`); (2) `collect`. Khác Q0 driver: `collect` lỗi/ngoại lệ ⇒ **nonzero**, và `collect` không che failure của stage/cleanup.
- Áp dụng kể cả khi `start`/`evidence`/Ph1 ném lỗi, bị cancel hoặc trả nonzero, và cả đoạn giữa `Popen` và `start_handshake` (trạng thái `pending` ⇒ STOP + xác nhận kết thúc có hạn).
- Một attempt Ph1 duy nhất; không stage nào chạy lại; không Ph1 phụ/heavy/Q0. Bắt đầu 11:00Z ≤ t < 12:00Z, hết hiệu lực tuyệt đối 13:00Z (20:00 VN).
- Pin (kiểm trước khi chạy; lệch ⇒ không chạy): operator_ph1 `e8c59eb0c08fd4144dee51728f5c48dbdfdc7f3246a8f933ecf2dc7e5a99873d`; supervisor `1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42`; 90a `7d4c007e65a814c8438d42f7aa3e685ba0e90fe244a93458434450e89a46d3b7`; 90b `4131145de37a9dfc1d3a1c7adcd8578b388220779685551ed053d1ded9f369d1`; profile `a5a0f1e0bf6f64101884d2b1d726b26f0f871b37893e1841edb211554f917f51`; plan-file `0dbfc554706c9a60a7f7a926f02e4659fcd0f2c22e9c294b6e3e693989ebe24a`; canonical `ba951b990f5df1db8d10a04f3174f7e5c28cb2bffb8cf9a06cc4c68f3eb0d81b`; invocation `e3ea7a635ae6d3dae7fb7b4af0d939aa8d70b687dca39f470645bf616c180be4`; nguồn/interpreter/parent/entry/runner do `operator_ph1.preconditions()` kiểm.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.

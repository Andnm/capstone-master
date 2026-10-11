# Q0 dossier — xác nhận lifecycle trước khi khởi động (2026-10-09T10:01:04Z)

Tôi (Claude) xác nhận các điều kiện 82a/82c và exact invocation TRƯỚC khi khởi động publisher:

- Chỉ MỘT publisher, khởi động trong 17:00–18:00 VN ngày 09/10/2026, hết hiệu lực 18:30 VN (11:30Z); phục vụ plan Q0 `6c5a18fe…`; không Ph1/3300 s/heavy; không auto-restart/extend/đổi cadence.
- argv đúng 82c; cwd `backend`; môi trường Clear + đúng 6 biến (Popen `env=` tường minh, `shell=False`, `CREATE_NO_WINDOW`); stdout/stderr ghi thẳng vào tệp `q0_publisher.log` (không pipe chưa đọc).
- Kiểm `DB_POOL_SIZE` qua Settings bằng một child hermetic TRƯỚC khi import tạo pool; chỉ ghi số nguyên; ≤5 mới tiếp tục.
- PID/creation-time/argv/profile SHA/source SHA/wall-clock start của process do lần này tạo ghi vào `q0_supervisor.jsonl`; không tìm/kill theo tên/glob.
- Hard wall deadline = start+900 s và không sau 11:30Z; dừng đúng process của mình (kill + wait 5 s + verified-dead) khi Q0 xong/refused/fail/cancel hoặc publisher busy/lỗi/stale.
- Publish đầu phải fresh + READ ONLY + zero-active; chờ lead 90 s; nếu busy ⇒ KHÔNG chạy Q0.
- Exact Q0 invocation (82a, một lần, không auto-retry): `D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_run_v1.py --approval D:\MSE\CAPSTONE\discuss\model-results-improvement-20261008\82a-gpt-one-q0-outer-approval.md --plan D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\q0.plan.json --plan-dir D:\MSE\CAPSTONE\outputs\repair-run-20261009\q0\plan --launcher-logs D:\MSE\CAPSTONE\outputs\repair-run-20261009\q0\logs --crawl-evidence D:\MSE\CAPSTONE\outputs\repair-run-20261009\live_crawl_idle_evidence.json`
- Hash: 82a `26083fed93bcd7bf1dca62f5c66726b0c83be3b03bf1de51da90cd2bd70c92ca`; 82c `935b8c7c54e0a6ff89881ae15b78babdec2a7aa6c5a857ad1dd54aa181e392c0`; profile `616162f83bb6869654dd7861bff45459de090b53a8e9cabecf1390154ed14add`; nguồn `6429482e6deddf711a8228726ec4776d7450bb82b526194299c9eae6c82ed4d9`; interpreter `0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd`; plan-file `3ab443931b914ce2e4adecb55e8c84d3ba1e95afa53229770583d0a6372f131e`; entry `bc93c167f7f64544818cb7b4ce54e11cca6e39f77dd400987df038e586182fb0`; runner `d3b4b595bd7c52a67af147f2ef25c614627fc9e57fdf5815791ffc463951aa8d`.

# Ph1 supplemental dossier — pre-run read-only (2026-10-09T11:09:48Z)

- Dispatch trong 18:00 ≤ VN < 18:50: **True**; ok tổng: **False**; vấn đề: ['ph1_outputs_already_exist', 'live_q0_sha_mismatch', 'archival_target_exists'].
- Pin (13 tệp, gồm parent interpreter, driver, operator, supervisor, 90a, 90b, profile, plan-file, nguồn publisher, child interpreter, entry, runner, 82b): 13/13 khớp.
- `ph1/plan` rỗng: True; đầu ra Ph1 chưa tồn tại: 5/12.
- Q0 verified-dead: True; live Q0 đúng SHA `d54b6ca1417475fa62e015f428883c516fd0ada372270dc52052616bd2b9336c`: False; archival target chưa tồn tại: False.
- OS crawler-process probe (`Get-CimInstance Win32_Process` lọc python + `run_scheduled_calendar`): returncode **0**, timed_out **False**, stderr ``, PIDs **[]**, success **True**; không lấy `[]` của probe lỗi làm idle.
- SHA kết quả: xem `ph1_prerun_check.json` (băm trong dossier gửi GPT).

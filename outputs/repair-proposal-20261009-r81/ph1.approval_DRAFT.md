# Ph1 doc ledger cu x2 + P4 (chi doc) — BAN NHAP approval (chua co hieu luc)

> NHAP khong phai authority: approval THAT la mot tep `NN-gpt-*.md` nam truc tiep trong thu muc thao luan, co dung nam dong duoi day (xem `repair_run_v1`). Hai dong cuoi can dien tu `--check-only`.

APPROVED_PLAN_SHA256: `f47bb6a4a574408ffa78947407f1bc1bc61163d1508c4c830ace2ea89f634e11`
APPROVED_NOT_AFTER_UTC: <YYYY-MM-DDTHH:MM:SSZ: han dung, nguoi duyet dien>
APPROVED_PLAN_DIR: <duong dan tuyet doi cua thu muc plan MOI/RONG cho dung lan chay nay, nguoi duyet dien>
APPROVED_ENTRYPOINT_SHA256: `bc93c167f7f64544818cb7b4ce54e11cca6e39f77dd400987df038e586182fb0`
APPROVED_INVOCATION_SHA256: <computed_invocation_sha256 do `repair_run_v1 --check-only` in ra cho dung lenh goi>

## Cai dat an toan (da nam trong hash)

```json
{
  "baseline_cap_s": 20.0,
  "containment_grace_s": 10.0,
  "gate_poll_s": 5.0,
  "margin_s": 60.0,
  "max_probes": 4,
  "probe_cap_s": 15.0,
  "q0_max_age_s": 3600.0,
  "receipt_reserve_s": 5.0,
  "settle_s": 2.0,
  "total_budget_s": 3000.0,
  "window_end_vn": [
    0,
    20
  ]
}
```

## Cong cu da ghim (trong hash)

```json
{
  "observer_sha256": null,
  "quiescence_model_sha256": "15780d46152658fae754c17a816bea428f4d9c69fd89a32761656811b0542173",
  "runner_sha256": "d3b4b595bd7c52a67af147f2ef25c614627fc9e57fdf5815791ffc463951aa8d",
  "windows_job_sha256": "0d0f19e38a693cdbc7fa33947af0c23159fbb0b7644722e471d286af569ae59e"
}
```

Q0 receipt pin: `None`

## Cac phase (theo thu tu, trong hash)

### 1. `ph1_old_ledger_h1` — kind `readonly`, deadline 900.0 s

- spec_sha256: `eae6b0cb910c4fb20b52156ea9c9ecb5030d0e9f2b95d051f3f01af9aeb2a2c9`
- argv_sha256: `9b5ba7855d314ee5ec62aeb22deb88e047cdc97f9a25cbf52f6b269031737a32`
- cwd: `D:\MSE\CAPSTONE\hotel-price-intelligence\backend`
- env (ten + gia tri, khong secret): `{"PATH": "C:\\Program Files\\Git\\cmd;C:\\Windows\\System32;C:\\Windows", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "SystemRoot": "C:\\Windows", "TEMP": "C:\\Windows\\Temp", "TMP": "C:\\Windows\\Temp"}`
- argv:

```
D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe
D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_flag_ledger_v1.py
--db
warehouse_dsdev_20261004_3src
--dataset
ds_20261006_dev1b
--dataset-dir
D:\MSE\CAPSTONE\outputs\datasets\ds_20261006_dev1b
--expected-samples-sha256
1a4a099ac6d11fcced901e662987f78e7f03e79c60911eb092b19213e693dce1
--expected-rows
146431
--snapshot-versions
ds_20261006_dev1b,ds_20261006_dev3b
--expected-old-code-sha256
562d22af69cb66b9d2c5bbbadbff1d325bc447cc9ffdb5a5ddf102ebf75f278d
--expected-config-sha256
ds_20261006_dev1b=6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928
--expected-config-sha256
ds_20261006_dev3b=45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8
--run
--gpt-approval
D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\ph1_producer_approval_STANDIN.md
--crawl-evidence
D:\MSE\CAPSTONE\outputs\repair-run-20261009\live_crawl_idle_evidence.json
--budget-seconds
600
--max-queries
200
--max-batches
160
```

- required_files (byte-closure):

  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_parity_v1.py` -> `1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_audit_v3.py` -> `491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_flag_ledger_v1.py` -> `a6968c0ff7a15bed7e91833933359bf2b2fa05f6cf9201539068add9b44ab67c`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\core\config.py` -> `aa137c180bf3609d88762ff518fc939b56214207e27113bff2c3f8905c8cd08e`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\connection.py` -> `0bd84c6594cc3abf599caafec10315ee9342bbd4736d59926fab63c4461ceb87`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\errors.py` -> `12395804009249f33eb1d44aa82ffe2385eaa1f368a388c595e697eda6f709be`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\naming.py` -> `af981a810d1e63cfa29bd19eecf6b3802e4bfd36bb93982de123a4b9232141f9`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe` -> `0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd`
  - `D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\ph1_producer_approval_STANDIN.md` -> `e611b74b9621fd5fed40fca79e15c790b4b52fcd9158245b7fc70642b06ccda9`

- outputs: `[]`

### 2. `ph1_old_ledger_h3` — kind `readonly`, deadline 900.0 s

- spec_sha256: `d5eefba891dc7f21076979ced736767b71470828991533b8a8481725fd01d553`
- argv_sha256: `4865d9d46fcc8e33096c0c41573f7ff84cd7fa3d5c2162ab3ddf445f93e16372`
- cwd: `D:\MSE\CAPSTONE\hotel-price-intelligence\backend`
- env (ten + gia tri, khong secret): `{"PATH": "C:\\Program Files\\Git\\cmd;C:\\Windows\\System32;C:\\Windows", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "SystemRoot": "C:\\Windows", "TEMP": "C:\\Windows\\Temp", "TMP": "C:\\Windows\\Temp"}`
- argv:

```
D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe
D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_flag_ledger_v1.py
--db
warehouse_dsdev_20261004_3src
--dataset
ds_20261006_dev3b
--dataset-dir
D:\MSE\CAPSTONE\outputs\datasets\ds_20261006_dev3b
--expected-samples-sha256
c0a3aef94e9038578b8dacc802c2d04fa59f44c3629d1c23456571f9cc7d1d03
--expected-rows
146431
--snapshot-versions
ds_20261006_dev1b,ds_20261006_dev3b
--expected-old-code-sha256
562d22af69cb66b9d2c5bbbadbff1d325bc447cc9ffdb5a5ddf102ebf75f278d
--expected-config-sha256
ds_20261006_dev1b=6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928
--expected-config-sha256
ds_20261006_dev3b=45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8
--run
--gpt-approval
D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\ph1_producer_approval_STANDIN.md
--crawl-evidence
D:\MSE\CAPSTONE\outputs\repair-run-20261009\live_crawl_idle_evidence.json
--budget-seconds
600
--max-queries
200
--max-batches
160
```

- required_files (byte-closure):

  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_parity_v1.py` -> `1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_audit_v3.py` -> `491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\warehouse_flag_ledger_v1.py` -> `a6968c0ff7a15bed7e91833933359bf2b2fa05f6cf9201539068add9b44ab67c`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\core\config.py` -> `aa137c180bf3609d88762ff518fc939b56214207e27113bff2c3f8905c8cd08e`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\connection.py` -> `0bd84c6594cc3abf599caafec10315ee9342bbd4736d59926fab63c4461ceb87`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\errors.py` -> `12395804009249f33eb1d44aa82ffe2385eaa1f368a388c595e697eda6f709be`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\backend\app\warehouse\naming.py` -> `af981a810d1e63cfa29bd19eecf6b3802e4bfd36bb93982de123a4b9232141f9`
  - `D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe` -> `0e818a1f9a0b8fbd4e7cc458a07cb7de2ea02ea326e387699a33b92f151242cd`
  - `D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\ph1_producer_approval_STANDIN.md` -> `e611b74b9621fd5fed40fca79e15c790b4b52fcd9158245b7fc70642b06ccda9`

- outputs: `[]`


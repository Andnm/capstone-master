"""Huan luyen/danh gia hoi quy gia ngan han (Phase 4, CLAUDE.md muc 3 + 9).

Doc `samples.parquet` do dataset builder xuat (khong doc DB), chay baseline (persistence / trung vi theo nhom / Ridge),
Random Forest va XGBoost voi RandomizedSearchCV -> GridSearchCV tren cross-validation THEO THOI GIAN co purge. Chon mo hinh
theo VALIDATION; test chi tinh mot lan cho mo hinh da chon + baseline. Khong dung test de tune.
"""

TRAINING_VERSION = "training-1.1.0"   # 1.1.0: test-once theo model duoc chon, mau so primary theo horizon, provenance aggregate + run transaction

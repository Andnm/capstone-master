"""Nap backend (`app.*`) vao sys.path + `.env`, giong `eda/src/db.py` - ml/ chay bang `eda/.venv`
(pyarrow, pandas) nhung dung lai ket noi/hashing cua backend, khong viet lai logic pin timezone.

Backend doc config qua bien moi truong, khong qua `.env` tuong doi CWD, nen nap `backend/.env` vao
`os.environ` (khong ghi de bien da co) TRUOC khi import bat ky module `app.*`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = ML_DIR.parent                      # .../hotel-price-intelligence
REPO_ROOT = PROJECT_DIR.parent                   # .../CAPSTONE
BACKEND_DIR = PROJECT_DIR / "backend"
DATASET_OUTPUT_ROOT = REPO_ROOT / "outputs" / "datasets"
HOLIDAYS_CSV = PROJECT_DIR / "data" / "vn_holidays.csv"

_ready = False


def ensure_backend_importable() -> None:
    """Idempotent: them `backend/` vao `sys.path` va nap `backend/.env` dung 1 lan cho ca process."""
    global _ready
    if _ready:
        return
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from dotenv import load_dotenv  # import cuc bo: chi can luc thuc su ket noi

    load_dotenv(BACKEND_DIR / ".env", override=False)
    _ready = True


ensure_backend_importable()

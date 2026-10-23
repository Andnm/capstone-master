"""Xuất MỘT LẦN metadata hotel tối thiểu từ DB vận hành (CHỈ ĐỌC) ra JSON offline có checksum, để builder snapshot dùng mà API KHÔNG cần DB.

Một câu SELECT duy nhất (xem app/ml_serving/hotel_meta.py), phiên `READ ONLY`, `time_zone='+00:00'`. Không ghi vào DB. Không ghi đè file có sẵn.

    C:\\Users\\ADMIN\\anaconda3\\python.exe -B scripts\\export_hotel_metadata.py --out <đường dẫn .json>

Kết nối đọc thông số từ `backend/.env` (chỉ script xuất này, không phải API/builder). Không in mật khẩu.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ml_serving.hotel_meta import SELECT, build_payload, write_payload  # noqa: E402


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"đã tồn tại, không ghi đè: {args.out}")

    import mysql.connector                                                  # import lười: chỉ script xuất này cần

    env = read_env(BACKEND / ".env")
    conn = mysql.connector.connect(host=env.get("DB_HOST", "localhost"), port=int(env.get("DB_PORT", "3306")), user=env["DB_USER"],
                                   password=env["DB_PASSWORD"], database=env["DB_NAME"], connection_timeout=5)
    try:
        cur = conn.cursor()
        cur.execute("SET time_zone='+00:00'")
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute(SELECT)
        rows = cur.fetchall()
        cur.execute("SELECT UTC_TIMESTAMP()")
        exported_at = cur.fetchone()[0]
    finally:
        conn.close()
    payload = build_payload(rows, exported_at)
    write_payload(args.out, payload)
    print(f"ok rows={payload['rowCount']} hotelsSha256={payload['hotelsSha256']} file={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

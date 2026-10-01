"""Holiday CSV loader + aggregation (EDA_CURATED_PLAN.md muc 6, muc 7.6).

Doc truc tiep `data/vn_holidays.csv` (KHONG chay `scripts/import_holidays.py` - script do tro database
van hanh mac dinh qua `get_db_connection()`, chua co guard `--database warehouse_*`, xem muc 7.6). Pin
SHA-256 cua chinh file CSV vao `input_manifest.json` - khong tin cache/ban sao nao khac.

Calendar feature CHINH dung `holiday_date = checkin_date` (nhu cau tai ngay nhan phong - muc 7.6 sua
doi cuoi cua GPT o file 05, KHONG hard-code range ngay theo `vn_observation_date`). Neu can phan tich
rieng tac dong vao ngay crawl thi dung ham `*_observation_day` - hai date role KHONG duoc tron.

Aggregate LUON xay ra TRUOC khi tra ve cho ben goi join tiep vao observation - 1 dong DUY NHAT cho moi
`(date, city)`, du 1 ngay co bao nhieu event chong nhau, de khong nhan dong observation sau join.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

EXPECTED_COLUMNS = (
    "holiday_date", "event_code", "name", "event_type", "scope", "city", "is_tet", "status", "source_url",
)
VALID_EVENT_TYPES = {"public_holiday", "festival", "major_event"}
VALID_STATUSES = {"confirmed", "provisional"}
# CLAUDE.md muc 2 / cot hotels.city trong warehouse - dung DUNG 5 gia tri nay de JOIN duoc.
VALID_CITIES: tuple[str, ...] = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")

_FLAG_COLUMNS = ("is_public_holiday", "is_tet", "is_festival_period", "is_major_event")
_COUNT_COLUMNS = ("holiday_event_count", "confirmed_event_count", "provisional_event_count")


@dataclass(frozen=True)
class HolidayCsv:
    path: Path
    sha256: str
    events: pd.DataFrame  # 1 dong / event, DA validate (chua aggregate)


def load_holiday_csv(path: Path) -> HolidayCsv:
    """Doc + validate CSV. FAIL CLOSED: sai cot, sai enum, trung `(holiday_date, event_code)`,
    scope/city mau thuan - phan anh dung CHECK constraint cua bang `vn_holidays` that
    (`app/database/setup.sql`), khong dua vao viec import vao DB moi phat hien loi."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"khong tim thay {path}")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != list(EXPECTED_COLUMNS):
            raise ValueError(f"{path.name} sai cot: {reader.fieldnames!r}, ky vong {EXPECTED_COLUMNS!r}")
        seen: set[tuple[str, str]] = set()
        for line_no, raw in enumerate(reader, start=2):
            holiday_date_text = (raw["holiday_date"] or "").strip()
            try:
                holiday_date = dt.date.fromisoformat(holiday_date_text)
            except ValueError as exc:
                raise ValueError(f"{path.name} dong {line_no}: holiday_date={holiday_date_text!r} sai dinh dang") from exc
            event_code = (raw["event_code"] or "").strip()
            key = (holiday_date_text, event_code)
            if key in seen:
                raise ValueError(f"{path.name} dong {line_no}: trung khoa (holiday_date, event_code)={key}")
            seen.add(key)

            event_type = (raw["event_type"] or "").strip()
            if event_type not in VALID_EVENT_TYPES:
                raise ValueError(f"{path.name} dong {line_no}: event_type={event_type!r} khong hop le")
            status = (raw["status"] or "").strip()
            if status not in VALID_STATUSES:
                raise ValueError(f"{path.name} dong {line_no}: status={status!r} khong hop le")
            scope = (raw["scope"] or "").strip()
            city = (raw["city"] or "").strip() or None
            if scope == "national" and city is not None:
                raise ValueError(f"{path.name} dong {line_no}: scope=national nhung city={city!r} khong rong")
            if scope == "city":
                if city is None:
                    raise ValueError(f"{path.name} dong {line_no}: scope=city nhung city rong")
                if city not in VALID_CITIES:
                    raise ValueError(f"{path.name} dong {line_no}: city={city!r} ngoai 5 thanh pho scope")
            elif scope != "national":
                raise ValueError(f"{path.name} dong {line_no}: scope={scope!r} khong hop le")
            is_tet_text = (raw["is_tet"] or "").strip()
            if is_tet_text not in {"0", "1"}:
                raise ValueError(f"{path.name} dong {line_no}: is_tet={is_tet_text!r} phai la '0' hoac '1'")

            rows.append({
                "holiday_date": holiday_date, "event_code": event_code, "name": (raw["name"] or "").strip(),
                "event_type": event_type, "scope": scope, "city": city, "is_tet": is_tet_text == "1",
                "status": status, "source_url": (raw["source_url"] or "").strip(),
            })
    if not rows:
        raise ValueError(f"{path.name} rong - khong doc duoc event nao")
    events = pd.DataFrame(rows).sort_values(["holiday_date", "event_code"]).reset_index(drop=True)
    return HolidayCsv(path=path, sha256=sha256, events=events)


def _empty_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in _FLAG_COLUMNS:
        out[col] = False
    for col in _COUNT_COLUMNS:
        out[col] = 0
    return out


def calendar_flags_by_date_city(events: pd.DataFrame, *, dates: Iterable, cities: tuple = VALID_CITIES) -> pd.DataFrame:
    """1 dong DUY NHAT cho moi `(date, city)` trong tich `dates` x `cities`. National event ap dung
    ca 5 thanh pho; city event chi ap dung dung thanh pho cua no. Frame dung tu tap `dates` thuc su can
    (KHONG hard-code mot con so ngay co dinh, xem muc 7.6 sua doi cuoi cua GPT)."""
    unique_dates = sorted({d if isinstance(d, dt.date) else pd.Timestamp(d).date() for d in dates})
    frame = pd.DataFrame([(d, c) for d in unique_dates for c in cities], columns=["holiday_date", "city"])
    if not unique_dates:
        return _empty_flags(frame)

    national = events[events["scope"] == "national"]
    city_scoped = events[events["scope"] == "city"]

    if len(national):
        national_exploded = national.drop(columns=["city"]).merge(pd.DataFrame({"city": list(cities)}), how="cross")
    else:
        national_exploded = pd.DataFrame(columns=[*national.columns])

    matched = pd.concat([national_exploded, city_scoped], ignore_index=True, sort=False)
    matched = matched.merge(frame, on=["holiday_date", "city"], how="inner")

    if matched.empty:
        return _empty_flags(frame)

    grouped = matched.groupby(["holiday_date", "city"], as_index=False).agg(
        is_public_holiday=("event_type", lambda s: bool((s == "public_holiday").any())),
        is_tet=("is_tet", "any"),
        is_festival_period=("event_type", lambda s: bool((s == "festival").any())),
        is_major_event=("event_type", lambda s: bool((s == "major_event").any())),
        holiday_event_count=("event_code", "count"),
        confirmed_event_count=("status", lambda s: int((s == "confirmed").sum())),
        provisional_event_count=("status", lambda s: int((s == "provisional").sum())),
    )
    out = frame.merge(grouped, on=["holiday_date", "city"], how="left")
    for col in _FLAG_COLUMNS:
        out[col] = out[col].fillna(False).astype(bool)
    for col in _COUNT_COLUMNS:
        out[col] = out[col].fillna(0).astype("int64")
    return out


def checkin_calendar_flags(events: pd.DataFrame, checkin_dates: Iterable, cities: tuple = VALID_CITIES) -> pd.DataFrame:
    """Vai tro date CHINH cua muc 7.6: `holiday_date = checkin_date` (nhu cau tai ngay nhan phong)."""
    out = calendar_flags_by_date_city(events, dates=checkin_dates, cities=cities)
    return out.rename(columns={"holiday_date": "checkin_date"})


def observation_day_calendar_flags(events: pd.DataFrame, observation_dates: Iterable,
                                   cities: tuple = VALID_CITIES) -> pd.DataFrame:
    """Vai tro date PHU cua muc 7.6: `holiday_date = vn_observation_date`. Moi cot mang suffix
    `_observation_day` de khong tron voi bo cot cua `checkin_calendar_flags` khi ghep chung 1 bang."""
    out = calendar_flags_by_date_city(events, dates=observation_dates, cities=cities)
    out = out.rename(columns={"holiday_date": "vn_observation_date"})
    rename = {c: f"{c}_observation_day" for c in out.columns if c not in ("vn_observation_date", "city")}
    return out.rename(columns=rename)

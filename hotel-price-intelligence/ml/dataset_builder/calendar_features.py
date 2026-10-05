"""Feature lich + `vn_holidays` (CLAUDE.md muc 5.1) - tinh tren NGAY CHECK-IN (nhu cau tai ngay nhan phong).

Doc truc tiep `data/vn_holidays.csv` (SHA-256 duoc ghim vao build report); event `scope=city` chi ap dung cho khach
san thuoc dung thanh pho do. `status=provisional` van la thong tin biet truoc (lich/le hoi du kien), duoc ghi nhan
trong data dictionary; khong phai du lieu tuong lai bi ro ri (la lich cong bo/du kien truoc ngay quan sat).

Dinh nghia (checkin D, thanh pho C):
  is_weekend              = D la thu 6 hoac thu 7 (CLAUDE.md: "thu 6, 7")  [Python weekday 4,5]
  is_public_holiday       = co event_type=public_holiday tai D (national hoac city=C)
  is_tet_period           = co event is_tet=1 tai D
  is_festival_period      = co event_type festival hoac major_event tai D (national hoac city=C)
  is_holiday_eve          = ngay D+1 la public_holiday
  days_to_nearest_holiday = khoang cach ngay (>=0, hai phia) toi public_holiday gan nhat; NULL neu lich khong co
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

from . import env

EXPECTED_COLUMNS = ("holiday_date", "event_code", "name", "event_type", "scope", "city", "is_tet", "status", "source_url")
FESTIVAL_TYPES = ("festival", "major_event")


@dataclass
class CalendarFeatures:
    csv_path: Path
    sha256: str
    # (ngay) -> list[(event_type, scope, city, is_tet)]
    events: dict[dt.date, list[tuple[str, str, str | None, bool]]] = field(default_factory=dict)
    # thanh pho (hoac None cho national) -> danh sach ngay public_holiday da sap xep
    _public_by_city: dict[str | None, list[dt.date]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "CalendarFeatures":
        path = Path(path or env.HOLIDAYS_CSV)
        raw = path.read_bytes()
        instance = cls(csv_path=path, sha256=hashlib.sha256(raw).hexdigest())
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if list(reader.fieldnames or []) != list(EXPECTED_COLUMNS):
                raise ValueError(f"{path.name} sai cot: {reader.fieldnames!r}")
            for row in reader:
                day = dt.date.fromisoformat(row["holiday_date"].strip())
                city = (row["city"] or "").strip() or None
                scope = row["scope"].strip()
                if (scope == "national") != (city is None):
                    raise ValueError(f"{path.name}: scope/city mau thuan o {row['holiday_date']} {row['event_code']}")
                instance.events.setdefault(day, []).append((row["event_type"].strip(), scope, city, row["is_tet"].strip() == "1"))
        cities = {None} | {e[2] for events in instance.events.values() for e in events if e[2]}
        for city in cities:
            instance._public_by_city[city] = sorted(
                day for day, events in instance.events.items()
                if any(t == "public_holiday" and (c is None or c == city) for t, _s, c, _tet in events))
        return instance

    def _applicable(self, day: dt.date, city: str | None) -> list[tuple[str, str, str | None, bool]]:
        return [e for e in self.events.get(day, []) if e[2] is None or e[2] == city]

    def _nearest_public(self, day: dt.date, city: str | None) -> int | None:
        days = sorted(set(self._public_by_city.get(None, [])) | set(self._public_by_city.get(city, [])))
        if not days:
            return None
        index = bisect_left(days, day)
        candidates = []
        if index < len(days):
            candidates.append((days[index] - day).days)
        if index > 0:
            candidates.append((day - days[index - 1]).days)
        return min(candidates)

    def features(self, checkin: dt.date, city: str | None) -> dict[str, object]:
        today = self._applicable(checkin, city)
        tomorrow = self._applicable(checkin + dt.timedelta(days=1), city)
        weekday = checkin.weekday()
        return {
            "day_of_week": weekday,
            "is_weekend": weekday in (4, 5),
            "month": checkin.month,
            "week_of_year": checkin.isocalendar()[1],
            "quarter": (checkin.month - 1) // 3 + 1,
            "is_public_holiday": any(e[0] == "public_holiday" for e in today),
            "is_holiday_eve": any(e[0] == "public_holiday" for e in tomorrow),
            "days_to_nearest_holiday": self._nearest_public(checkin, city),
            "is_tet_period": any(e[3] for e in today),
            "is_festival_period": any(e[0] in FESTIVAL_TYPES for e in today),
        }

"""Cohort manifest - nguon su that DUY NHAT cho `hotels.city` o warehouse (muc 6 rule 5).

Muc 6: "`city`/cohort **luon luon** lay tu cohort manifest, khong bao gio tu `hotels.city` nguon
nao". Ly do: moi nguon tu suy city tu address/sheet cua rieng no, 2 nguon co the bat dong; con
cohort manifest la file nguoi van hanh kiem soat, dung de dinh nghia quan the nghien cuu.

File nguon: `link_hotel_data_expanded.xlsx` o goc repo - 5 sheet, ten sheet CHINH LA ten thanh pho
(dung y het gia tri can co trong `hotels.city`), moi sheet 2 cot: ten khach san + link Booking.
`hotel_id` suy tu link bang `extract_hotel_slug()` cua chinh scraper - KHONG viet lai logic slug.

`cohort_manifest_sha256` tinh tu NOI DUNG da chuan hoa (danh sach (hotel_id, city) sap xep), khong
phai tu bytes cua file .xlsx - file Excel doi bytes moi lan mo/luu du noi dung khong doi.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.scraper.url_utils import extract_hotel_slug

from .errors import ManifestError
from .hashing import canonical_json, sha256_hex

# CLAUDE.md muc 2/7.2 - 5 thanh pho trong scope, viet DUNG nhu vay de JOIN thang voi hotels.city.
VALID_CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")

_LINK_HEADER_CANDIDATES = ("link", "url")


@dataclass(frozen=True)
class CohortManifest:
    """`hotel_city` la MappingProxyType - `frozen=True` chi chan gan lai field, KHONG chan sua dict
    ben trong. Neu de dict mutable thi `manifest_sha256` va `city_of()` co the lech nhau sau khi
    load (GPT review 06 MINOR 4)."""

    path: Path
    hotel_city: Mapping[str, str]

    @property
    def manifest_sha256(self) -> str:
        return sha256_hex(canonical_json(sorted(self.hotel_city.items())))

    @property
    def size(self) -> int:
        return len(self.hotel_city)

    def city_of(self, hotel_id: str) -> str | None:
        """None = hotel KHONG thuoc cohort -> warehouse ghi city=NULL (muc 6 rule 5, chot o file 02)."""
        return self.hotel_city.get(hotel_id)

    def contains(self, hotel_id: str) -> bool:
        return hotel_id in self.hotel_city


def load_cohort_manifest(path: str | Path, *, require_all_cities: bool = True) -> CohortManifest:
    """Doc workbook cohort -> {hotel_id: city}. FAIL CLOSED tren moi bat thuong.

    `require_all_cities=True` (mac dinh cho moi duong chay that): thieu 1 sheet thanh pho la FAIL,
    khong phai canh bao. Neu khong, xoa nham 1 sheet se lam scope dataset AM THAM co lai tu 5 thanh
    pho xuong 4 ma khong ai phat hien (GPT review 06 MINOR 4). Chi test fixture moi truyen False.
    """
    import openpyxl  # import cuc bo: chi lenh warehouse can openpyxl, worker crawl thi khong

    workbook_path = Path(path)
    if not workbook_path.exists():
        raise ManifestError(f"khong tim thay cohort manifest: {workbook_path}")

    workbook = openpyxl.load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        unknown_sheets = [name for name in workbook.sheetnames if name not in VALID_CITIES]
        if unknown_sheets:
            raise ManifestError(
                f"cohort manifest co sheet khong phai thanh pho trong scope: {unknown_sheets}. "
                f"Chi chap nhan dung {list(VALID_CITIES)} (CLAUDE.md muc 2)."
            )
        missing_cities = [city for city in VALID_CITIES if city not in workbook.sheetnames]
        if require_all_cities and missing_cities:
            raise ManifestError(
                f"cohort manifest THIEU sheet thanh pho: {missing_cities}. Scope de tai co dinh 5 "
                f"thanh pho (CLAUDE.md muc 2) - thieu sheet se lam scope dataset am tham co lai."
            )

        hotel_city: dict[str, str] = {}
        duplicates: list[tuple[str, str, str]] = []
        for city in workbook.sheetnames:
            sheet = workbook[city]
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            link_index = _find_link_column(header, city)
            for row_number, row in enumerate(rows, start=2):
                if row is None or all(cell is None for cell in row):
                    continue
                raw_link = row[link_index] if link_index < len(row) else None
                if raw_link is None or not str(raw_link).strip():
                    raise ManifestError(
                        f"cohort manifest sheet {city!r} dong {row_number}: thieu link Booking - "
                        f"khong suy duoc hotel_id."
                    )
                hotel_id = extract_hotel_slug(str(raw_link))
                if not hotel_id:
                    raise ManifestError(
                        f"cohort manifest sheet {city!r} dong {row_number}: khong parse duoc slug tu "
                        f"link {str(raw_link)[:80]!r}."
                    )
                if hotel_id in hotel_city:
                    duplicates.append((hotel_id, hotel_city[hotel_id], city))
                    continue
                hotel_city[hotel_id] = city
    finally:
        workbook.close()

    if duplicates:
        raise ManifestError(
            f"cohort manifest co hotel_id xuat hien o nhieu sheet (khong the xac dinh city): "
            f"{duplicates[:10]}"
        )
    if not hotel_city:
        raise ManifestError("cohort manifest rong - khong doc duoc hotel nao.")
    return CohortManifest(
        path=workbook_path.resolve(), hotel_city=MappingProxyType(dict(hotel_city))
    )


def _find_link_column(header: tuple | None, city: str) -> int:
    if not header:
        raise ManifestError(f"cohort manifest sheet {city!r} khong co dong header.")
    for index, cell in enumerate(header):
        if cell is None:
            continue
        text = str(cell).strip().lower()
        if any(candidate in text for candidate in _LINK_HEADER_CANDIDATES):
            return index
    raise ManifestError(
        f"cohort manifest sheet {city!r}: khong tim thay cot link trong header {header!r} "
        f"(tim theo tu khoa {_LINK_HEADER_CANDIDATES})."
    )

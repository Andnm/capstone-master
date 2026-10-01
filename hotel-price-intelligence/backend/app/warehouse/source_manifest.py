"""EXTERNAL IMMUTABLE source manifest - input co dinh cua ca batch (muc 3a buoc 1).

Dinh dang file (JSON, nguoi van hanh tu viet 1 lan cho moi batch):

```json
{
  "manifest_version": 1,
  "batch_label": "warehouse_20260915_2src",
  "sources": [
    {
      "source_code": "local_primary",
      "source_priority": 0,
      "dump_path": "hotel-price-intelligence/data/local_crawl/local_20260915_155533.sql",
      "dump_sha256": "a024...",
      "dump_taken_at": "2026-09-15T15:55:33Z",
      "schema_sha256": "09f8...",
      "source_version_json": {"scraper_version_range": ["2.1.0", "2.3.0"], "mysql_version": "8.0.45"}
    }
  ]
}
```

Bon field `dump_sha256`/`dump_taken_at`/`schema_sha256`/`source_version_json` la ket qua cua buoc
2-4, da thuc hien BEN NGOAI truoc phien build (xem `data/*/*.manifest.json` cua tung nguon).
`build_warehouse` khong tin chung: no **rehash lai dump that** va **tinh lai schema_sha256 tu chinh
dump** roi so khop; lech -> FAIL (D10 gate 3).

`source_priority` la thu tu tie-break XAC DINH khi merge `hotels` (muc 6 rule 3). KHONG duoc so
sanh chuoi `source_code` - registry la mo, ten nguon khong mang thu tu uu tien ngam dinh.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ManifestError
from .hashing import file_sha256, iso_utc, source_manifest_sha256

SOURCE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")  # khop chk_sources_code_format o muc 4
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Khoi CREATE TABLE trong dump: tu 'CREATE TABLE' toi dong ') ENGINE=...;'
_CREATE_TABLE_RE = re.compile(
    r"^CREATE TABLE .*?^\) ENGINE=.*?;$",
    re.MULTILINE | re.DOTALL,
)
# AUTO_INCREMENT=N la BO DEM hien tai cua bang, phu thuoc so dong - KHONG phai cau truc schema.
# Khong bo no thi 2 DB giong het nhau ve cau truc van ra 2 schema_sha256 khac nhau.
_AUTO_INCREMENT_RE = re.compile(r"\s*AUTO_INCREMENT=\d+")


@dataclass(frozen=True)
class SourceEntry:
    source_code: str
    source_priority: int
    dump_path: Path
    dump_sha256: str
    dump_taken_at: str
    schema_sha256: str
    source_version_json: dict[str, Any]

    def identity(self) -> dict[str, Any]:
        return {
            "source_code": self.source_code,
            "source_priority": self.source_priority,
            "dump_sha256": self.dump_sha256,
            "dump_taken_at": self.dump_taken_at,
            "schema_sha256": self.schema_sha256,
            "source_version_json": self.source_version_json,
        }


@dataclass(frozen=True)
class SourceManifest:
    path: Path
    sources: tuple[SourceEntry, ...]

    @property
    def manifest_sha256(self) -> str:
        return source_manifest_sha256(entry.identity() for entry in self.sources)

    def by_priority(self) -> tuple[SourceEntry, ...]:
        """Thu tu xu ly cua buoc 11 va thu tu gan warehouse ID (D1)."""
        return tuple(sorted(self.sources, key=lambda entry: (entry.source_priority, entry.source_code)))

    def get(self, source_code: str) -> SourceEntry:
        for entry in self.sources:
            if entry.source_code == source_code:
                return entry
        raise ManifestError(f"source_code {source_code!r} khong co trong manifest.")


def _require(entry: dict[str, Any], field: str, index: int) -> Any:
    if field not in entry:
        raise ManifestError(f"sources[{index}] thieu field bat buoc {field!r}.")
    return entry[field]


def load_source_manifest(path: str | Path, *, base_dir: str | Path | None = None) -> SourceManifest:
    """Doc + validate manifest. `base_dir` de resolve `dump_path` tuong doi (mac dinh: cwd)."""
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"khong doc duoc source manifest {manifest_path}: {exc}") from exc

    if data.get("manifest_version") != 1:
        raise ManifestError(f"manifest_version khong duoc ho tro: {data.get('manifest_version')!r}")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ManifestError("manifest phai co 'sources' la mang khong rong (muc 3a buoc 1: toi thieu 1 nguon).")

    root = Path(base_dir) if base_dir is not None else Path.cwd()
    entries: list[SourceEntry] = []
    seen_codes: set[str] = set()
    seen_priorities: dict[int, str] = {}

    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise ManifestError(f"sources[{index}] khong phai object.")
        code = _require(raw, "source_code", index)
        if not isinstance(code, str) or not SOURCE_CODE_RE.match(code):
            raise ManifestError(
                f"sources[{index}].source_code={code!r} khong khop ^[a-z][a-z0-9_]{{0,19}}$ "
                f"(chk_sources_code_format, muc 4)."
            )
        if code in seen_codes:
            raise ManifestError(f"source_code trung trong manifest: {code!r}")
        seen_codes.add(code)

        priority = _require(raw, "source_priority", index)
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise ManifestError(
                f"sources[{index}].source_priority={priority!r} phai la so nguyen >= 0 "
                f"(chk_sources_priority, muc 4)."
            )
        if priority in seen_priorities:
            raise ManifestError(
                f"source_priority={priority} bi trung giua {seen_priorities[priority]!r} va {code!r} "
                f"(uq_sources_priority, muc 4) - tie-break merge hotels se khong xac dinh."
            )
        seen_priorities[priority] = code

        for hash_field in ("dump_sha256", "schema_sha256"):
            value = _require(raw, hash_field, index)
            if not isinstance(value, str) or not _SHA256_RE.match(value):
                raise ManifestError(f"sources[{index}].{hash_field}={value!r} khong phai SHA-256 hex 64 ky tu.")

        version_json = _require(raw, "source_version_json", index)
        if not isinstance(version_json, dict):
            raise ManifestError(f"sources[{index}].source_version_json phai la object.")

        dump_path = Path(_require(raw, "dump_path", index))
        if not dump_path.is_absolute():
            dump_path = (root / dump_path).resolve()

        entries.append(SourceEntry(
            source_code=code,
            source_priority=priority,
            dump_path=dump_path,
            dump_sha256=raw["dump_sha256"],
            dump_taken_at=iso_utc(_require(raw, "dump_taken_at", index)),
            schema_sha256=raw["schema_sha256"],
            source_version_json=version_json,
        ))

    return SourceManifest(path=manifest_path.resolve(), sources=tuple(entries))


def verify_dump_checksum(entry: SourceEntry) -> str:
    """Rehash dump that va so voi manifest (D10 gate 3). Tra ve hash da tinh."""
    if not entry.dump_path.exists():
        raise ManifestError(f"nguon {entry.source_code!r}: khong tim thay dump tai {entry.dump_path}")
    actual = file_sha256(entry.dump_path)
    if actual != entry.dump_sha256:
        raise ManifestError(
            f"nguon {entry.source_code!r}: dump_sha256 KHONG KHOP.\n"
            f"  manifest: {entry.dump_sha256}\n"
            f"  file that: {actual}\n"
            f"  file: {entry.dump_path}\n"
            f"Dump da bi thay doi sau khi manifest duoc tao - dung, khong tu sua hash."
        )
    return actual


def normalize_schema_blocks(blocks: dict[str, str]) -> str:
    """Chuan hoa + sort theo ten bang -> van ban schema xac dinh de hash.

    `blocks`: {ten_bang: van ban CREATE TABLE nguyen goc}.
    """
    if not blocks:
        raise ManifestError("khong tim thay khoi CREATE TABLE nao trong dump - file co dung la mysqldump?")
    normalized = [
        _AUTO_INCREMENT_RE.sub("", blocks[name]).strip()
        for name in sorted(blocks)
    ]
    return "\n\n".join(normalized) + "\n"


def extract_schema_text(dump_text: str) -> str:
    """Ban in-memory cua `normalize_schema_blocks` - dung cho dump nho/fixture trong test."""
    found = _CREATE_TABLE_RE.findall(dump_text)
    blocks: dict[str, str] = {}
    for block in found:
        name = _table_name_of(block)
        blocks[name] = block
    return normalize_schema_blocks(blocks)


def _table_name_of(create_block: str) -> str:
    match = re.match(r"CREATE TABLE\s+`([^`]+)`", create_block)
    if not match:
        raise ManifestError(f"khong doc duoc ten bang tu khoi CREATE TABLE: {create_block[:60]!r}")
    return match.group(1)


def iter_dump_create_tables(dump_path: str | Path, *, expected: tuple[str, ...]) -> dict[str, str]:
    """Trich DDL cua cac bang `expected` tu dump bang state machine THEO DONG.

    Vi sao khong doc ca file roi regex: mysqldump ghi DDL cua bang N ngay TRUOC du lieu cua bang N,
    nen `CREATE TABLE price_observations` nam sau toan bo INSERT cua `crawl_run_items` - khong the
    chi doc phan dau file, va doc het 726 MB vao RAM roi regex thi vua ton bo nho vua O(n^2).

    State machine chi giu 1 khoi trong bo nho va DUNG NGAY khi da du so bang can (~76 MB dau file
    voi dump that), nen khong bao gio phai doc het file.
    """
    wanted = set(expected)
    blocks: dict[str, str] = {}
    current: list[str] | None = None
    with open(dump_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if current is None:
                if line.startswith("CREATE TABLE "):
                    current = [line]
                continue
            current.append(line)
            if line.startswith(") ENGINE="):
                block = "".join(current).rstrip()
                current = None
                name = _table_name_of(block)
                if name in wanted:
                    blocks[name] = block
                    if len(blocks) == len(wanted):
                        break
    missing = wanted - set(blocks)
    if missing:
        raise ManifestError(
            f"dump {dump_path} thieu DDL cua bang: {sorted(missing)} (tim thay: {sorted(blocks)})."
        )
    return blocks


def compute_schema_sha256_from_dump(
    dump_path: str | Path, *, expected: tuple[str, ...] | None = None
) -> tuple[str, str]:
    """Tinh lai `schema_sha256` tu chinh dump - buoc 4, duoc re-verify moi lan build."""
    from .etl_ddl import CORE_TABLES
    from .hashing import sha256_hex  # import cuc bo de tranh vong lap import o module level

    blocks = iter_dump_create_tables(dump_path, expected=expected or CORE_TABLES)
    schema_text = normalize_schema_blocks(blocks)
    return sha256_hex(schema_text), schema_text


def verify_schema_checksum(entry: SourceEntry) -> tuple[str, str]:
    """Tinh lai schema_sha256 tu dump va so voi manifest. Tra (hash, schema_text)."""
    actual, schema_text = compute_schema_sha256_from_dump(entry.dump_path)
    if actual != entry.schema_sha256:
        raise ManifestError(
            f"nguon {entry.source_code!r}: schema_sha256 KHONG KHOP.\n"
            f"  manifest: {entry.schema_sha256}\n"
            f"  tinh lai tu dump: {actual}\n"
            f"Schema trong dump khac voi luc manifest duoc tao."
        )
    return actual, schema_text

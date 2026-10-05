"""Giao dich cho mot run huan luyen (GPT review vong 2 R2-M3 + R2-m2). Mot run chi xuat hien o ten cuoi cung khi da PASS tron ven.

Luong: kiem dataset/provenance TRUOC khi tao bat ky thu muc nao -> xay trong thu muc tam anh em `.<run_id>.tmp-<uuid>` (co `run_manifest.json` state
`running`, cap nhat nguyen tu sau moi horizon) -> loi/ngat giua chung: state `fail` + ly do, doi ten thanh `<run_id>.failed-<ts>-<uuid>` (giu lam bang chung,
khong bao gio la run hop le) -> moi horizon du kien xong: ghi checksum tung file + state `pass` roi doi ten nguyen tu thu muc tam -> `<run_id>`.
Thu muc `<run_id>` da ton tai => tu choi (khong ghi de). Process bi giet giua chung chi de lai thu muc tam `running` - khong bao gio duoc coi la run.
`verify_run_dir` la cong doc: official chi chap nhan run co manifest `pass` + moi output khop checksum + du bao cao cac horizon da khai bao.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

MANIFEST = "run_manifest.json"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_MODELS = ("ridge", "rf", "xgb")


class RunExistsError(RuntimeError):
    pass


class RunStateError(RuntimeError):
    pass


class ArgumentError(ValueError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- tham so CLI (R2-m2)

def parse_selection(raw: str | None, *, name: str, allowed: tuple[Any, ...], default: list[Any] | None, cast=str) -> list[Any]:
    """Chuoi `a,b,c` -> danh sach hop le. De trong => `default`; chuoi khong rong nhung khong co phan tu (vd `,,`), phan tu ngoai `allowed`
    hoac trung lap => ArgumentError (khong am tham chay baselines-only vi go nham)."""
    if raw is None or raw.strip() == "":
        if default is None:
            raise ArgumentError(f"--{name} khong duoc de trong.")
        return list(default)
    items: list[Any] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            items.append(cast(token))
        except ValueError as exc:
            raise ArgumentError(f"--{name}: gia tri {token!r} khong hop le ({exc}).") from exc
    if not items:
        raise ArgumentError(f"--{name}={raw!r} khong chua gia tri nao.")
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ArgumentError(f"--{name}: {unknown} ngoai tap cho phep {list(allowed)}.")
    if len(set(items)) != len(items):
        raise ArgumentError(f"--{name}: co gia tri trung lap {items}.")
    return items


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.match(run_id):
        raise ArgumentError(f"--run-id {run_id!r} khong hop le: dung chu/so/._- (<=64 ky tu), khong bat dau bang '.'.")
    if ".tmp-" in run_id or ".failed-" in run_id:
        raise ArgumentError(f"--run-id {run_id!r} trung quy uoc ten thu muc tam/that bai.")
    return run_id


# --------------------------------------------------------------------------- giao dich

class RunTransaction:
    def __init__(self, dataset_root: Path, run_id: str, base_manifest: dict[str, Any], expected_horizons: list[int]) -> None:
        self.dataset_root, self.run_id = Path(dataset_root), validate_run_id(run_id)
        self.base_manifest, self.expected_horizons = dict(base_manifest), list(expected_horizons)
        self.final_dir = self.dataset_root / run_id
        self.tmp_dir = self.dataset_root / f".{run_id}.tmp-{uuid.uuid4().hex[:12]}"
        self.manifest: dict[str, Any] = {}
        self.started = False

    def start(self) -> Path:
        if self.final_dir.exists():
            raise RunExistsError(f"thu muc run da ton tai: {self.final_dir} - dung --run-id moi, khong ghi de artifact cu.")
        self.dataset_root.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir()
        self.manifest = {**self.base_manifest, "run_id": self.run_id, "state": "running", "started_at": _now(), "finished_at": None, "error": None,
                         "expected_horizons": self.expected_horizons, "horizons": [], "outputs": None}
        self.started = True
        self._save()
        return self.tmp_dir

    def _save(self) -> None:
        _write_json(self.tmp_dir / MANIFEST, self.manifest)

    def record_horizon(self, summary: dict[str, Any]) -> None:
        if not self.started or self.manifest.get("state") != "running":
            raise RunStateError("record_horizon chi hop le khi run dang `running`.")
        self.manifest["horizons"].append(summary)
        self._save()

    def fail(self, error: str) -> Path:
        """State `fail` + ly do, doi ten sang `<run_id>.failed-...` (giu bang chung). Khong bao gio tao ten cuoi cung."""
        if not self.started:
            raise RunStateError("run chua start.")
        self.manifest.update(state="fail", error=str(error)[:4000], finished_at=_now())
        self._save()
        failed = self.dataset_root / f"{self.run_id}.failed-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        os.replace(self.tmp_dir, failed)
        self.tmp_dir = failed
        return failed

    def commit(self) -> Path:
        """Du moi horizon du kien + moi bao cao ton tai -> ghi checksum tung file, state `pass`, doi ten nguyen tu sang ten cuoi cung."""
        if not self.started or self.manifest.get("state") != "running":
            raise RunStateError("commit chi hop le khi run dang `running`.")
        done = [entry.get("horizon") for entry in self.manifest["horizons"]]
        if sorted(done) != sorted(self.expected_horizons):
            raise RunStateError(f"run chua xong het horizon: da xong {done}, du kien {self.expected_horizons}")
        missing = [h for h in self.expected_horizons if not (self.tmp_dir / f"h{h}_report.json").is_file()]
        if missing:
            raise RunStateError(f"thieu h*_report.json cua horizon {missing}")
        self.manifest["outputs"] = _checksums(self.tmp_dir)
        self.manifest.update(state="pass", finished_at=_now())
        self._save()
        if self.final_dir.exists():                       # kiem lai sat luc doi ten (co the run khac vua xong cung ten)
            raise RunExistsError(f"thu muc run da ton tai: {self.final_dir}")
        os.replace(self.tmp_dir, self.final_dir)
        self.tmp_dir = self.final_dir
        return self.final_dir

    def discard_if_unstarted(self) -> None:
        if self.tmp_dir.exists() and self.manifest.get("state") is None:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)


def _checksums(directory: Path) -> dict[str, str]:
    """sha256 cua MOI file trong thu muc run (tru chinh run_manifest.json va file .tmp)."""
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != MANIFEST and not path.name.endswith(".tmp"):
            result[path.relative_to(directory).as_posix()] = _sha256(path)
    return result


def verify_run_dir(run_dir: Path | str) -> list[str]:
    """Danh sach sai lech cua mot run (rong = hop le): manifest `pass`, du horizon, moi output khop checksum, khong file la."""
    run_dir = Path(run_dir)
    problems: list[str] = []
    path = run_dir / MANIFEST
    if not path.is_file():
        return [f"thieu {MANIFEST}"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{MANIFEST} khong doc duoc JSON: {exc}"]
    if manifest.get("state") != "pass":
        problems.append(f"state={manifest.get('state')!r} != 'pass'")
    declared = manifest.get("outputs")
    if not isinstance(declared, dict) or not declared:
        return problems + ["manifest khong co `outputs` (checksum tung file)"]
    actual = _checksums(run_dir)
    for name, sha in declared.items():
        if name not in actual:
            problems.append(f"thieu output {name}")
        elif actual[name] != sha:
            problems.append(f"output {name} lech checksum")
    extra = sorted(set(actual) - set(declared))
    if extra:
        problems.append(f"file khong co trong checksum: {extra[:5]}")
    expected = manifest.get("expected_horizons") or []
    done = sorted(entry.get("horizon") for entry in manifest.get("horizons", []))
    if sorted(expected) != done:
        problems.append(f"horizon da xong {done} != du kien {sorted(expected)}")
    for h in expected:
        if f"h{h}_report.json" not in declared:
            problems.append(f"thieu h{h}_report.json trong checksum")
    return problems

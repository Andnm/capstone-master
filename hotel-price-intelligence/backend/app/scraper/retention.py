"""Retention an toàn cho upload/artifact; không bao giờ xoá file của job queued/running."""
from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List

from app.core.config import settings
from app.core.database import get_db_connection
from app.scraper.data_contract import utc_now_naive


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _log(cleanup_type: str, path: Path, action: str, reason: str) -> None:
    size = path.stat().st_size if path.exists() and path.is_file() else 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO file_cleanup_logs(cleanup_type,file_path,file_size,action,reason) VALUES(%s,%s,%s,%s,%s)",
            (cleanup_type, str(path), size, action, reason[:500]),
        )
        conn.commit()
        cursor.close()


def _run_paths() -> Dict[str, dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT source_file,MAX(created_at) last_used,
              SUM(status IN ('queued','running')) active_count
            FROM crawl_runs WHERE source_file IS NOT NULL GROUP BY source_file
            """
        )
        rows = cursor.fetchall()
        cursor.close()
    return {str(Path(row["source_file"]).resolve()): row for row in rows}


def _active_artifacts() -> set[str]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT artifact_html_path FROM crawl_run_items
            WHERE status IN ('queued','running') AND artifact_html_path IS NOT NULL
            UNION
            SELECT screenshot_path FROM crawl_run_items
            WHERE status IN ('queued','running') AND screenshot_path IS NOT NULL
            """
        )
        paths = {str(Path(row[0]).resolve()) for row in cursor.fetchall()}
        cursor.close()
    return paths


def cleanup_files(*, apply: bool = False) -> List[dict]:
    now = utc_now_naive()
    upload_cutoff = now - timedelta(days=settings.UPLOAD_RETENTION_DAYS)
    artifact_cutoff = now - timedelta(days=settings.ARTIFACT_RETENTION_DAYS)
    results = []
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    artifact_root = Path(settings.ARTIFACT_DIR).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    referenced = _run_paths()
    for path in upload_root.glob("*"):
        if not path.is_file() or not _inside(path, upload_root):
            continue
        info = referenced.get(str(path.resolve()))
        if info and info["active_count"]:
            continue
        last_used = info["last_used"] if info else None
        modified = __import__("datetime").datetime.utcfromtimestamp(path.stat().st_mtime)
        if (last_used or modified) >= upload_cutoff:
            continue
        action = "deleted" if apply else "dry_run"
        reason = f"upload quá {settings.UPLOAD_RETENTION_DAYS} ngày và không thuộc job đang chạy"
        _log("upload", path, action, reason)
        results.append({"type": "upload", "path": str(path), "action": action})
        if apply:
            path.unlink()

    active_artifacts = _active_artifacts()
    for path in artifact_root.rglob("*"):
        if not path.is_file() or not _inside(path, artifact_root):
            continue
        if str(path.resolve()) in active_artifacts:
            continue
        modified = __import__("datetime").datetime.utcfromtimestamp(path.stat().st_mtime)
        if modified >= artifact_cutoff:
            continue
        action = "deleted" if apply else "dry_run"
        reason = f"artifact quá {settings.ARTIFACT_RETENTION_DAYS} ngày"
        _log("artifact", path, action, reason)
        results.append({"type": "artifact", "path": str(path), "action": action})
        if apply:
            path.unlink()

    if apply:
        for directory in sorted(
            (p for p in artifact_root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts), reverse=True,
        ):
            if _inside(directory, artifact_root):
                try:
                    directory.rmdir()
                except OSError:
                    pass
    return results

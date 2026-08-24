import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.database.durable import DurableQueueRepository
from app.database.repositories import CrawlRunItemRepository, CrawlRunRepository, PriceObservationRepository
from app.schemas.scraper import (
    CrawlRunItemPageResponse, CrawlRunPageResponse, CrawlRunResponse, PreflightResponse,
    UploadResponse, WorkerHealthResponse,
)
from app.scraper.data_contract import current_git_commit, default_crawl_context
from app.scraper.export import build_run_export_xlsx
from app.scraper.job_runner import inspect_hotel_list_excel, parse_hotel_list_excel

router = APIRouter()
run_repo = CrawlRunRepository()
run_item_repo = CrawlRunItemRepository()
price_repo = PriceObservationRepository()
queue_repo = DurableQueueRepository()
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _validate_excel_filename(filename: str) -> str:
    safe_name = Path(filename or '').name
    if not safe_name.lower().endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file Excel .xlsx")
    return safe_name


@router.post("/preflight", response_model=PreflightResponse)
async def preflight_hotel_list(file: UploadFile = File(...)):
    safe_name = _validate_excel_filename(file.filename or '')
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File Excel vượt quá giới hạn 10 MB")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(safe_name).suffix) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name
        return inspect_hotel_list_excel(temp_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không đọc được file Excel: {exc}") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@router.post("/upload", response_model=UploadResponse)
async def upload_hotel_list(
    file: UploadFile = File(...),
    checkin_dates: str = Form(...),
    save_artifacts: bool = Form(False),
    trigger_type: Literal['manual', 'scheduled'] = Form('manual'),
):
    """Chỉ tạo durable run/items. Worker độc lập sẽ claim; API không chạy Selenium."""
    safe_name = _validate_excel_filename(file.filename or '')
    dates = [d.strip() for d in checkin_dates.split(',') if d.strip()]
    if not dates:
        raise HTTPException(status_code=400, detail="Cần chọn ít nhất 1 ngày checkin")
    if len(dates) != len(set(dates)):
        raise HTTPException(status_code=400, detail="Danh sách ngày checkin có ngày bị trùng")
    today = datetime.now(ZoneInfo(settings.DISPLAY_TIMEZONE)).date()
    for value in dates:
        try:
            parsed = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ngày không hợp lệ: {value} (YYYY-MM-DD)")
        if parsed < today:
            raise HTTPException(status_code=400, detail=f"Ngày checkin {value} đã ở quá khứ")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File Excel vượt quá giới hạn 10 MB")
    digest = hashlib.sha256(content).hexdigest()
    upload_root = Path(settings.UPLOAD_DIR).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    saved_path = upload_root / f"{digest[:16]}_{safe_name}"
    created_file = not saved_path.exists()
    if created_file:
        saved_path.write_bytes(content)

    try:
        preflight = inspect_hotel_list_excel(str(saved_path))
        links = parse_hotel_list_excel(str(saved_path))
    except Exception as exc:
        if created_file and saved_path.exists():
            saved_path.unlink()
        raise HTTPException(status_code=400, detail=f"Không đọc được file Excel: {exc}") from exc
    if preflight['valid_links'] == 0:
        if created_file:
            saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="File không có link Booking.com hợp lệ")
    out_of_scope = [s['name'] for s in preflight['sheets'] if s['total_rows'] > 0 and not s['in_scope']]
    if out_of_scope:
        if created_file:
            saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Sheet ngoài phạm vi thành phố đang hỗ trợ: {', '.join(out_of_scope)}.",
        )

    context = default_crawl_context(save_artifacts)
    run_id = queue_repo.create_run_with_items(
        trigger_type=trigger_type, source_file=str(saved_path), source_original_filename=safe_name,
        source_file_sha256=digest, source_file_size=len(content), date_mode='explicit',
        checkin_dates=dates, hotel_links=links, crawl_context=context,
        save_artifacts=save_artifacts, scraper_version=settings.SCRAPER_VERSION,
        selector_version=settings.SELECTOR_VERSION, git_commit=current_git_commit(),
    )
    health = queue_repo.worker_health()
    message = "Đã đưa vào hàng đợi; worker sẽ xử lý."
    if not health.get('online'):
        message += " Worker hiện offline — hãy chạy scripts/run_worker.py."
    return UploadResponse(run_id=run_id, status='queued', message=message)


@router.get("/worker/health", response_model=WorkerHealthResponse)
async def get_worker_health():
    return queue_repo.worker_health()


@router.post("/runs/{run_id}/retry", response_model=UploadResponse)
async def retry_failed_items(run_id: int):
    new_run_id = queue_repo.create_retry_run(run_id)
    if new_run_id is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    if new_run_id == 0:
        raise HTTPException(status_code=400, detail="Job không có item error/partial để retry")
    return UploadResponse(run_id=new_run_id, status='queued', message="Đã tạo job retry riêng có audit trail.")


@router.get("/runs/{run_id}", response_model=CrawlRunResponse)
async def get_run_progress(run_id: int):
    run = run_repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    return run


@router.get("/runs", response_model=CrawlRunPageResponse)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return {
        'items': run_repo.list_runs(limit=limit, offset=offset),
        'total': run_repo.count_runs(),
        'limit': limit,
        'offset': offset,
    }


@router.get("/runs/{run_id}/items", response_model=CrawlRunItemPageResponse)
async def get_run_items(
    run_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    market: Optional[str] = Query(None, min_length=1, max_length=100),
    status: Optional[
        Literal['queued', 'running', 'success', 'partial', 'sold_out', 'not_bookable', 'error']
    ] = None,
):
    if not run_repo.get_by_id(run_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    page = run_item_repo.list_page_by_run(
        run_id, limit=limit, offset=offset, market=market, status=status,
    )
    return {**page, 'limit': limit, 'offset': offset}


@router.get("/items/{item_id}/artifact/{kind}")
async def get_item_artifact(item_id: int, kind: str):
    item = run_item_repo.get_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy item")
    key = 'screenshot_path' if kind == 'screenshot' else 'artifact_html_path' if kind == 'html' else None
    if not key or not item.get(key):
        raise HTTPException(status_code=404, detail="Item không có artifact này")
    root = Path(settings.ARTIFACT_DIR).resolve()
    path = Path(item[key]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Artifact path không hợp lệ")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact đã hết retention hoặc không tồn tại")
    media = 'image/png' if kind == 'screenshot' else 'application/gzip'
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: int):
    if not run_repo.get_by_id(run_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    content = build_run_export_xlsx(run_id, price_repo.list_for_export(run_id), run_item_repo.list_by_run(run_id))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="crawl_run_{run_id}.xlsx"'},
    )

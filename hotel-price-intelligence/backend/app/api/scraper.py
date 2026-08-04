import os
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.core.config import settings
from app.database.repositories import CrawlRunItemRepository, CrawlRunRepository, PriceObservationRepository
from app.schemas.scraper import CrawlRunItemResponse, CrawlRunResponse, UploadResponse
from app.scraper.export import build_run_export_xlsx
from app.scraper.job_runner import drain_queue

router = APIRouter()
run_repo = CrawlRunRepository()
run_item_repo = CrawlRunItemRepository()
price_repo = PriceObservationRepository()


@router.post("/upload", response_model=UploadResponse)
async def upload_hotel_list(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    checkin_dates: str = Form(...),  # CSV "YYYY-MM-DD,YYYY-MM-DD,..." - bắt buộc chọn trước khi submit
):
    """Nhận 1 file Excel + danh sách ngày checkin do người dùng tự chọn, tạo crawl_run
    (status='queued'), chạy nền (không block request). Checkout luôn tự động = checkin + 1.
    Nếu đang có job khác chạy, job này tự động chờ trong hàng đợi (không bị từ chối).
    """
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file Excel (.xlsx/.xls)")

    dates = [d.strip() for d in checkin_dates.split(',') if d.strip()]
    if not dates:
        raise HTTPException(status_code=400, detail="Cần chọn ít nhất 1 ngày checkin")
    if len(dates) != len(set(dates)):
        raise HTTPException(status_code=400, detail="Danh sách ngày checkin có ngày bị trùng")

    today = datetime.now().date()
    for d in dates:
        try:
            parsed = datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ngày không hợp lệ: {d} (định dạng YYYY-MM-DD)")
        if parsed < today:
            raise HTTPException(status_code=400, detail=f"Ngày checkin {d} đã ở quá khứ")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    saved_path = os.path.join(settings.UPLOAD_DIR, f"{timestamp}_{file.filename}")
    content = await file.read()
    with open(saved_path, 'wb') as f:
        f.write(content)

    was_busy = run_repo.has_running()

    run_id = run_repo.create(
        trigger_type='manual',
        source_file=saved_path,
        total=0,
        date_mode='explicit',
        checkin_dates=dates,
    )

    # Chạy nền trong thread pool của Starlette - không block response, không cần WebSocket.
    # Nếu đang có job khác 'running', drain_queue() sẽ tự no-op và job này chờ tới lượt
    # (được xử lý khi worker đang chạy job hiện tại rảnh ra và tự loop sang job kế tiếp).
    background_tasks.add_task(drain_queue)

    message = (
        "Đang có job khác chạy, đã đưa vào hàng chờ." if was_busy
        else "Đã bắt đầu chạy."
    )
    return UploadResponse(run_id=run_id, status='queued', message=message)


@router.get("/runs/{run_id}", response_model=CrawlRunResponse)
async def get_run_progress(run_id: int):
    run = run_repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    return run


@router.get("/runs", response_model=list[CrawlRunResponse])
async def list_runs(limit: int = 20, offset: int = 0):
    return run_repo.list_runs(limit=limit, offset=offset)


@router.get("/runs/{run_id}/items", response_model=list[CrawlRunItemResponse])
async def get_run_items(run_id: int):
    if not run_repo.get_by_id(run_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    return run_item_repo.list_by_run(run_id)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: int):
    if not run_repo.get_by_id(run_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy job")
    rows = price_repo.list_for_export(run_id)
    content = build_run_export_xlsx(run_id, rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="crawl_run_{run_id}.xlsx"'},
    )

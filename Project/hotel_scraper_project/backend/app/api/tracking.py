from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.database.repositories import TrackingRepository
from app.core.database import get_db

router = APIRouter()
tracking_repo = TrackingRepository(get_db())


class TrackingDataRequest(BaseModel):
    history_id: int
    year: int
    month: int
    market: str
    cluster: str
    breakfast: str
    room_group: str
    level: str
    compare_history_id: Optional[int] = None
    # Compare filters (optional, for comparing with different filters)
    compare_year: Optional[int] = None
    compare_month: Optional[int] = None
    compare_market: Optional[str] = None
    compare_cluster: Optional[str] = None
    compare_breakfast: Optional[str] = None
    compare_room_group: Optional[str] = None
    compare_level: Optional[str] = None


@router.get("/history-list")
async def get_tracking_history_list():
    """
    Lấy danh sách các phiên cào với type = 'price'
    """
    try:
        histories = tracking_repo.get_price_crawl_histories()
        return {
            "success": True,
            "data": histories
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data")
async def get_tracking_data(request: TrackingDataRequest):
    """
    Lấy dữ liệu tracking với filters
    """
    try:
        # Validate month
        if request.month < 1 or request.month > 12:
            raise HTTPException(status_code=400, detail="Tháng không hợp lệ")
        
        # Get main data
        main_data = tracking_repo.get_tracking_data(
            history_id=request.history_id,
            year=request.year,
            month=request.month,
            market=request.market,
            cluster=request.cluster,
            breakfast=request.breakfast,
            room_group=request.room_group,
            level=request.level
        )
        
        # Get compare data if requested
        compare_data = None
        if request.compare_history_id:
            # Use compare filters if provided, otherwise use main filters
            compare_year = request.compare_year if request.compare_year else request.year
            compare_month = request.compare_month if request.compare_month else request.month
            compare_market = request.compare_market if request.compare_market else request.market
            compare_cluster = request.compare_cluster if request.compare_cluster else request.cluster
            compare_breakfast = request.compare_breakfast if request.compare_breakfast else request.breakfast
            compare_room_group = request.compare_room_group if request.compare_room_group else request.room_group
            compare_level = request.compare_level if request.compare_level else request.level
            
            compare_data = tracking_repo.get_tracking_data(
                history_id=request.compare_history_id,
                year=compare_year,
                month=compare_month,
                market=compare_market,
                cluster=compare_cluster,
                breakfast=compare_breakfast,
                room_group=compare_room_group,
                level=compare_level
            )
        
        # Generate date columns for the month
        date_columns = tracking_repo.generate_month_dates(request.year, request.month)
        
        return {
            "success": True,
            "data": {
                "main": main_data,
                "compare": compare_data,
                "date_columns": date_columns
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import date
from app.schemas.scraper import CrawlHistoryResponse, ApiDataQuery
from app.database.repositories import CrawlHistoryRepository, CrawlDataRepository
import json

router = APIRouter()

@router.get("/histories")
async def get_histories(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    source: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    scrape_type: Optional[str] = None
):
    try:
        history_repo = CrawlHistoryRepository()
        
        total_count = history_repo.get_total_count(
            source_filter=source,
            date_from=date_from,
            date_to=date_to,
            scrape_type=scrape_type
        )
        
        offset = (page - 1) * page_size
        
        histories = history_repo.get_all_histories(
            limit=page_size,
            offset=offset,
            source_filter=source,
            date_from=date_from,
            date_to=date_to,
            scrape_type=scrape_type
        )
        
        return {
            'items': histories,
            'total': total_count,
            'page': page,
            'page_size': page_size,
            'total_pages': (total_count + page_size - 1) // page_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/histories/{history_id}")
async def get_history_detail(
    history_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000)
):
    try:
        history_repo = CrawlHistoryRepository()
        data_repo = CrawlDataRepository()
        
        history = history_repo.get_history_by_id(history_id)
        if not history:
            raise HTTPException(status_code=404, detail="History not found")
        
        total_records = data_repo.get_data_count(history_id)
        offset = (page - 1) * page_size
        
        data_records = data_repo.get_data_by_history(
            history_id,
            limit=page_size,
            offset=offset
        )
        
        return {
            'history': history,
            'data': data_records,
            'total_records': total_records,
            'page': page,
            'page_size': page_size,
            'total_pages': (total_records + page_size - 1) // page_size
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/histories/{history_id}")
async def delete_history(history_id: int):
    try:
        history_repo = CrawlHistoryRepository()
        history_repo.delete_history(history_id)
        return {'message': 'History deleted successfully'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/{history_id}")
async def export_history_data(history_id: int):
    try:
        history_repo = CrawlHistoryRepository()
        data_repo = CrawlDataRepository()
        
        # Get history to know scrape_type
        history = history_repo.get_history_by_id(history_id)
        if not history:
            raise HTTPException(status_code=404, detail="History not found")
        
        scrape_type = history.get('scrape_type', 'info')
        records = data_repo.export_data_by_history(history_id)
        
        formatted_records = []
        for record in records:
            options = record.get('options', {})
            if isinstance(options, str):
                options = json.loads(options) if options else {}
            
            if scrape_type == 'price':
                # Price type: 16 columns in exact order
                formatted_records.append({
                    'Ngày cào': record['crawl_date'].isoformat() if record.get('crawl_date') else '',
                    'Giờ cào': record.get('crawl_time') or (record['created_at'].strftime('%H:%M:%S') if record.get('created_at') else ''),
                    'Check in': record['check_in'].isoformat() if record.get('check_in') else '',
                    'Check out': record['check_out'].isoformat() if record.get('check_out') else '',
                    'Tên khách sạn': record.get('hotel_name') or '',
                    'Tên hạng phòng': record.get('room_type') or '',
                    'Số lượng người': record.get('num_people') if record.get('num_people') else '',
                    'Giá sau giảm': record.get('price_after_discount') if record.get('price_after_discount') else '',
                    'Giá gốc': record.get('price_original') if record.get('price_original') else '',
                    'Giảm giá': record.get('discount_percent') or '',
                    'Market': record.get('Market') or '',
                    'Cluster': record.get('Cluster') or '',
                    'Level đối thủ': record.get('Level đối thủ') or '',
                    'Giá bao gồm bữa sáng': record.get('Giá bao gồm bữa sáng') or '',
                    'Nhóm hạng phòng': record.get('Nhóm hạng phòng') or '',
                    'Level': record.get('Level') or ''
                })
            else:
                # Info type: 17 columns in exact order
                formatted_records.append({
                    'Ngày cào': record['crawl_date'].isoformat() if record.get('crawl_date') else '',
                    'Giờ cào': record.get('crawl_time') or (record['created_at'].strftime('%H:%M:%S') if record.get('created_at') else ''),
                    'Check in': record['check_in'].isoformat() if record.get('check_in') else '',
                    'Check out': record['check_out'].isoformat() if record.get('check_out') else '',
                    'Tên khách sạn': record.get('hotel_name') or '',
                    'Link khách sạn': record.get('hotel_link') or '',
                    'Số lượng review': record.get('review_count') if record.get('review_count') else '',
                    'Điểm review': record.get('review_score') if record.get('review_score') else '',
                    'Các tiện nghi được ưa chuộng nhất': record.get('popular_facilities') or '',
                    'Tên hạng phòng': record.get('room_type') or '',
                    'Số lượng người': record.get('num_people') if record.get('num_people') else '',
                    'Giường': record.get('bed_info') or '',
                    'Diện tích phòng': record.get('room_area') or '',
                    'Các lựa chọn': record.get('room_choices') or '',
                    'Market': record.get('Market') or '',
                    'Cluster': record.get('Cluster') or '',
                    'Level đối thủ': record.get('Level đối thủ') or '',
                    'Giá bao gồm bữa sáng': record.get('Giá bao gồm bữa sáng') or '',
                    'Nhóm hạng phòng': record.get('Nhóm hạng phòng') or '',
                    'Level': record.get('Level') or ''
                })
        
        return formatted_records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/public/history/{history_id}")
async def get_history_public_data(history_id: int):
    """
    Public API to get data for a specific history record, formatted exactly like the Excel export.
    Useful for PowerBI integration.
    """
    try:
        data_repo = CrawlDataRepository()
        history_repo = CrawlHistoryRepository()
        
        # Get history info to know scrape_type
        history = history_repo.get_history_by_id(history_id)
        if not history:
            raise HTTPException(status_code=404, detail="History not found")
            
        scrape_type = history.get('scrape_type', 'info')
        # Reuse export logic which gets all raw data
        records = data_repo.export_data_by_history(history_id)
        
        formatted_records = []
        for record in records:
            options = record.get('options', {})
            if isinstance(options, str):
                options = json.loads(options) if options else {}
            
            # Common fields logic
            base_record = {
                'Ngày cào': record['crawl_date'].isoformat() if record.get('crawl_date') else '',
                'Giờ cào': record['created_at'].strftime('%H:%M:%S') if record.get('created_at') else record.get('crawl_time', ''),
                'Check in': record['check_in'].isoformat() if record.get('check_in') else '',
                'Check out': record['check_out'].isoformat() if record.get('check_out') else '',
                'Tên khách sạn': record.get('hotel_name') or '',
            }

            if scrape_type == 'info':
                # Info-specific columns
                formatted_records.append({
                    **base_record,
                    'Link khách sạn': record.get('hotel_link') or '',
                    'Số lượng review': record.get('review_count') if record.get('review_count') else '',
                    'Điểm review': record.get('review_score') if record.get('review_score') else '',
                    'Các tiện nghi được ưa chuộng nhất': record.get('popular_facilities') or '',
                    'Tên hạng phòng': record.get('room_type') or '',
                    'Số lượng người': record.get('num_people') if record.get('num_people') else '',
                    'Giường': record.get('bed_info') or '',
                    'Diện tích phòng': record.get('room_area') or '',
                    'Các lựa chọn': options.get('facilities', ''),
                    'Market': record.get('Market') or '',
                    'Cluster': record.get('Cluster') or '',
                    'Level đối thủ': record.get('Level đối thủ') or '',
                    'Giá bao gồm bữa sáng': record.get('Giá bao gồm bữa sáng') or '',
                    'Nhóm hạng phòng': record.get('Nhóm hạng phòng') or '',
                    'Level': record.get('Level') or ''
                })
            else:
                # Price-specific columns
                formatted_records.append({
                    **base_record,
                    'Tên hạng phòng': record.get('room_type') or '',
                    'Số lượng người': record.get('num_people') if record.get('num_people') else '',
                    'Giá sau giảm': record.get('price_after_discount') if record.get('price_after_discount') else '',
                    'Giá gốc': record.get('price_original') if record.get('price_original') else '',
                    'Giảm giá': record.get('discount_percent') or '',
                    'Market': record.get('Market') or '',
                    'Cluster': record.get('Cluster') or '',
                    'Level đối thủ': record.get('Level đối thủ') or '',
                    'Giá bao gồm bữa sáng': record.get('Giá bao gồm bữa sáng') or '',
                    'Nhóm hạng phòng': record.get('Nhóm hạng phòng') or '',
                    'Level': record.get('Level') or ''
                })
        
        return formatted_records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/public/latest")
async def get_latest_public_data(scrape_type: str = Query(..., regex="^(info|price)$")):
    """
    Public API to get the LATEST data for a specific scrape type.
    """
    try:
        history_repo = CrawlHistoryRepository()
        
        # Get latest history for this type
        latest_history = history_repo.get_latest_history(scrape_type=scrape_type)
        
        if not latest_history:
            return []
            
        return await get_history_public_data(latest_history['id'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api")
async def get_api_data(
    mode: str = Query("latest", regex="^(latest|all|filter)$"),
    source: Optional[str] = None,
    history_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None
):
    try:
        history_repo = CrawlHistoryRepository()
        data_repo = CrawlDataRepository()
        
        if mode == "latest":
            latest = history_repo.get_latest_history(source=source)
            if not latest:
                return []
            records = data_repo.export_data_by_history(latest["id"])
        elif mode == "all":
            records = data_repo.export_all_data()
        elif mode == "filter":
            if history_id:
                records = data_repo.export_data_by_history(history_id)
            else:
                records = data_repo.export_data_by_filters(
                    source_filter=source,
                    date_from=date_from,
                    date_to=date_to,
                )
        else:
            raise HTTPException(status_code=400, detail="Unsupported mode")
        
        formatted_records = []
        for record in records:
            options = record.get('options', {})
            if isinstance(options, str):
                options = json.loads(options) if options else {}
            
            formatted_records.append({
                'Hàng_gốc': options.get('row_number', ''),
                'Ngày cào': record['crawl_date'].isoformat() if record.get('crawl_date') else '',
                'Ngày cần cào': options.get('target_date') or (record['check_in'].isoformat() if record.get('check_in') else ''),
                'Tên khách sạn': record.get('hotel_name') or '',
                'Link khách sạn': record.get('hotel_link') or '',
                'Giá sau giảm': record.get('price_after_discount') if record.get('price_after_discount') else '',
                'Giá gốc': record.get('price_original') if record.get('price_original') else '',
                'Số lượng review': record.get('review_count') if record.get('review_count') else '',
                'Điểm review': record.get('review_score') if record.get('review_score') else '',
                'Tên hạng phòng': record.get('room_type') or '',
                'Số lượng người': record.get('num_people') if record.get('num_people') else '',
                'Giường': record.get('bed_info') or '',
                'Diện tích phòng': record.get('room_area') or '',
                'Các lựa chọn': options.get('facilities', ''),
                'Market': record.get('Market') or record.get('market') or '',
                'Cluster': record.get('Cluster') or '',
                'Level đối thủ': record.get('Level đối thủ') or '',
                'Giá bao gồm bữa sáng': record.get('Giá bao gồm bữa sáng') or '',
                'Nhóm hạng phòng': record.get('Nhóm hạng phòng') or '',
                'Level': record.get('Level') or ''
            })
        
        return formatted_records
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

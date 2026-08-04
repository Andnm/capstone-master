from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import date, datetime

class CrawlHistoryCreate(BaseModel):
    crawl_date: date
    crawl_time: str
    source: str = "booking"
    scrape_type: str = "info"

class CrawlHistoryResponse(BaseModel):
    id: int
    crawl_date: date
    crawl_time: str
    source: str
    scrape_type: str
    total_records: int
    created_at: datetime

class CrawlDataCreate(BaseModel):
    hotel_name: str
    hotel_link: str
    price_after_discount: Optional[float] = None
    price_original: Optional[float] = None
    discount_percent: Optional[str] = None
    review_count: Optional[int] = None
    review_score: Optional[float] = None
    room_type: str
    num_people: Optional[int] = None
    bed_info: Optional[str] = None
    room_area: Optional[str] = None
    room_choices: Optional[str] = None
    popular_facilities: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    options: Optional[Dict[str, Any]] = None

class CrawlDataResponse(BaseModel):
    id: int
    history_id: int
    hotel_name: str
    hotel_link: str
    price_after_discount: Optional[float]
    price_original: Optional[float]
    review_count: Optional[int]
    review_score: Optional[float]
    room_type: str
    num_people: Optional[int]
    bed_info: str
    room_area: str
    room_choices: Optional[str] = None
    popular_facilities: Optional[str] = None
    discount_percent: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    options: Dict[str, Any]
    created_at: datetime

class ScrapeRequest(BaseModel):
    links: List[Dict[str, Any]]
    date_ranges: List[Dict[str, str]]
    source: str = "booking"

class ScrapeProgress(BaseModel):
    type: str
    current: int
    total: int
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None

class HistoryListQuery(BaseModel):
    page: int = 1
    page_size: int = 10
    source_filter: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None

class ApiDataQuery(BaseModel):
    mode: str = "latest"
    source: Optional[str] = None
    history_id: Optional[int] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None

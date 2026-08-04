from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class CrawlRunResponse(BaseModel):
    id: int
    status: str
    trigger_type: str
    source_file: Optional[str] = None
    date_mode: str = 'lead_time'
    lead_time_buckets: Optional[str] = None
    checkin_dates: Optional[List[str]] = None
    total: int
    processed: int
    success_count: int
    error_count: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    run_id: int
    status: str
    message: str


class RoomObservationResponse(BaseModel):
    room_type_raw: Optional[str] = None
    room_type_norm: Optional[str] = None
    is_reference_room: bool = False
    price_total: Optional[float] = None
    price_per_night: Optional[float] = None
    original_price: Optional[float] = None
    discount_percent: Optional[float] = None
    max_occupancy: Optional[int] = None
    bed_config: Optional[str] = None
    room_area: Optional[str] = None
    breakfast_included: Optional[bool] = None
    free_cancellation: Optional[bool] = None
    cancellation_policy: Optional[str] = None
    rooms_left: Optional[int] = None
    availability_status: str

    class Config:
        from_attributes = True


class CrawlRunItemResponse(BaseModel):
    id: int
    crawl_run_id: int
    hotel_link: str
    hotel_name_hint: Optional[str] = None
    hotel_name: Optional[str] = None
    hotel_id: Optional[str] = None
    hotel_city: Optional[str] = None
    hotel_address: Optional[str] = None
    hotel_review_score: Optional[float] = None
    hotel_review_count: Optional[int] = None
    checkin_date: date
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    rooms: List[RoomObservationResponse] = []

    class Config:
        from_attributes = True

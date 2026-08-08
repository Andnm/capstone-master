from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _as_utc(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class CrawlRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    trigger_type: str
    source_file: Optional[str] = None
    source_original_filename: Optional[str] = None
    source_file_sha256: Optional[str] = None
    source_file_size: Optional[int] = None
    save_artifacts: bool = False
    crawl_context: Optional[Dict[str, Any]] = None
    scraper_version: Optional[str] = None
    selector_version: Optional[str] = None
    git_commit: Optional[str] = None
    storage_timezone: str = 'UTC'
    retry_of_run_id: Optional[int] = None
    date_mode: str = 'lead_time'
    lead_time_buckets: Optional[str] = None
    checkin_dates: Optional[List[str]] = None
    total: int
    processed: int
    success_count: int
    partial_count: int = 0
    error_count: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None

    _timestamps_utc = field_validator(
        'started_at', 'finished_at', 'created_at', 'updated_at', mode='before'
    )(_as_utc)


class UploadResponse(BaseModel):
    run_id: int
    status: str
    message: str


class PreflightSheet(BaseModel):
    name: str
    city: Optional[str] = None
    in_scope: bool
    total_rows: int
    valid_links: int


class PreflightIssue(BaseModel):
    sheet: str
    row: int
    name: str = ''
    reason: Optional[str] = None
    duplicate_of: Optional[str] = None


class PreflightResponse(BaseModel):
    total_rows: int
    valid_links: int
    invalid_rows: List[PreflightIssue]
    duplicate_rows: List[PreflightIssue]
    sheets: List[PreflightSheet]
    search_context: str


class RoomObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    room_type_raw: Optional[str] = None
    room_type_norm: Optional[str] = None
    is_reference_room: bool = False
    price_total: Optional[float] = None
    price_per_night: Optional[float] = None
    original_price: Optional[float] = None
    discount_percent: Optional[float] = None
    taxes_fees: Optional[float] = None
    price_includes_tax: Optional[bool] = None
    room_option_index: int
    room_option_key: str
    room_identity_key: Optional[str] = None
    rate_plan_key: Optional[str] = None
    reference_definition_id: Optional[int] = None
    reference_match_status: str = 'calibrating'
    reference_match_score: Optional[float] = None
    max_occupancy: Optional[int] = None
    bed_config: Optional[str] = None
    room_area: Optional[str] = None
    breakfast_included: Optional[bool] = None
    free_cancellation: Optional[bool] = None
    cancellation_policy: Optional[str] = None
    rooms_left: Optional[int] = None
    availability_status: str

class CrawlRunItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    crawl_run_id: int
    source_hotel_link: str
    hotel_link: str
    hotel_name_hint: Optional[str] = None
    hotel_name: Optional[str] = None
    hotel_id: Optional[str] = None
    hotel_city: Optional[str] = None
    hotel_address: Optional[str] = None
    hotel_review_score: Optional[float] = None
    hotel_review_count: Optional[int] = None
    checkin_date: date
    checkout_date: date
    status: str
    attempt_count: int = 0
    claimed_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    last_error_code: Optional[str] = None
    dom_room_row_count: int = 0
    candidate_rate_count: int = 0
    parsed_options_count: int = 0
    rejected_options_count: int = 0
    raw_options_count: int = 0
    saved_options_count: int = 0
    parse_warning_count: int = 0
    rejected_options: Optional[Any] = None
    reference_match_status: str = 'calibrating'
    driver_start_ms: Optional[int] = None
    page_load_ms: Optional[int] = None
    availability_wait_ms: Optional[int] = None
    parse_ms: Optional[int] = None
    db_write_ms: Optional[int] = None
    item_total_ms: Optional[int] = None
    artifact_html_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    rooms: List[RoomObservationResponse] = Field(default_factory=list)

    _timestamps_utc = field_validator(
        'claimed_at', 'heartbeat_at', 'finished_at', 'created_at', mode='before'
    )(_as_utc)


class WorkerHealthResponse(BaseModel):
    online: bool
    message: Optional[str] = None
    worker_id: Optional[str] = None
    status: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    heartbeat_age_seconds: Optional[int] = None
    current_item_id: Optional[int] = None
    scraper_version: Optional[str] = None

    _heartbeat_utc = field_validator('heartbeat_at', mode='before')(_as_utc)

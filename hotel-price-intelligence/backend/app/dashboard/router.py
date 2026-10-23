"""Router `/api/dashboard/v1/*` — CHỈ GET, chỉ đọc. Không tham số đường dẫn/tên file, không upload, không scrape/build/deserialize."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from . import labels, service
from . import schemas as S
from .service import ApiError
from .store import SnapshotStore

router = APIRouter(prefix="/api/dashboard/v1")


def _store(request: Request) -> SnapshotStore:
    store: Optional[SnapshotStore] = getattr(request.app.state, "store", None)
    if store is None:
        raise ApiError(503, "snapshot_unavailable", "err_snapshot_unavailable")
    return store


@router.get("/health", response_model=S.HealthResponse, response_model_by_alias=True)
def health(request: Request) -> S.HealthResponse:
    store: Optional[SnapshotStore] = getattr(request.app.state, "store", None)
    if store is None:
        return S.HealthResponse(status="unavailable", snapshot_id=None, reason=getattr(request.app.state, "unavailable_reason", None) or "snapshot_unavailable")
    return S.HealthResponse(status="ok", snapshot_id=store.snapshot_id, reason=None)


@router.get("/config", response_model=S.DashboardConfig, response_model_by_alias=True)
def config(request: Request) -> S.DashboardConfig:
    return service.get_config(_store(request))


@router.get("/hotels", response_model=S.HotelsResponse, response_model_by_alias=True)
def hotels(request: Request, city: Optional[str] = Query(default=None, max_length=40)) -> S.HotelsResponse:
    return service.list_hotels(_store(request), city)


@router.get("/forecast", response_model=S.ForecastResponse, response_model_by_alias=True)
def forecast(request: Request, hotel_id: Optional[str] = Query(default=None, alias="hotelId", max_length=200),
             check_in: Optional[str] = Query(default=None, alias="checkIn", max_length=20)) -> S.ForecastResponse:
    return service.get_forecast(_store(request), hotel_id, check_in)


@router.get("/hotelier", response_model=S.HotelierResponse, response_model_by_alias=True)
def hotelier(request: Request, hotel_id: Optional[str] = Query(default=None, alias="hotelId", max_length=200),
             check_in: Optional[str] = Query(default=None, alias="checkIn", max_length=20)) -> S.HotelierResponse:
    return service.get_hotelier(_store(request), hotel_id, check_in)


@router.get("/model-info", response_model=S.ModelInfoResponse, response_model_by_alias=True)
def model_info(request: Request) -> S.ModelInfoResponse:
    return service.get_model_info(_store(request))

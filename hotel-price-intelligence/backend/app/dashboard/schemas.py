"""Hợp đồng API (API_CONTRACT v3) dạng pydantic v2, JSON camelCase. Đơn vị: VND; mọi `*PctChange`/`pctVs*` là ĐIỂM PHẦN TRĂM; không NaN/inf; `null` không bao giờ thành 0."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Horizon = Literal[1, 3, 7, 14]
ServedHorizon = Literal[1, 3]
Direction = Literal["up", "down", "stable"]
InferenceMode = Literal["cold_start", "history_enriched"]
PredictionMethod = Literal["model", "persistence"]
WarningCode = Literal["cold_start_preliminary", "persistence_routed", "review_gap_wide", "group_small", "metadata_missing"]
ClassifierReason = Literal["dependency_missing", "not_validated"]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", allow_inf_nan=False)


class ApiWarning(ApiModel):
    code: WarningCode
    message: str


class SnapshotInfo(ApiModel):
    snapshot_id: str
    data_as_of: str
    generated_at: str
    claim_level: Literal["dev_research_exploratory"]
    dataset_versions: dict[str, str]
    label: str


class UnsupportedHorizonConfig(ApiModel):
    horizon: Horizon
    reason: Literal["model_not_available"]


class ClassifierCapability(ApiModel):
    available: bool
    reason: Optional[ClassifierReason]


class Limits(ApiModel):
    min_group_size: int
    position_alert_pct: float
    market_alert_pct: float


class DashboardConfig(ApiModel):
    snapshot: SnapshotInfo
    horizons: list[ServedHorizon]
    unsupported_horizons: list[UnsupportedHorizonConfig]
    classifier: ClassifierCapability
    limits: Limits


class Hotel(ApiModel):
    id: str
    name: str
    city: str
    review_score: Optional[float]
    metadata_as_of: Optional[str]


class CheckInOption(ApiModel):
    date: str
    supported_horizons: list[ServedHorizon]


class HotelWithCheckIns(Hotel):
    check_ins: list[CheckInOption]


class HotelsResponse(ApiModel):
    snapshot_id: str
    hotels: list[HotelWithCheckIns]


class HorizonPrediction(ApiModel):
    horizon: ServedHorizon
    method: PredictionMethod
    routing_reason: str
    predicted_price: float
    expected_delta: float
    expected_pct_change: float
    direction: Direction
    target_observation_date: str


class UnsupportedHorizon(ApiModel):
    horizon: Horizon
    reason: Literal["model_not_available", "after_checkin"]


class RoomSummary(ApiModel):
    max_occupancy: Optional[int]
    area_m2: Optional[float]
    breakfast_included: Optional[bool]
    free_cancellation: Optional[bool]


class ClassifierResult(ApiModel):
    horizon: ServedHorizon
    label: Direction
    record_id: int


class ClassifierBlock(ApiModel):
    available: bool
    reason: Optional[ClassifierReason]
    results: list[ClassifierResult]
    claim: Literal["none"]


class Evidence(ApiModel):
    level: Literal["dev_exploratory"]
    claim: Literal["exploratory"]


class HistoryPoint(ApiModel):
    date: str
    price: float


class Provenance(ApiModel):
    canonical_series_id: str
    record_id: int
    bundles: dict[str, str]


class ForecastResponse(ApiModel):
    snapshot_id: str
    data_as_of: str
    hotel: Hotel
    check_in: str
    lead_days: int
    current_price: float
    current_observed_at: str
    inference_mode: InferenceMode
    history_observation_count: int
    history_span_days: Optional[float]
    room_summary: RoomSummary
    evidence: Evidence
    supported_horizons: list[ServedHorizon]
    unsupported_horizons: list[UnsupportedHorizon]
    predictions: list[HorizonPrediction]
    classifier: ClassifierBlock
    warnings: list[ApiWarning]
    history: list[HistoryPoint]
    provenance: Provenance


class CompsetHotel(Hotel):
    current_price: float
    observed_at: str
    pct_vs_group_median: Optional[float]
    change7_days_pct: Optional[float]
    review_gap: Optional[float]
    is_anchor: bool


class GroupHorizonSummary(ApiModel):
    horizon: ServedHorizon
    n_matched: int
    n_model: int
    n_persistence: int
    current_median_price: float
    predicted_median_price: float
    median_pct_change: float


class PriceRank(ApiModel):
    rank: int
    of: int


class GroupSummary(ApiModel):
    insufficient: bool
    reason: Optional[Literal["group_too_small", "anchor_review_missing"]]
    n: int
    max_review_gap: Optional[float]
    current_median_price: Optional[float]
    price_rank: Optional[PriceRank]
    per_horizon: list[GroupHorizonSummary]


class AlertRule(ApiModel):
    name: Literal["position_gap", "market_move"]
    threshold: float
    value: float
    horizon: Optional[ServedHorizon] = None


class HotelierAlert(ApiModel):
    id: str
    severity: Literal["attention", "info"]
    category: Literal["position", "market"]
    title: str
    description: str
    rule: AlertRule


class CheckInPrice(ApiModel):
    check_in: str
    anchor_price: float
    group_median_price: Optional[float]
    n: int


class HotelierResponse(ApiModel):
    snapshot_id: str
    data_as_of: str
    check_in: str
    anchor: CompsetHotel
    compset: list[CompsetHotel]
    group_summary: GroupSummary
    check_in_prices: list[CheckInPrice]
    anchor_forecast: Optional[ForecastResponse]
    alerts: list[HotelierAlert]
    warnings: list[ApiWarning]


class RoutingInfo(ApiModel):
    mode: InferenceMode
    use_model: bool
    reason: str
    validation_lift_vnd: float
    ci95: list[float]
    validation_rows: int


class Compatibility(ApiModel):
    status: Literal["dev_compatibility_empirically_checked"]
    rows: int
    max_abs_diff_vnd: float
    rows_over_tolerance: int
    tolerance_vnd: float


class HorizonEvidence(ApiModel):
    validation_lift_pct: list[float]
    test_lift_pct: list[float]
    test_exposed: Literal[True]
    source: str
    source_sha256: str


class HorizonModelInfo(ApiModel):
    horizon: ServedHorizon
    task: Literal["regression"]
    model_name: str
    family: str
    dataset_version: str
    claim_level: Literal["dev_research_exploratory"]
    bundle_sha256: str
    trained_environment: dict[str, str]
    validated_runtime: dict[str, str]
    compatibility: Compatibility
    routing: list[RoutingInfo]
    evidence: HorizonEvidence
    limitations: list[str]


class ClassifierCompatibility(ApiModel):
    status: Literal["dev_compatibility_empirically_checked"]
    rows: int
    max_abs_probability_diff: float
    label_mismatch_count: int
    tolerance_probability: float


class ClassifierCoverage(ApiModel):
    snapshot_records: int
    record_ids_linked_to_regression: Literal[True]


class ClassifierHorizonModelInfo(ApiModel):
    horizon: ServedHorizon
    task: Literal["classification"]
    model_name: str
    family: str
    dataset_version: str
    claim_level: Literal["none"]
    bundle_sha256: str
    trained_environment: dict[str, str]
    validated_runtime: dict[str, str]
    class_mapping: dict[str, str]
    compatibility: ClassifierCompatibility
    coverage: ClassifierCoverage
    limitations: list[str]


class ClassifierModelInfo(ApiModel):
    available: bool
    reason: Optional[ClassifierReason]
    claim: Literal["none"]
    note: str
    horizons: list[ClassifierHorizonModelInfo]


class RuleInfo(ApiModel):
    name: str
    description: str
    threshold: float


class ModelInfoResponse(ApiModel):
    snapshot: SnapshotInfo
    horizons: list[HorizonModelInfo]
    unsupported_horizons: list[UnsupportedHorizonConfig]
    classifier: ClassifierModelInfo
    rules: list[RuleInfo]
    notes: list[str]


class HealthResponse(ApiModel):
    status: Literal["ok", "unavailable"]
    snapshot_id: Optional[str]
    reason: Optional[str]


class ErrorBody(ApiModel):
    code: str
    message: str
    details: Optional[dict[str, object]] = None


class ErrorResponse(ApiModel):
    error: ErrorBody

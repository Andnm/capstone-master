/**
 * Hợp đồng API thật `/api/dashboard/v1/*` (API_CONTRACT v3, đã GPT PASS PLAN).
 * VND là số dương hữu hạn; mọi `*PctChange`/`pctVs*` là ĐIỂM PHẦN TRĂM (10 nghĩa là +10%).
 * Ngày `YYYY-MM-DD` là ngày VN; `*At` là ISO-8601 UTC có `Z`. `null` nghĩa là thiếu dữ liệu thật — không bao giờ coi là 0.
 */
export type InferenceMode = "cold_start" | "history_enriched";
export type Direction = "up" | "down" | "stable";
export type Horizon = 1 | 3 | 7 | 14;
export type ServedHorizon = 1 | 3;
export type PredictionMethod = "model" | "persistence";
export type WarningCode = "cold_start_preliminary" | "persistence_routed" | "review_gap_wide" | "group_small" | "metadata_missing";
export interface ApiWarning { code: WarningCode; message: string }

export interface SnapshotInfo {
  snapshotId: string;
  dataAsOf: string;
  generatedAt: string;
  claimLevel: "dev_research_exploratory";
  datasetVersions: Record<string, string>;
  label: string;
}
export type ClassifierReason = "dependency_missing" | "not_validated";
export interface DashboardConfig {
  snapshot: SnapshotInfo;
  horizons: ServedHorizon[];
  unsupportedHorizons: { horizon: Horizon; reason: "model_not_available" }[];
  classifier: { available: boolean; reason: ClassifierReason | null };
  limits: { minGroupSize: number; positionAlertPct: number; marketAlertPct: number };
}

export interface Hotel { id: string; name: string; city: string; reviewScore: number | null; metadataAsOf: string | null }
export interface CheckInOption { date: string; supportedHorizons: ServedHorizon[] }
export interface HotelWithCheckIns extends Hotel { checkIns: CheckInOption[] }
export interface HotelsResponse { snapshotId: string; hotels: HotelWithCheckIns[] }

export interface HorizonPrediction {
  horizon: ServedHorizon;
  method: PredictionMethod;
  routingReason: string;
  predictedPrice: number;
  expectedDelta: number;
  expectedPctChange: number;
  direction: Direction;
  targetObservationDate: string;
}
export interface UnsupportedHorizon { horizon: Horizon; reason: "model_not_available" | "after_checkin" }
export interface RoomSummary { maxOccupancy: number | null; areaM2: number | null; breakfastIncluded: boolean | null; freeCancellation: boolean | null }
export interface ClassifierResult { horizon: ServedHorizon; label: Direction; recordId: number }
export interface ClassifierBlock { available: boolean; reason: ClassifierReason | null; results: ClassifierResult[]; claim: "none" }

export interface ForecastResponse {
  snapshotId: string;
  dataAsOf: string;
  hotel: Hotel;
  checkIn: string;
  leadDays: number;
  currentPrice: number;
  currentObservedAt: string;
  inferenceMode: InferenceMode;
  historyObservationCount: number;
  historySpanDays: number | null;
  roomSummary: RoomSummary;
  evidence: { level: "dev_exploratory"; claim: "exploratory" };
  supportedHorizons: ServedHorizon[];
  unsupportedHorizons: UnsupportedHorizon[];
  predictions: HorizonPrediction[];
  classifier: ClassifierBlock;
  warnings: ApiWarning[];
  history: { date: string; price: number }[];
  provenance: { canonicalSeriesId: string; recordId: number; bundles: Record<string, string> };
}

export interface CompsetHotel extends Hotel {
  currentPrice: number;
  observedAt: string;
  pctVsGroupMedian: number | null;
  change7DaysPct: number | null;
  reviewGap: number | null;
  isAnchor: boolean;
}
export interface GroupHorizonSummary {
  horizon: ServedHorizon;
  nMatched: number;
  nModel: number;
  nPersistence: number;
  currentMedianPrice: number;
  predictedMedianPrice: number;
  medianPctChange: number;
}
export interface GroupSummary {
  insufficient: boolean;
  reason: "group_too_small" | "anchor_review_missing" | null;
  n: number;
  maxReviewGap: number | null;
  currentMedianPrice: number | null;
  priceRank: { rank: number; of: number } | null;
  perHorizon: GroupHorizonSummary[];
}
export interface HotelierAlert {
  id: string;
  severity: "attention" | "info";
  category: "position" | "market";
  title: string;
  description: string;
  rule: { name: "position_gap" | "market_move"; threshold: number; value: number; horizon?: ServedHorizon | null };
}
export interface HotelierResponse {
  snapshotId: string;
  dataAsOf: string;
  checkIn: string;
  anchor: CompsetHotel;
  compset: CompsetHotel[];
  groupSummary: GroupSummary;
  checkInPrices: { checkIn: string; anchorPrice: number; groupMedianPrice: number | null; n: number }[];
  anchorForecast: ForecastResponse | null;
  alerts: HotelierAlert[];
  warnings: ApiWarning[];
}

/** `validationLiftVnd`/`ci95` là TỈ LỆ cải thiện MAE trên thang VND so với giữ nguyên giá (0,1575 = +15,75%), KHÔNG phải tiền VND; muốn hiển thị % phải ×100. Không phải độ tin cậy của một dự báo. */
export interface RoutingInfo { mode: InferenceMode; useModel: boolean; reason: string; validationLiftVnd: number; ci95: number[]; validationRows: number }
export interface HorizonModelInfo {
  horizon: ServedHorizon;
  task: "regression";
  modelName: string;
  family: string;
  datasetVersion: string;
  claimLevel: "dev_research_exploratory";
  bundleSha256: string;
  trainedEnvironment: Record<string, string>;
  validatedRuntime: Record<string, string>;
  compatibility: { status: "dev_compatibility_empirically_checked"; rows: number; maxAbsDiffVnd: number; rowsOverTolerance: number; toleranceVnd: number };
  routing: RoutingInfo[];
  evidence: { validationLiftPct: number[]; testLiftPct: number[]; testExposed: true; source: string; sourceSha256: string };
  limitations: string[];
}
export interface ClassifierHorizonModelInfo {
  horizon: ServedHorizon;
  task: "classification";
  modelName: string;
  family: string;
  datasetVersion: string;
  claimLevel: "none";
  bundleSha256: string;
  trainedEnvironment: Record<string, string>;
  validatedRuntime: Record<string, string>;
  classMapping: Record<string, string>;
  compatibility: { status: "dev_compatibility_empirically_checked"; rows: number; maxAbsProbabilityDiff: number; labelMismatchCount: number; toleranceProbability: number };
  coverage: { snapshotRecords: number; recordIdsLinkedToRegression: true };
  limitations: string[];
}
export interface ClassifierModelInfo {
  available: boolean;
  reason: ClassifierReason | null;
  claim: "none";
  note: string;
  horizons: ClassifierHorizonModelInfo[];
}
export interface ModelInfoResponse {
  snapshot: SnapshotInfo;
  horizons: HorizonModelInfo[];
  unsupportedHorizons: { horizon: Horizon; reason: "model_not_available" }[];
  classifier: ClassifierModelInfo;
  rules: { name: string; description: string; threshold: number }[];
  notes: string[];
}

export interface ApiErrorBody { error: { code: string; message: string; details?: Record<string, unknown> } }

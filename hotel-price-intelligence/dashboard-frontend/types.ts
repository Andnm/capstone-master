/** Future API contract. Prices and deltas are VND; percentages are percentage points. */
export type InferenceMode = "cold_start" | "history_enriched";
export type ReferenceStatus = "proposed" | "approved";
export type ConfidenceTier = "low" | "medium" | "high";
export type Direction = "stable" | "up" | "down";
export type Horizon = 1 | 3 | 7 | 14;

export interface HorizonPrediction {
  horizon: Horizon;
  predictedPrice: number;
  expectedDelta: number;
  expectedPctChange: number;
  direction: Direction;
}
export interface RateConditions {
  breakfastIncluded: boolean | null;
  freeCancellation: boolean | null;
  payment: string;
}
export interface ForecastResponse {
  inferenceMode: InferenceMode;
  currentObservedAt: string;
  currentPrice: number;
  roomType: string;
  rateConditions: RateConditions;
  referenceStatus: ReferenceStatus;
  historyObservationCount: number;
  historySpanDays: number;
  supportedHorizons: Horizon[];
  confidenceTier: ConfidenceTier;
  predictions: HorizonPrediction[];
  warnings: string[];
  history: { date: string; price: number }[];
}
export interface Hotel {
  id: string;
  name: string;
  city: string;
  reviewScore: number;
}
export interface CompsetHotel extends Hotel {
  isMyHotel: boolean;
  currentPrice: number;
  observedAt: string;
  pctVsGroup: number;
  change7DaysPct: number;
  soldOut: boolean;
}
export interface HotelierAlert {
  id: string;
  severity: "attention" | "info";
  category: "position" | "market" | "availability";
  title: string;
  description: string;
}
export interface HotelierResponse {
  checkIn: string;
  myHotel: CompsetHotel;
  compset: CompsetHotel[];
  competitorAverage: number;
  priceRank: number;
  groupSize: number;
  soldOutCompetitorCount: number;
  checkInPrices: { checkIn: string; myPrice: number; competitorAverage: number }[];
  marketForecast: ForecastResponse;
  alerts: HotelierAlert[];
}
export interface ForecastRequest { hotelId: string; checkIn: string }
export interface DashboardConfig {
  mockToday: string;
  defaultCheckIn: string;
  hotelierMinDate: string;
  hotelierMaxDate: string;
  consumerMinDate: string;
  consumerMaxDate: string;
  dataLabel: string;
}

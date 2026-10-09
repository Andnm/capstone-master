/** The only data access module for pages/components. Replace this implementation to connect an API. */
import dayjs from "dayjs";
import { consumerHotels, makeConsumerForecast, makeHotelierData, mockConfig } from "./mock-data";
import type { DashboardConfig, ForecastRequest, ForecastResponse, Hotel, HotelierResponse } from "@/types";

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const copy = <T,>(value: T): T => structuredClone(value);
function validDate(value: string, min: string, max: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && dayjs(value).isValid() && dayjs(value).format("YYYY-MM-DD") === value && value >= min && value <= max;
}
export async function getDashboardConfig(): Promise<DashboardConfig> { await wait(80); return copy(mockConfig); }
export async function getConsumerHotels(): Promise<Hotel[]> { await wait(100); return copy(consumerHotels); }
export async function getHotelierDashboard(checkIn: string): Promise<HotelierResponse> {
  if (!validDate(checkIn, mockConfig.hotelierMinDate, mockConfig.hotelierMaxDate)) throw new Error("Ngày check-in phải nằm trong 14 ngày tới của dữ liệu mẫu.");
  await wait(250); return copy(makeHotelierData(checkIn));
}
export async function getForecast(request: ForecastRequest): Promise<ForecastResponse> {
  if (!validDate(request.checkIn, mockConfig.consumerMinDate, mockConfig.consumerMaxDate)) throw new Error("Ngoài phạm vi hỗ trợ (1-30 ngày tới)");
  await wait(250); return copy(makeConsumerForecast(request));
}

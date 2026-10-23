/**
 * Điểm truy cập dữ liệu DUY NHẤT của UI: gọi dịch vụ dashboard THẬT qua same-origin `/api/dashboard/<endpoint>` (Next rewrites → 127.0.0.1:8100).
 * Không mock, không fallback giả: lỗi mạng/timeout/HTTP/cấu trúc sai (guard `validateBody`, chỉ phần cần cho render) đều thành `ApiError` để UI hiện lỗi + thử lại.
 * Timeout dùng AbortController + setTimeout (KHÔNG phụ thuộc `AbortSignal.any/timeout`), có dọn timer/listener.
 * Chỉ chạy ở trình duyệt (các component đều "use client"); không có đường SSR gọi backend nên `next build` không cần backend.
 */
import type { DashboardConfig, ForecastResponse, HotelierResponse, HotelsResponse, ModelInfoResponse } from "@/types";
import { L } from "./labels";
import { validateBody, type Endpoint } from "./validate";

const BASE = "/api/dashboard";
const TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

/** Nối tín hiệu hủy của caller với timeout bằng MỘT controller; luôn gọi `cleanup()` để không rò timer/listener. */
function linkSignals(signal: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }
  return { signal: controller.signal, timedOut: () => timedOut, cleanup: () => { clearTimeout(timer); signal?.removeEventListener("abort", onAbort); } };
}

export async function fetchEndpoint<T>(endpoint: Endpoint, params: Record<string, string | undefined>, signal?: AbortSignal, timeoutMs: number = TIMEOUT_MS): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, value);
  const url = `${BASE}/${endpoint}${query.toString() ? `?${query.toString()}` : ""}`;
  const link = linkSignals(signal, timeoutMs);
  try {
    let response: Response;
    try {
      response = await fetch(url, { signal: link.signal, cache: "no-store", headers: { Accept: "application/json" } });
    } catch (caught) {
      if (signal?.aborted) throw caught;                                           // bị hủy chủ động (đổi lựa chọn/unmount): để hook bỏ qua
      if (link.timedOut()) throw new ApiError(0, "timeout", L.timeoutError);
      throw new ApiError(0, "network_error", L.networkError);
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch (caught) {
      if (signal?.aborted) throw caught;
      if (link.timedOut()) throw new ApiError(0, "timeout", L.timeoutError);
      throw new ApiError(response.status, "bad_response", response.status >= 500 ? L.serviceDown : L.badResponse);
    }
    if (!response.ok) {
      const error = isObject(body) && isObject(body.error) ? body.error : null;
      const message = error && typeof error.message === "string" ? error.message : response.status >= 500 ? L.serviceDown : L.badResponse;
      throw new ApiError(response.status, error && typeof error.code === "string" ? error.code : "http_error", message);
    }
    if (!validateBody(endpoint, body)) throw new ApiError(response.status, "bad_response", L.badResponse);
    return body as T;
  } finally {
    link.cleanup();
  }
}

export const getDashboardConfig = (signal?: AbortSignal) => fetchEndpoint<DashboardConfig>("config", {}, signal);
export const getHotels = (signal?: AbortSignal) => fetchEndpoint<HotelsResponse>("hotels", {}, signal);
export const getForecast = (req: { hotelId: string; checkIn: string }, signal?: AbortSignal) => fetchEndpoint<ForecastResponse>("forecast", { hotelId: req.hotelId, checkIn: req.checkIn }, signal);
export const getHotelier = (req: { hotelId: string; checkIn: string }, signal?: AbortSignal) => fetchEndpoint<HotelierResponse>("hotelier", { hotelId: req.hotelId, checkIn: req.checkIn }, signal);
export const getModelInfo = (signal?: AbortSignal) => fetchEndpoint<ModelInfoResponse>("model-info", {}, signal);

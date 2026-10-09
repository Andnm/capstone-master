/** Entirely fictional fixtures. No observed real data, model, Booking.com call, or randomness. */
import dayjs from "dayjs";
import type { DashboardConfig, ForecastRequest, ForecastResponse, Hotel, HotelierResponse, Horizon, HorizonPrediction } from "@/types";
import { addDays, directionFor, pct, shortDate } from "./format";

export const MOCK_TODAY = "2026-11-18";
const DEFAULT_CHECK_IN = "2026-11-28";
export const mockConfig: DashboardConfig = {
  mockToday: MOCK_TODAY,
  defaultCheckIn: DEFAULT_CHECK_IN,
  hotelierMinDate: addDays(MOCK_TODAY, 1),
  hotelierMaxDate: addDays(MOCK_TODAY, 14),
  consumerMinDate: addDays(MOCK_TODAY, 1),
  consumerMaxDate: addDays(MOCK_TODAY, 30),
  dataLabel: "Dữ liệu mẫu - chưa nối mô hình thật",
};
const myHotel: Hotel = { id: "suong-mai", name: "Khách sạn Sương Mai", city: "Đà Lạt", reviewScore: 8.7 };
const competitors = [
  { id: "thong-nho", name: "Khách sạn Thông Nhỏ", reviewScore: 7.8, price: 680000, change: -1.2 },
  { id: "may-ben-doi", name: "Khách sạn Mây Bên Đồi", reviewScore: 8.1, price: 780000, change: 0.8 },
  { id: "nang-tren-la", name: "Khách sạn Nắng Trên Lá", reviewScore: 8.4, price: 860000, change: 1.5 },
  { id: "doi-ngan", name: "Khách sạn Đồi Ngân", reviewScore: 8.3, price: 920000, change: 2.2 },
  { id: "mien-suoi", name: "Khách sạn Miền Suối", reviewScore: 8.8, price: 980000, change: 2.6 },
  { id: "vuon-may", name: "Khách sạn Vườn Mây", reviewScore: 8.9, price: 1050000, change: 3.1 },
  { id: "an-nhien-doi", name: "Khách sạn An Nhiên Đồi", reviewScore: 9.0, price: 1110000, change: 4.2 },
  { id: "ho-thong", name: "Khách sạn Hồ Thông", reviewScore: 9.2, price: 1226112, change: 3.8 },
];
export const consumerHotels: Hotel[] = [myHotel, { ...competitors[2], city: "Đà Lạt" }, { ...competitors[5], city: "Đà Lạt" }].map(({ id, name, city, reviewScore }) => ({ id, name, city, reviewScore }));
const observedAt = `${MOCK_TODAY}T09:00:00+07:00`;
const horizons: Horizon[] = [1, 3, 7, 14];
const caution = "Dự báo chỉ là minh họa. Confidence tier không phải xác suất; khi nối mô hình thật cần so sánh với giữ nguyên giá.";

function predictions(price: number, changes: number[]): HorizonPrediction[] {
  return horizons.map((horizon, index) => {
    const expectedPctChange = changes[index];
    const predictedPrice = Math.round(price * (1 + expectedPctChange / 100));
    return { horizon, predictedPrice, expectedPctChange, expectedDelta: predictedPrice - price, direction: directionFor(expectedPctChange) };
  });
}

export function makeConsumerForecast({ hotelId, checkIn }: ForecastRequest): ForecastResponse {
  const hotel = consumerHotels.find((h) => h.id === hotelId);
  if (!hotel) throw new Error("Khách sạn không nằm trong dữ liệu mẫu.");
  const enriched = hotelId === myHotel.id && checkIn === DEFAULT_CHECK_IN;
  const currentPrice = hotelId === myHotel.id ? 1120000 : hotelId === "nang-tren-la" ? 860000 : 1050000;
  const history = enriched
    ? [1050000, 1055000, 1060000, 1065000, 1070000, 1080000, 1085000, 1090000, 1100000, 1105000, 1110000, 1120000].map((price, i) => ({ date: addDays(MOCK_TODAY, i - 11), price }))
    : [{ date: MOCK_TODAY, price: currentPrice }];
  return {
    inferenceMode: enriched ? "history_enriched" : "cold_start", currentObservedAt: observedAt, currentPrice,
    roomType: "Phòng Superior · 2 người lớn · 1 đêm",
    rateConditions: { breakfastIncluded: true, freeCancellation: false, payment: "Thanh toán tại chỗ nghỉ" },
    referenceStatus: enriched ? "approved" : "proposed",
    historyObservationCount: enriched ? 12 : 1, historySpanDays: enriched ? 11 : 0,
    supportedHorizons: [...horizons], confidenceTier: enriched ? "medium" : "low",
    predictions: predictions(currentPrice, enriched ? [0.8, 2.7, 5.1, 6.4] : [0.5, -0.4, 1.2, 2.3]),
    warnings: enriched ? [caution] : ["Chưa có lịch sử của chuỗi này", "Phòng và điều kiện giá đang được đề xuất, chưa được duyệt.", caution],
    history,
  };
}

export function makeHotelierData(checkIn: string): HotelierResponse {
  const weekend = [0, 6].includes(dayjs(checkIn).day());
  const factor = checkIn === DEFAULT_CHECK_IN ? 1 : weekend ? 1.04 : 0.94;
  const quotes = competitors.map((hotel, i) => ({ ...hotel, currentPrice: Math.round(hotel.price * factor), soldOut: checkIn === DEFAULT_CHECK_IN && [1, 4].includes(i) }));
  const competitorAverage = quotes.reduce((sum, h) => sum + h.currentPrice, 0) / quotes.length;
  const mine = {
    ...myHotel, isMyHotel: true, currentPrice: Math.round(1120000 * factor), observedAt,
    pctVsGroup: 0, change7DaysPct: (1120000 / 1070000 - 1) * 100, soldOut: false,
  };
  mine.pctVsGroup = (mine.currentPrice / competitorAverage - 1) * 100;
  const compset = [...quotes.map((h) => ({ id: h.id, name: h.name, city: "Đà Lạt", reviewScore: h.reviewScore, isMyHotel: false, currentPrice: h.currentPrice, observedAt, pctVsGroup: (h.currentPrice / competitorAverage - 1) * 100, change7DaysPct: h.change, soldOut: h.soldOut })), mine];
  const soldOutCompetitorCount = quotes.filter((h) => h.soldOut).length;
  const marketForecast: ForecastResponse = {
    inferenceMode: "history_enriched", currentObservedAt: observedAt, currentPrice: competitorAverage,
    roomType: "Phòng đôi tiêu chuẩn · nhóm đối thủ", rateConditions: { breakfastIncluded: null, freeCancellation: null, payment: "Điều kiện giá khác nhau giữa khách sạn" },
    referenceStatus: "approved", historyObservationCount: 12, historySpanDays: 11,
    supportedHorizons: [...horizons], confidenceTier: "medium", predictions: predictions(competitorAverage, [0.8, 1.5, 4.6, 6.4]), warnings: [caution],
    history: [0.95, 0.952, 0.956, 0.96, 0.966, 0.971, 0.977, 0.982, 0.986, 0.989, 0.995, 1].map((factor, i) => ({ date: addDays(MOCK_TODAY, i - 11), price: Math.round(competitorAverage * factor) })),
  };
  return {
    checkIn, myHotel: mine, compset, competitorAverage,
    priceRank: compset.filter((h) => h.currentPrice < mine.currentPrice).length + 1, groupSize: compset.length, soldOutCompetitorCount,
    checkInPrices: Array.from({ length: 14 }, (_, i) => {
      const date = addDays(MOCK_TODAY, i + 1);
      const multiplier = date === DEFAULT_CHECK_IN ? 1 : [0, 6].includes(dayjs(date).day()) ? 1.04 : 0.94;
      return { checkIn: date, myPrice: Math.round(1120000 * multiplier), competitorAverage: competitors.reduce((sum, h) => sum + Math.round(h.price * multiplier), 0) / 8 };
    }),
    marketForecast,
    alerts: [
      { id: "position", severity: "attention", category: "position", title: "Giá của bạn đang ở nhóm cao", description: `Giá của bạn cao hơn trung bình nhóm ${pct(mine.pctVsGroup, false)} cho check-in ${shortDate(checkIn)}/2026. Cần đối chiếu phòng và điều kiện giá trước khi so sánh.` },
      { id: "market", severity: "info", category: "market", title: "Mặt bằng giá minh họa có xu hướng tăng", description: "Nhóm dự báo tăng +4,6% ở h7 và +6,4% ở h14 (kịch bản cuối tuần cao điểm). Không phải tín hiệu từ mô hình thật." },
      { id: "availability", severity: "info", category: "availability", title: `${soldOutCompetitorCount}/8 đối thủ hết phòng`, description: `Trạng thái mẫu cho check-in ${shortDate(checkIn)}/2026. Đây chỉ là tín hiệu tham khảo về nhu cầu, không đo được lượng đặt phòng.` },
    ],
  };
}

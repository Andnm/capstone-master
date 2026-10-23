/**
 * MỌI chuỗi người dùng thấy do FRONTEND tự định nghĩa (LABEL_POLICY v3). Chuỗi do API phát ra nằm ở backend `labels.py`.
 * Mức diễn đạt yếu nhất mà bằng chứng đỡ được: không "chính xác", "tối ưu", "nên đặt/chờ", "đã triển khai", "tin cậy cao/thấp".
 * `scripts/check-ui-labels.mjs` quét chuỗi/JSX text của app/, components/, lib/ theo cùng danh sách cụm cấm với backend.
 */
import type { Direction, ForecastResponse, HorizonPrediction, InferenceMode, PredictionMethod, UnsupportedHorizon } from "@/types";
import { longDate, pct } from "./format";

export const L = {
  brand: "RateScope",
  brandSub: "Hotel rate intelligence",
  navHotelier: "Chủ khách sạn",
  navConsumer: "Khách đặt phòng",
  navModel: "Về mô hình",
  skipLink: "Chuyển đến nội dung",
  footerBrandNote: "· Mô hình thử nghiệm",
  footerText: "Thông tin tham khảo dựa trên giá niêm yết Booking.com đã quan sát. Đây không phải trang đặt phòng và không cam kết tiết kiệm.",
  loading: "Đang tải dữ liệu…",
  loadingShort: "Đang tải…",
  retry: "Thử lại",
  errorTitle: "Không tải được dữ liệu",
  noData: "Chưa có dữ liệu để hiển thị.",
  emptyCatalog: "Dịch vụ dữ liệu không trả về khách sạn nào.",
  unavailable: "—",
  serviceDown: "Dịch vụ dữ liệu chưa chạy hoặc chưa sẵn sàng. Hãy khởi động dịch vụ dashboard rồi thử lại.",
  networkError: "Không kết nối được dịch vụ dữ liệu.",
  timeoutError: "Dịch vụ dữ liệu phản hồi quá lâu.",
  badResponse: "Phản hồi của dịch vụ dữ liệu không hợp lệ.",
  historyMissing: "— (chưa có lịch sử giá riêng)",
  noSoldOut: "Chưa có dữ liệu hết phòng.",
  notBookingSite: "Đây không phải trang đặt phòng và không cam kết tiết kiệm.",
  groupNote: "Nhóm so sánh dựa trên cùng thành phố và điểm đánh giá (metadata ngày xuất, không phải thời điểm quan sát). Không bảo đảm cùng loại phòng hay điều kiện bán.",
  groupInsufficient: "Chưa đủ khách sạn tương đồng (cùng thành phố, điểm đánh giá lệch ≤ 0,5) để tổng hợp.",
  anchorReviewMissing: "Khách sạn này chưa có điểm đánh giá trong metadata nên không lập được nhóm so sánh.",
  demoAnchorNote: "Chọn khách sạn của bạn (bản demo, chưa đăng nhập)",
  classifierTitle: "Phân loại thử nghiệm",
  classifierNote: "Phân loại thử nghiệm — chưa có claim cải thiện so với hướng suy ra từ hồi quy. Chỉ hiển thị lớp, không hiển thị xác suất.",
  classifierMissing: "Mô hình phân loại chưa khả dụng trong môi trường này.",
  classifierDisagree: "Hai mô hình chưa thống nhất về hướng.",
  signalTitle: "Tín hiệu tham khảo (thăm dò)",
  signalDisclaimer: "Không phải khuyến nghị giao dịch.",
  evidenceLine: "Bằng chứng: mô hình thử nghiệm (dev). Chưa có hiệu chỉnh độ tin cậy cho từng dự báo.",
  aboutModelCta: "Xem bằng chứng và giới hạn của mô hình",
  pickHotel: "Khách sạn",
  pickCity: "Thành phố",
  pickCheckIn: "Ngày check-in",
  allCities: "Tất cả thành phố",
  noSupportedDates: "Khách sạn này không có ngày nào được hỗ trợ.",
  hotelierHeading: "Hiểu thị trường.",
  hotelierHeading2: "Theo dõi vị thế giá.",
  consumerHeading: "Quan sát giá.",
  consumerHeading2: "Lên kế hoạch lưu trú.",
  consumerSub: "Tham khảo xu hướng giá niêm yết của một phòng cụ thể, không đặt phòng tại đây.",
  tableHotel: "Khách sạn",
  tableScore: "Điểm",
  tablePrice: "Giá niêm yết",
  tableVsGroup: "So với trung vị nhóm",
  table7days: "7 ngày",
  tableGap: "Lệch điểm",
  you: "Của bạn",
  kpiYourPrice: "Giá của bạn",
  kpiGroupMedian: "Trung vị nhóm so sánh",
  kpiVsGroup: "Chênh lệch với trung vị nhóm",
  kpiRank: "Thứ hạng giá",
  rankNote: "Xếp từ giá thấp đến cao (1 = rẻ nhất)",
  unsupportedPanel: "Mốc chưa hỗ trợ",
  methodPersistence: "Giữ nguyên giá đã quan sát (không dùng mô hình)",
  methodModel: "Mô hình thử nghiệm",
  chartObserved: "Giá đã quan sát",
  chartPredicted: "Ước lượng thăm dò",
  chartAnchor: "Khách sạn của bạn",
  chartGroup: "Trung vị nhóm",
  metadataMissing: "Thiếu metadata của khách sạn này.",
  noAlerts: "Không có tín hiệu vượt ngưỡng ở ngày check-in này.",
  alertsNotEvaluated: "Chưa đủ dữ liệu nhóm để đánh giá tín hiệu.",
  alertsNotEvaluatedNoReview: "Chưa lập được nhóm so sánh (thiếu điểm đánh giá của khách sạn này) nên chưa đánh giá tín hiệu.",
  alertsNote: "Các tín hiệu hỗ trợ quan sát, không đề xuất mức giá bán hay cam kết doanh thu.",
} as const;

export const unsupportedText = (u: UnsupportedHorizon): string =>
  u.reason === "model_not_available" ? `Chưa có mô hình cho ${u.horizon} ngày.` : `Ngày nhận phòng trước mốc ${u.horizon} ngày kể từ dữ liệu — không có dự báo.`;

export const stalenessBanner = (dataAsOf: string, days: number): string => {
  const age = days > 0 ? `cách hôm nay ${days} ngày` : days === 0 ? "là hôm nay" : "nằm sau ngày máy của bạn";
  return `Dữ liệu tới ${longDate(dataAsOf)} — ${age}. Đây là ước lượng thăm dò từ mô hình thử nghiệm, không phải khuyến nghị đặt phòng hay định giá.`;
};

export const forecastMeaning = (checkIn: string, horizons: number[], dataAsOf: string): string =>
  `Ước lượng thay đổi của giá niêm yết cho ngày nhận phòng ${longDate(checkIn)}, quan sát sau ${horizons.join(" và ")} ngày kể từ ${longDate(dataAsOf)}.`;

export const directionText = (direction: Direction): string =>
  direction === "up" ? "Giá niêm yết có thể tăng" : direction === "down" ? "Giá niêm yết có thể giảm" : "Giá niêm yết ít biến động";

export const directionPlain = (direction: Direction): string => (direction === "up" ? "Có thể tăng" : direction === "down" ? "Có thể giảm" : "Ít biến động");

export const directionShort = (direction: Direction): string => (direction === "stable" ? `${directionPlain(direction)} (±2%)` : directionPlain(direction));

export const modeText = (mode: InferenceMode): string => (mode === "history_enriched" ? "Có lịch sử giá riêng" : "Chưa đủ lịch sử (sơ bộ)");

export const methodText = (method: PredictionMethod): string => (method === "model" ? L.methodModel : L.methodPersistence);

export const horizonLabel = (h: number): string => `Sau ${h} ngày`;

/** Câu mô tả hướng theo từng mốc, trung tính; hướng lấy NGUYÊN từ API (không suy lại từ số làm tròn). */
export function signalSentences(predictions: HorizonPrediction[]): string[] {
  return predictions.map((p) => `Mốc ${p.horizon} ngày: ${directionPlain(p.direction).toLowerCase()}, thay đổi ước lượng ${pct(p.expectedPctChange)} (ngưỡng ±2%)`);
}

export function disagreement(forecast: ForecastResponse): string | null {
  if (!forecast.classifier.available) return null;
  const mismatch = forecast.classifier.results.some((r) => forecast.predictions.find((p) => p.horizon === r.horizon)?.direction !== r.label);
  return mismatch ? L.classifierDisagree : null;
}

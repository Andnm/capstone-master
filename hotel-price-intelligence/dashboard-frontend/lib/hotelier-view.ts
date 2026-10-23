import type { HotelierResponse } from "@/types";
import { L } from "./labels";

export type AlertsPanel =
  | { kind: "not_evaluated"; badge: "—"; message: string }
  | { kind: "evaluated_none"; badge: "0"; message: string }
  | { kind: "evaluated_some"; badge: string; message: null };

/**
 * Trạng thái ô "Tín hiệu". Thiếu dữ liệu nhóm (insufficient) ⇒ CHƯA ĐÁNH GIÁ — không được trình bày là "không có tín hiệu" (một kết quả âm tính đã hoàn tất).
 * Chỉ khi nhóm đủ điều kiện mới phân biệt "không có tín hiệu vượt ngưỡng" với "có tín hiệu".
 */
export function alertsPanel(data: Pick<HotelierResponse, "groupSummary" | "alerts">): AlertsPanel {
  if (data.groupSummary.insufficient) {
    return { kind: "not_evaluated", badge: "—", message: data.groupSummary.reason === "anchor_review_missing" ? L.alertsNotEvaluatedNoReview : L.alertsNotEvaluated };
  }
  if (data.alerts.length === 0) return { kind: "evaluated_none", badge: "0", message: L.noAlerts };
  return { kind: "evaluated_some", badge: String(data.alerts.length), message: null };
}

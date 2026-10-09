import { ArrowDownRight, ArrowUpRight, ArrowRight, Info, History, Sparkles } from "lucide-react";
import type { Direction, ForecastResponse } from "@/types";
import { pct, vnd } from "@/lib/format";

export function DirectionMark({ direction, change }: { direction: Direction; change: number }) {
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : ArrowRight;
  return <span className={`direction trend-${direction}`}><Icon size={15} aria-hidden="true" />{pct(change)}<span>{direction === "up" ? "Tăng" : direction === "down" ? "Giảm" : "Ổn định (±2%)"}</span></span>;
}

export function ConfidenceBadge({ forecast }: { forecast: ForecastResponse }) {
  const names = { low: "Thấp", medium: "Trung bình", high: "Cao" };
  return <span className={`badge confidence-${forecast.confidenceTier}`}><span className="badge-dot" aria-hidden="true" />{forecast.confidenceTier} · {names[forecast.confidenceTier]}</span>;
}

export function ModeBadge({ forecast }: { forecast: ForecastResponse }) {
  const history = forecast.inferenceMode === "history_enriched";
  const Icon = history ? History : Sparkles;
  return <span className={`badge ${history ? "badge-history" : "badge-cold"}`}><Icon size={14} aria-hidden="true" />{history ? "history_enriched · Có lịch sử" : "cold_start · Đánh giá sơ bộ"}</span>;
}

export function ForecastCards({ forecast }: { forecast: ForecastResponse }) {
  return <div className="forecast-grid">
    {forecast.supportedHorizons.map((horizon) => {
      const prediction = forecast.predictions.find((p) => p.horizon === horizon);
      return <article className="forecast-card" key={horizon}>
        <span className="horizon-label">h{horizon}<span>Sau {horizon} ngày</span></span>
        {prediction ? <><strong className="forecast-price">{vnd(prediction.predictedPrice)}</strong><DirectionMark direction={prediction.direction} change={prediction.expectedPctChange} /></> : <p className="muted">Chưa có dự báo</p>}
      </article>;
    })}
  </div>;
}

export function ForecastCaution({ warnings }: { warnings: string[] }) {
  return <div className="forecast-caution"><Info size={16} aria-hidden="true" /><div>{warnings.map((warning) => <p key={warning}>{warning}</p>)}</div></div>;
}

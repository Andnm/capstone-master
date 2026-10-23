import { ArrowDownRight, ArrowRight, ArrowUpRight, History, Info, Sparkles } from "lucide-react";
import type { ApiWarning, Direction, ForecastResponse, Horizon, InferenceMode } from "@/types";
import { longDate, pct, vnd } from "@/lib/format";
import { directionShort, disagreement, horizonLabel, L, methodText, modeText, unsupportedText } from "@/lib/labels";

export function DirectionMark({ direction, change }: { direction: Direction; change: number }) {
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : ArrowRight;
  return <span className={`direction trend-${direction}`}><Icon size={15} aria-hidden="true" />{pct(change)}<span>{directionShort(direction)}</span></span>;
}

export function ModeBadge({ mode }: { mode: InferenceMode }) {
  const history = mode === "history_enriched";
  const Icon = history ? History : Sparkles;
  return <span className={`badge ${history ? "badge-history" : "badge-cold"}`}><Icon size={14} aria-hidden="true" />{modeText(mode)}</span>;
}

const SLOTS: Horizon[] = [1, 3, 7, 14];

/** Luôn đủ 4 ô 1/3/7/14: mốc có dự báo hiển thị giá; mốc không hỗ trợ ghi rõ lý do (không giả lập). */
export function ForecastCards({ forecast }: { forecast: ForecastResponse }) {
  return <div className="forecast-grid">
    {SLOTS.map((horizon) => {
      const prediction = forecast.predictions.find((p) => p.horizon === horizon);
      if (prediction) {
        return <article className="forecast-card" key={horizon}>
          <span className="horizon-label">{horizonLabel(horizon)}<span>{longDate(prediction.targetObservationDate)}</span></span>
          <strong className="forecast-price">{vnd(prediction.predictedPrice)}</strong>
          <DirectionMark direction={prediction.direction} change={prediction.expectedPctChange} />
          <small className="method-note">{methodText(prediction.method)}</small>
        </article>;
      }
      const unsupported = forecast.unsupportedHorizons.find((u) => u.horizon === horizon);
      return <article className="forecast-card forecast-card-off" key={horizon}>
        <span className="horizon-label">{horizonLabel(horizon)}</span>
        <p className="muted">{unsupported ? unsupportedText(unsupported) : L.unavailable}</p>
      </article>;
    })}
  </div>;
}

export function WarningList({ warnings, extra = [] }: { warnings: ApiWarning[]; extra?: string[] }) {
  const messages = [...warnings.map((w) => w.message), ...extra];
  if (messages.length === 0) return null;
  return <div className="forecast-caution"><Info size={16} aria-hidden="true" /><div>{messages.map((m) => <p key={m}>{m}</p>)}</div></div>;
}

export function ClassifierPanel({ forecast }: { forecast: ForecastResponse }) {
  const { classifier } = forecast;
  const mismatch = disagreement(forecast);
  return <section className="panel classifier-panel">
    <div className="section-title"><div><span className="section-kicker">{L.classifierTitle.toUpperCase()}</span><h2>Lớp hướng giá (phụ)</h2></div></div>
    {!classifier.available || classifier.results.length === 0
      ? <p className="muted">{L.classifierMissing}</p>
      : <ul className="classifier-list">{classifier.results.map((r) => <li key={r.horizon}><span>{horizonLabel(r.horizon)}</span><strong>{directionShort(r.label)}</strong></li>)}</ul>}
    {mismatch && <p className="field-hint">{mismatch}</p>}
    <p className="field-hint">{L.classifierNote}</p>
  </section>;
}

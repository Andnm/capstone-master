import { CircleAlert, LoaderCircle, Inbox } from "lucide-react";
import type { ApiError } from "@/lib/api";
import { L } from "@/lib/labels";

export function LoadingBlock({ label = L.loading }: { label?: string }) {
  return <div className="state-box" role="status" aria-live="polite"><LoaderCircle size={18} className="spin" aria-hidden="true" /><span>{label}</span></div>;
}

export function ErrorBlock({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  return <div className="state-box state-error" role="alert">
    <CircleAlert size={18} aria-hidden="true" />
    <div><strong>{L.errorTitle}</strong><p>{error.message}</p></div>
    {onRetry && <button type="button" className="secondary-button" onClick={onRetry}>{L.retry}</button>}
  </div>;
}

export function EmptyBlock({ text = L.noData }: { text?: string }) {
  return <div className="state-box"><Inbox size={18} aria-hidden="true" /><span>{text}</span></div>;
}

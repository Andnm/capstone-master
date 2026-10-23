"use client";
import { useEffect } from "react";
import { L } from "@/lib/labels";

/** Ranh giới lỗi cho lỗi render không lường (Next 16: `unstable_retry`). Lỗi API đã được xử lý trong từng màn hình, không đi qua đây. */
export default function Error({ error, unstable_retry }: { error: Error & { digest?: string }; unstable_retry: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return <div className="state-box state-error" role="alert"><div><strong>{L.errorTitle}</strong><p>Đã xảy ra lỗi hiển thị.</p></div><button type="button" className="secondary-button" onClick={() => unstable_retry()}>{L.retry}</button></div>;
}

import Link from "next/link";
import { Eye } from "lucide-react";
import { listRuns } from "@/lib/api";
import { formatDateTime } from "@/utils/format";
import { RUN_STATUS_BADGE_CLASS, RUN_STATUS_LABEL, type RunStatus } from "@/utils/status";

export default async function JobsPage() {
  const runs = await listRuns(50);

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Lịch sử job</h1>
          <p className="mt-1 text-sm text-muted">{runs.length} job gần nhất</p>
        </div>
        <Link
          href="/"
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:opacity-90"
        >
          + Cào mới
        </Link>
      </div>

      <div className="mt-6 overflow-hidden rounded-xl border border-border bg-surface">
        {runs.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted">Chưa có job nào.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-background text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-3 font-medium">Job</th>
                <th className="px-4 py-3 font-medium">Loại</th>
                <th className="px-4 py-3 font-medium">Tạo lúc</th>
                <th className="px-4 py-3 font-medium">Trạng thái</th>
                <th className="px-4 py-3 font-medium">Tiến độ</th>
                <th className="px-4 py-3 font-medium text-right">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.map((run) => (
                <tr key={run.id} className="transition hover:bg-background">
                  <td className="px-4 py-3 font-medium">#{run.id}</td>
                  <td className="px-4 py-3 text-muted">
                    {run.trigger_type === "manual" ? "Thủ công" : "Tự động"}
                  </td>
                  <td className="px-4 py-3 text-muted">{formatDateTime(run.created_at)}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${RUN_STATUS_BADGE_CLASS[run.status as RunStatus]}`}
                    >
                      {RUN_STATUS_LABEL[run.status as RunStatus] ?? run.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {run.processed}/{run.total}
                    {run.total > 0 && (
                      <span className="text-xs"> ({Math.round((run.processed / run.total) * 100)}%)</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/jobs/${run.id}`}
                      title="Xem chi tiết"
                      className="inline-flex items-center justify-center rounded-lg border border-border p-1.5 text-muted transition hover:border-accent hover:text-accent"
                    >
                      <Eye size={16} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}

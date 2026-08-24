import Link from "next/link";
import { Eye } from "lucide-react";
import { listRuns } from "@/lib/api";
import { formatDateTime } from "@/utils/format";
import { getPaginationItems } from "@/utils/pagination";
import { getRunStatusBadgeClass, getRunStatusLabel } from "@/utils/status";

const PAGE_SIZE = 20;

function pageHref(page: number): string {
  return page <= 1 ? "/jobs" : `/jobs?page=${page}`;
}

export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string | string[] }>;
}) {
  const rawPage = (await searchParams).page;
  const requestedPage = Number(Array.isArray(rawPage) ? rawPage[0] : rawPage);
  const page = Number.isInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const result = await listRuns(PAGE_SIZE, (page - 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
  const runs = result.items;

  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Lịch sử job</h1>
          <p className="mt-1 text-sm text-muted">Tổng cộng {result.total} job</p>
        </div>
        <Link
          href="/"
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:opacity-90"
        >
          + Cào mới
        </Link>
      </div>

      <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
        {runs.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted">Chưa có job nào.</p>
        ) : (
          <table className="min-w-[860px] w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-background text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-3 font-medium">Job</th>
                <th className="px-4 py-3 font-medium">Loại</th>
                <th className="px-4 py-3 font-medium">Tạo lúc</th>
                <th className="px-4 py-3 font-medium">Kết thúc lúc</th>
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
                  <td className="px-4 py-3 text-muted">
                    {run.finished_at ? formatDateTime(run.finished_at) : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${getRunStatusBadgeClass(run)}`}
                    >
                      {getRunStatusLabel(run)}
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

      {result.total > PAGE_SIZE && (
        <nav className="mt-4 flex items-center justify-between gap-3" aria-label="Phân trang lịch sử job">
          {page > 1 ? (
            <Link
              href={pageHref(page - 1)}
              className="cursor-pointer rounded-lg border border-border px-3 py-2 text-sm transition hover:border-accent hover:text-accent"
            >
              ← Trang trước
            </Link>
          ) : (
            <span className="cursor-pointer rounded-lg border border-border px-3 py-2 text-sm text-muted opacity-50">
              ← Trang trước
            </span>
          )}
          <div className="flex min-w-0 items-center gap-1 overflow-x-auto py-1">
            {getPaginationItems(Math.min(page, totalPages), totalPages).map((item, index) =>
              item === "ellipsis" ? (
                <span key={`ellipsis-${index}`} className="px-2 text-sm text-muted" aria-hidden="true">
                  …
                </span>
              ) : (
                <Link
                  key={item}
                  href={pageHref(item)}
                  aria-current={item === page ? "page" : undefined}
                  aria-label={`Trang ${item}`}
                  className={`cursor-pointer inline-flex h-9 min-w-9 items-center justify-center rounded-lg border px-2 text-sm transition ${
                    item === page
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border hover:border-accent hover:text-accent"
                  }`}
                >
                  {item}
                </Link>
              ),
            )}
          </div>
          {page < totalPages ? (
            <Link
              href={pageHref(page + 1)}
              className="cursor-pointer rounded-lg border border-border px-3 py-2 text-sm transition hover:border-accent hover:text-accent"
            >
              Trang sau →
            </Link>
          ) : (
            <span className="cursor-pointer rounded-lg border border-border px-3 py-2 text-sm text-muted opacity-50">
              Trang sau →
            </span>
          )}
        </nav>
      )}
    </main>
  );
}

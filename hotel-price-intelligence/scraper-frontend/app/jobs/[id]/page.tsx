"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import * as Tooltip from "@radix-ui/react-tooltip";
import { ChevronDown, ChevronRight, Download, ExternalLink, Info, SquareArrowOutUpRight } from "lucide-react";
import { getRun, getRunItems, exportRunUrl, type CrawlRun, type CrawlRunItem, type RoomObservation } from "@/lib/api";
import { formatDate, formatDateTime } from "@/utils/format";
import {
  ITEM_STATUS_BADGE_CLASS,
  ITEM_STATUS_LABEL,
  RUN_STATUS_BADGE_CLASS,
  RUN_STATUS_LABEL,
  type ItemStatus,
  type RunStatus,
} from "@/utils/status";
import RoomDetailModal from "@/components/RoomDetailModal";

const POLL_INTERVAL_MS = 25_000;

function estimateRemaining(run: CrawlRun): string | null {
  if (run.status !== "running" || !run.started_at || run.processed === 0) return null;
  const elapsedMs = Date.now() - new Date(run.started_at).getTime();
  const perItemMs = elapsedMs / run.processed;
  const remainingItems = run.total - run.processed;
  const remainingMs = perItemMs * remainingItems;
  const remainingMin = Math.round(remainingMs / 60000);
  if (remainingMin < 1) return "dưới 1 phút";
  if (remainingMin < 60) return `~${remainingMin} phút`;
  return `~${(remainingMin / 60).toFixed(1)} giờ`;
}

function formatVnd(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("vi-VN") + " đ";
}

function ReferenceRoomBadge() {
  return (
    <Tooltip.Provider delayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span className="ml-1.5 inline-flex cursor-help items-center gap-0.5 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent">
            phòng tham chiếu <Info size={10} />
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            sideOffset={6}
            className="max-w-xs rounded-lg bg-foreground px-3 py-2 text-xs text-background shadow-lg"
          >
            Phòng rẻ nhất cho ≤2 khách trong số các phòng cào được — dùng làm giá đại diện xuyên
            suốt các lần cào để so sánh được với nhau.
            <Tooltip.Arrow className="fill-foreground" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

function RoomsTable({
  rooms,
  onViewDetail,
}: {
  rooms: RoomObservation[];
  onViewDetail: (room: RoomObservation) => void;
}) {
  if (rooms.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted">Không có dữ liệu phòng (hết phòng).</p>;
  }
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left uppercase tracking-wide text-muted">
          <th className="px-3 py-2 font-medium">Loại phòng</th>
          <th className="px-3 py-2 font-medium">Giá/đêm</th>
          <th className="px-3 py-2 font-medium">Giá gốc</th>
          <th className="px-3 py-2 font-medium">Khách tối đa</th>
          <th className="px-3 py-2 font-medium">Giường</th>
          <th className="px-3 py-2 font-medium text-right">Chi tiết</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rooms.map((room, i) => (
          <tr key={i} className={room.is_reference_room ? "bg-accent/5" : ""}>
            <td className="px-3 py-2">
              {room.room_type_raw}
              {room.is_reference_room && <ReferenceRoomBadge />}
            </td>
            <td className="px-3 py-2 font-medium">{formatVnd(room.price_per_night)}</td>
            <td className="px-3 py-2 text-muted line-through">{formatVnd(room.original_price)}</td>
            <td className="px-3 py-2 text-muted">{room.max_occupancy ?? "—"}</td>
            <td className="px-3 py-2 text-muted">{room.bed_config ?? "—"}</td>
            <td className="px-3 py-2 text-right">
              <button
                type="button"
                onClick={() => onViewDetail(room)}
                title="Xem đầy đủ thông tin phòng này"
                className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-border p-1 text-muted transition hover:border-accent hover:text-accent"
              >
                <SquareArrowOutUpRight size={13} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = Number(params.id);
  const [run, setRun] = useState<CrawlRun | null>(null);
  const [items, setItems] = useState<CrawlRunItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [detailTarget, setDetailTarget] = useState<{ item: CrawlRunItem; room: RoomObservation } | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchOnce() {
      try {
        const [runData, itemsData] = await Promise.all([getRun(runId), getRunItems(runId)]);
        if (!cancelled) {
          setRun(runData);
          setItems(itemsData);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Lỗi tải tiến độ");
      }
    }

    fetchOnce();
    const interval = setInterval(fetchOnce, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId]);

  function toggleExpanded(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const columns = useMemo<ColumnDef<CrawlRunItem>[]>(
    () => [
      {
        id: "expand",
        header: "",
        cell: ({ row }) => {
          const item = row.original;
          if (item.status === "error") return null;
          const isOpen = expandedIds.has(item.id);
          return (
            <button
              type="button"
              onClick={() => toggleExpanded(item.id)}
              className="cursor-pointer text-muted hover:text-accent"
              aria-label={isOpen ? "Thu gọn" : "Xem chi tiết phòng"}
            >
              {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </button>
          );
        },
      },
      {
        accessorFn: (item) => item.hotel_name || item.hotel_name_hint || item.hotel_id || "—",
        id: "hotel_name",
        header: "Khách sạn",
        cell: ({ row }) => {
          const item = row.original;
          return (
            <div>
              <p className="font-medium">{item.hotel_name || item.hotel_name_hint || item.hotel_id || "—"}</p>
              {(item.hotel_address || item.hotel_review_score) && (
                <p className="mt-0.5 text-xs text-muted">
                  {item.hotel_address && <span>{item.hotel_address}</span>}
                  {item.hotel_review_score && (
                    <span>
                      {item.hotel_address && " · "}⭐ {item.hotel_review_score} ({item.hotel_review_count ?? 0})
                    </span>
                  )}
                </p>
              )}
              <a
                href={item.hotel_link}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 inline-flex items-center gap-1 text-xs text-accent hover:underline"
                title={item.hotel_link}
              >
                Link đã cào <ExternalLink size={11} />
              </a>
            </div>
          );
        },
      },
      {
        accessorKey: "checkin_date",
        header: "Checkin",
        cell: ({ getValue }) => formatDate(getValue<string>()),
      },
      {
        accessorKey: "hotel_city",
        header: "Khu vực",
        cell: ({ getValue }) => getValue<string | null>() ?? "—",
      },
      {
        accessorKey: "status",
        header: "Trạng thái",
        cell: ({ getValue }) => {
          const status = getValue<ItemStatus>();
          return (
            <span
              className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${ITEM_STATUS_BADGE_CLASS[status]}`}
            >
              {ITEM_STATUS_LABEL[status] ?? status}
            </span>
          );
        },
      },
      {
        accessorKey: "error_message",
        header: "Ghi chú",
        cell: ({ row }) => (row.original.status === "error" ? row.original.error_message || "—" : "—"),
      },
    ],
    [expandedIds],
  );

  const table = useReactTable({
    data: items,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const percent = run && run.total > 0 ? Math.round((run.processed / run.total) * 100) : 0;
  const eta = run ? estimateRemaining(run) : null;

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <Link href="/jobs" className="text-sm text-muted underline">
        ← Lịch sử job
      </Link>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">Job #{runId}</h1>
          {run && (
            <span
              className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${RUN_STATUS_BADGE_CLASS[run.status as RunStatus]}`}
            >
              {RUN_STATUS_LABEL[run.status as RunStatus] ?? run.status}
            </span>
          )}
        </div>
        {run && run.success_count > 0 && (
          <a
            href={exportRunUrl(runId)}
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:opacity-90"
          >
            <Download size={16} /> Xuất Excel
          </a>
        )}
      </div>

      {error && <p className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}

      {run && (
        <>
          <div className="mt-6 rounded-xl border border-border bg-surface p-5">
            <p className="text-sm text-muted">
              {run.date_mode === "explicit"
                ? `Ngày checkin chọn tay: ${(run.checkin_dates ?? []).map(formatDate).join(", ")}`
                : `Lead-time tự động: ${run.lead_time_buckets ?? ""} ngày`}
            </p>

            <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-background">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
            <p className="mt-2 text-sm text-muted">
              {run.processed}/{run.total} ({percent}%) — thành công{" "}
              <span className="text-emerald-600">{run.success_count}</span>, lỗi{" "}
              <span className="text-red-600">{run.error_count}</span>
            </p>

            {eta && <p className="mt-1 text-sm text-muted">Ước tính còn lại: {eta}</p>}

            {run.status === "failed" && run.error_message && (
              <p className="mt-2 text-sm text-red-600">Lỗi: {run.error_message}</p>
            )}

            <p className="mt-4 text-xs text-muted">
              Tự làm mới mỗi {POLL_INTERVAL_MS / 1000}s khi đang mở. Có thể đóng tab bất cứ lúc
              nào — job vẫn chạy nền, quay lại trang này để xem tiếp.
            </p>
          </div>

          <h2 className="mt-8 text-lg font-semibold">Chi tiết từng khách sạn / ngày</h2>
          <p className="mt-1 text-sm text-muted">
            Bấm mũi tên để xem nhanh các phòng đã cào được. Bấm icon ở cột "Chi tiết" trong bảng
            phòng để xem đầy đủ mọi thông tin (giá gốc, giường, diện tích, huỷ miễn phí, số phòng
            còn lại...) — hoặc xuất Excel để có toàn bộ dữ liệu.
          </p>
          <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
            {items.length === 0 ? (
              <p className="p-8 text-center text-sm text-muted">Chưa có kết quả nào.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <tr
                      key={headerGroup.id}
                      className="border-b border-border bg-background text-left text-xs uppercase tracking-wide text-muted"
                    >
                      {headerGroup.headers.map((header) => (
                        <th
                          key={header.id}
                          className="px-4 py-3 font-medium select-none"
                          onClick={header.column.getToggleSortingHandler()}
                          style={{ cursor: header.column.getCanSort() ? "pointer" : undefined }}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {{ asc: " ↑", desc: " ↓" }[header.column.getIsSorted() as string] ?? ""}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody className="divide-y divide-border">
                  {table.getRowModel().rows.map((row) => (
                    <Fragment key={row.id}>
                      <tr className="hover:bg-background">
                        {row.getVisibleCells().map((cell) => (
                          <td key={cell.id} className="px-4 py-3 align-top">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                      {expandedIds.has(row.original.id) && row.original.status !== "error" && (
                        <tr className="bg-background">
                          <td colSpan={columns.length} className="px-2 py-2">
                            <RoomsTable
                              rooms={row.original.rooms}
                              onViewDetail={(room) => setDetailTarget({ item: row.original, room })}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <p className="mt-2 text-xs text-muted">Cào lúc: {formatDateTime(run.started_at)}</p>
        </>
      )}

      <RoomDetailModal
        item={detailTarget?.item ?? null}
        room={detailTarget?.room ?? null}
        onClose={() => setDetailTarget(null)}
      />
    </main>
  );
}

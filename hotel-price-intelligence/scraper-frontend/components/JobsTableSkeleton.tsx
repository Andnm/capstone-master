import Skeleton from "./Skeleton";

export default function JobsTableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex border-b border-border bg-background px-4 py-3 text-left text-xs uppercase tracking-wide text-muted">
        <span className="w-16">Job</span>
        <span className="w-20">Loại</span>
        <span className="w-32">Tạo lúc</span>
        <span className="w-32">Kết thúc lúc</span>
        <span className="w-24">Trạng thái</span>
        <span className="w-20">Tiến độ</span>
        <span className="ml-auto w-20 text-right">Hành động</span>
      </div>
      <div className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 w-10" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="ml-auto h-6 w-6 rounded-lg" />
          </div>
        ))}
      </div>
    </div>
  );
}

import Skeleton from "./Skeleton";

export default function JobItemsTableSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div>
      <div className="flex border-b border-border bg-background px-4 py-3 text-left text-xs uppercase tracking-wide text-muted">
        <span className="w-4" />
        <span className="ml-4 w-40">Khách sạn</span>
        <span className="ml-4 w-20">Checkin</span>
        <span className="ml-4 w-16">Khu vực</span>
        <span className="ml-4 w-20">Trạng thái</span>
        <span className="ml-4 w-32">Phát hiện → Parse → DB</span>
        <span className="ml-4 w-20">Reference</span>
        <span className="ml-4 w-16">Ghi chú</span>
      </div>
      <div className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 w-4" />
            <div className="w-40">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="mt-1.5 h-3 w-3/4" />
            </div>
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 w-12" />
          </div>
        ))}
      </div>
    </div>
  );
}

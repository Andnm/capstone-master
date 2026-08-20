import Skeleton from "./Skeleton";

export default function JobItemsTableSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <table className="min-w-[900px] w-full text-sm">
      <thead>
        <tr className="border-b border-border bg-background text-left text-xs uppercase tracking-wide text-muted">
          <th className="px-4 py-3 font-medium" />
          <th className="px-4 py-3 font-medium">Khách sạn</th>
          <th className="px-4 py-3 font-medium">Checkin</th>
          <th className="px-4 py-3 font-medium">Khu vực</th>
          <th className="px-4 py-3 font-medium">Trạng thái</th>
          <th className="px-4 py-3 font-medium">Phát hiện → Parse → DB</th>
          <th className="px-4 py-3 font-medium">Reference</th>
          <th className="px-4 py-3 font-medium">Ghi chú</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, index) => (
          <tr key={index}>
            <td className="px-4 py-3"><Skeleton className="h-4 w-4" /></td>
            <td className="px-4 py-3">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="mt-1.5 h-3 w-56" />
            </td>
            <td className="px-4 py-3"><Skeleton className="h-4 w-20" /></td>
            <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
            <td className="px-4 py-3"><Skeleton className="h-5 w-20 rounded-full" /></td>
            <td className="px-4 py-3"><Skeleton className="h-4 w-24" /></td>
            <td className="px-4 py-3"><Skeleton className="h-4 w-20" /></td>
            <td className="px-4 py-3"><Skeleton className="h-4 w-12" /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

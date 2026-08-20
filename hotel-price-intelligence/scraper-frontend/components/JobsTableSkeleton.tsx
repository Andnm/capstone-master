import Skeleton from "./Skeleton";

export default function JobsTableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-surface">
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
          {Array.from({ length: rows }).map((_, index) => (
            <tr key={index}>
              <td className="px-4 py-3"><Skeleton className="h-4 w-10" /></td>
              <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
              <td className="px-4 py-3"><Skeleton className="h-4 w-28" /></td>
              <td className="px-4 py-3"><Skeleton className="h-4 w-28" /></td>
              <td className="px-4 py-3"><Skeleton className="h-5 w-20 rounded-full" /></td>
              <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
              <td className="px-4 py-3 text-right"><Skeleton className="ml-auto h-6 w-6 rounded-lg" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

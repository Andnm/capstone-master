import Skeleton from "./Skeleton";
import JobItemsTableSkeleton from "./JobItemsTableSkeleton";

export default function JobDetailSkeleton() {
  return (
    <>
      <div className="mt-6 rounded-xl border border-border bg-surface p-4 sm:p-5">
        <Skeleton className="h-4 w-72" />
        <Skeleton className="mt-4 h-2 w-full rounded-full" />
        <Skeleton className="mt-3 h-4 w-96" />
        <Skeleton className="mt-4 h-3 w-80" />
      </div>

      <Skeleton className="mt-8 h-6 w-64" />
      <Skeleton className="mt-2 h-4 w-full max-w-xl" />

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Skeleton className="h-[3.25rem] w-40 rounded-lg" />
        <Skeleton className="h-[3.25rem] w-40 rounded-lg" />
      </div>

      <div className="mt-3 overflow-x-auto rounded-xl border border-border bg-surface">
        <JobItemsTableSkeleton />
      </div>
    </>
  );
}

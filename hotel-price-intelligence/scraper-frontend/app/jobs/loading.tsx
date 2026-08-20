import Skeleton from "@/components/Skeleton";
import JobsTableSkeleton from "@/components/JobsTableSkeleton";

export default function Loading() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Skeleton className="h-7 w-40" />
          <Skeleton className="mt-2 h-4 w-24" />
        </div>
        <Skeleton className="h-9 w-28 rounded-lg" />
      </div>
      <JobsTableSkeleton />
    </main>
  );
}

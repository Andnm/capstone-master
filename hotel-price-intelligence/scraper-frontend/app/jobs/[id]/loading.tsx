import Skeleton from "@/components/Skeleton";
import JobDetailSkeleton from "@/components/JobDetailSkeleton";

export default function Loading() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
      <Skeleton className="h-4 w-24" />
      <div className="mt-3 flex items-center gap-3">
        <Skeleton className="h-8 w-28" />
        <Skeleton className="h-5 w-20 rounded-full" />
      </div>
      <JobDetailSkeleton />
    </main>
  );
}

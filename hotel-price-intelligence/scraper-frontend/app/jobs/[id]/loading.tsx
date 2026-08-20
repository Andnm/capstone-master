import LoadingState from "@/components/LoadingState";

export default function Loading() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
      <LoadingState message="Đang tải job…" />
    </main>
  );
}

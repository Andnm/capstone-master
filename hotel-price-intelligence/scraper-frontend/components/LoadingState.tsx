import Spinner from "./Spinner";

export default function LoadingState({ message = "Đang tải…" }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-sm text-muted">
      <Spinner className="h-6 w-6 text-accent" />
      <p>{message}</p>
    </div>
  );
}

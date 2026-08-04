"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { CrawlRunItem, RoomObservation } from "@/lib/api";
import { formatDate } from "@/utils/format";

function formatVnd(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("vi-VN") + " đ";
}

function yesNo(value: boolean | null): string {
  if (value === null || value === undefined) return "—";
  return value ? "Có" : "Không";
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm">{value ?? "—"}</dd>
    </div>
  );
}

export default function RoomDetailModal({
  item,
  room,
  onClose,
}: {
  item: CrawlRunItem | null;
  room: RoomObservation | null;
  onClose: () => void;
}) {
  const open = !!item && !!room;

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[90vw] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-surface p-6 shadow-xl">
          {item && room && (
            <>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <Dialog.Title className="text-lg font-semibold">
                    {item.hotel_name || item.hotel_name_hint}
                  </Dialog.Title>
                  <Dialog.Description className="mt-1 text-sm text-muted">
                    {room.room_type_raw} — checkin {formatDate(item.checkin_date)}
                  </Dialog.Description>
                </div>
                <Dialog.Close className="rounded-lg p-1.5 text-muted hover:bg-background hover:text-foreground">
                  <X size={18} />
                </Dialog.Close>
              </div>

              <section className="mt-5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Khách sạn</h3>
                <dl className="mt-2 grid grid-cols-2 gap-4">
                  <Field label="Địa chỉ" value={item.hotel_address} />
                  <Field label="Khu vực" value={item.hotel_city} />
                  <Field
                    label="Điểm review"
                    value={
                      item.hotel_review_score
                        ? `${item.hotel_review_score} (${item.hotel_review_count ?? 0} đánh giá)`
                        : "—"
                    }
                  />
                  <Field
                    label="Link đã cào"
                    value={
                      <a
                        href={item.hotel_link}
                        target="_blank"
                        rel="noreferrer"
                        className="text-accent hover:underline"
                      >
                        Mở trên Booking.com
                      </a>
                    }
                  />
                </dl>
              </section>

              <section className="mt-5 border-t border-border pt-5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                  Phòng {room.is_reference_room && <span className="text-accent">(phòng tham chiếu)</span>}
                </h3>
                <dl className="mt-2 grid grid-cols-2 gap-4">
                  <Field label="Loại phòng (gốc)" value={room.room_type_raw} />
                  <Field label="Loại phòng (chuẩn hoá)" value={room.room_type_norm} />
                  <Field label="Giá/đêm" value={formatVnd(room.price_per_night)} />
                  <Field label="Giá gốc" value={formatVnd(room.original_price)} />
                  <Field label="% giảm" value={room.discount_percent ? `${room.discount_percent}%` : "—"} />
                  <Field label="Khách tối đa" value={room.max_occupancy} />
                  <Field label="Giường" value={room.bed_config} />
                  <Field label="Diện tích" value={room.room_area} />
                  <Field label="Bao gồm bữa sáng" value={yesNo(room.breakfast_included)} />
                  <Field label="Huỷ miễn phí" value={yesNo(room.free_cancellation)} />
                  <Field label="Số phòng còn lại" value={room.rooms_left} />
                  <Field label="Trạng thái" value={room.availability_status} />
                </dl>
                {room.cancellation_policy && (
                  <div className="mt-4">
                    <dt className="text-xs uppercase tracking-wide text-muted">Chính sách huỷ (nguyên văn)</dt>
                    <dd className="mt-0.5 text-sm">{room.cancellation_policy}</dd>
                  </div>
                )}
              </section>

              
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type CrawlRun = {
  id: number;
  status: "queued" | "running" | "completed" | "failed";
  trigger_type: "scheduled" | "manual";
  source_file: string | null;
  date_mode: "lead_time" | "explicit";
  lead_time_buckets: string | null;
  checkin_dates: string[] | null;
  total: number;
  processed: number;
  success_count: number;
  error_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
};

export type UploadResponse = {
  run_id: number;
  status: string;
  message: string;
};

export type RoomObservation = {
  room_type_raw: string | null;
  room_type_norm: string | null;
  is_reference_room: boolean;
  price_total: number | null;
  price_per_night: number | null;
  original_price: number | null;
  discount_percent: number | null;
  max_occupancy: number | null;
  bed_config: string | null;
  room_area: string | null;
  breakfast_included: boolean | null;
  free_cancellation: boolean | null;
  cancellation_policy: string | null;
  rooms_left: number | null;
  availability_status: string;
};

export type CrawlRunItem = {
  id: number;
  crawl_run_id: number;
  hotel_link: string;
  hotel_name_hint: string | null;
  hotel_name: string | null;
  hotel_id: string | null;
  hotel_city: string | null;
  hotel_address: string | null;
  hotel_review_score: number | null;
  hotel_review_count: number | null;
  checkin_date: string;
  status: "success" | "sold_out" | "error";
  error_message: string | null;
  created_at: string;
  rooms: RoomObservation[];
};

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || fallback;
  } catch {
    return fallback;
  }
}

export async function uploadHotelList(file: File, checkinDates: string[]): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("checkin_dates", checkinDates.join(","));
  const res = await fetch(`${API_BASE}/api/scraper/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res, "Upload thất bại"));
  }
  return res.json();
}

export async function getRun(runId: number): Promise<CrawlRun> {
  const res = await fetch(`${API_BASE}/api/scraper/runs/${runId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được tiến độ job"));
  return res.json();
}

export async function listRuns(limit = 20): Promise<CrawlRun[]> {
  const res = await fetch(`${API_BASE}/api/scraper/runs?limit=${limit}`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được danh sách job"));
  return res.json();
}

export async function getRunItems(runId: number): Promise<CrawlRunItem[]> {
  const res = await fetch(`${API_BASE}/api/scraper/runs/${runId}/items`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được chi tiết job"));
  return res.json();
}

export function exportRunUrl(runId: number): string {
  return `${API_BASE}/api/scraper/runs/${runId}/export`;
}

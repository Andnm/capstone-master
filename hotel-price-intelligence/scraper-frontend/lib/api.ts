const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export type CrawlRun = {
  id: number;
  status: "queued" | "running" | "completed" | "failed";
  trigger_type: "scheduled" | "manual";
  source_file: string | null;
  source_original_filename: string | null;
  source_file_sha256: string | null;
  source_file_size: number | null;
  save_artifacts: boolean;
  crawl_context: Record<string, unknown> | null;
  scraper_version: string | null;
  selector_version: string | null;
  storage_timezone: string;
  retry_of_run_id: number | null;
  date_mode: "lead_time" | "explicit";
  lead_time_buckets: string | null;
  checkin_dates: string[] | null;
  total: number;
  processed: number;
  success_count: number;
  partial_count: number;
  sold_out_count: number;
  not_bookable_count: number;
  error_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
};

export type CrawlRunPage = {
  items: CrawlRun[];
  total: number;
  limit: number;
  offset: number;
};

export type UploadResponse = {
  run_id: number;
  status: string;
  message: string;
};

export type PreflightResult = {
  total_rows: number;
  valid_links: number;
  invalid_rows: Array<{ sheet: string; row: number; name: string; reason: string | null }>;
  duplicate_rows: Array<{
    sheet: string;
    row: number;
    name: string;
    duplicate_of: string | null;
  }>;
  sheets: Array<{
    name: string;
    city: string | null;
    in_scope: boolean;
    total_rows: number;
    valid_links: number;
  }>;
  search_context: string;
};

export type RoomObservation = {
  room_type_raw: string | null;
  room_type_norm: string | null;
  is_reference_room: boolean;
  price_total: number | null;
  price_per_night: number | null;
  original_price: number | null;
  discount_percent: number | null;
  taxes_fees: number | null;
  price_includes_tax: boolean | null;
  room_option_index: number;
  room_option_key: string;
  room_identity_key: string | null;
  rate_plan_key: string | null;
  reference_definition_id: number | null;
  reference_match_status: string;
  reference_match_score: number | null;
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
  source_hotel_link: string;
  requested_hotel_link: string | null;
  hotel_link: string;
  hotel_name_hint: string | null;
  hotel_name: string | null;
  hotel_id: string | null;
  hotel_city: string | null;
  hotel_address: string | null;
  hotel_review_score: number | null;
  hotel_review_count: number | null;
  checkin_date: string;
  checkout_date: string;
  status: "queued" | "running" | "success" | "partial" | "sold_out" | "not_bookable" | "error";
  attempt_count: number;
  claimed_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  worker_id: string | null;
  last_error_code: string | null;
  dom_room_row_count: number;
  candidate_rate_count: number;
  parsed_options_count: number;
  rejected_options_count: number;
  duplicate_options_count: number;
  raw_options_count: number;
  saved_options_count: number;
  parse_warning_count: number;
  rejected_options: Array<{ reason_code: string; row_index: number; message?: string }> | null;
  reference_match_status: string;
  driver_start_ms: number | null;
  page_load_ms: number | null;
  availability_wait_ms: number | null;
  parse_ms: number | null;
  db_write_ms: number | null;
  item_total_ms: number | null;
  artifact_html_path: string | null;
  screenshot_path: string | null;
  error_message: string | null;
  created_at: string;
  rooms: RoomObservation[];
};

export type CrawlRunItemPage = {
  items: CrawlRunItem[];
  total: number;
  limit: number;
  offset: number;
  markets: string[];
};

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || fallback;
  } catch {
    return fallback;
  }
}

export type WorkerHealth = {
  online: boolean;
  waiting_for_network: boolean;
  message: string | null;
  worker_id: string | null;
  status: string | null;
  heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  current_item_id: number | null;
  scraper_version: string | null;
  status_reason: string | null;
  paused_at: string | null;
  next_probe_at: string | null;
  network_failure_count: number;
};

export async function uploadHotelList(
  file: File,
  checkinDates: string[],
  saveArtifacts = false,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("checkin_dates", checkinDates.join(","));
  form.append("save_artifacts", String(saveArtifacts));
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/scraper/upload`, { method: "POST", body: form });
  } catch {
    throw new Error(
      "Không kết nối được backend. Hãy kiểm tra backend đang chạy ở cổng 8000 và cấu hình CORS.",
    );
  }
  if (!res.ok) {
    throw new Error(await readErrorDetail(res, "Upload thất bại"));
  }
  return res.json();
}

export async function getWorkerHealth(): Promise<WorkerHealth> {
  const res = await fetch(`${API_BASE}/api/scraper/worker/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được trạng thái worker"));
  return res.json();
}

export async function retryFailedItems(runId: number): Promise<UploadResponse> {
  const res = await fetch(`${API_BASE}/api/scraper/runs/${runId}/retry`, { method: "POST" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không tạo được job retry"));
  return res.json();
}

export async function preflightHotelList(file: File): Promise<PreflightResult> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/scraper/preflight`, { method: "POST", body: form });
  } catch {
    throw new Error(
      "Không kết nối được backend để kiểm tra file. Hãy kiểm tra backend đang chạy ở cổng 8000.",
    );
  }
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không kiểm tra được file Excel"));
  return res.json();
}

export async function getRun(runId: number): Promise<CrawlRun> {
  const res = await fetch(`${API_BASE}/api/scraper/runs/${runId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được tiến độ job"));
  return res.json();
}

export async function listRuns(limit = 20, offset = 0): Promise<CrawlRunPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const res = await fetch(`${API_BASE}/api/scraper/runs?${params}`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được danh sách job"));
  return res.json();
}

export async function getRunItems(
  runId: number,
  options: { limit?: number; offset?: number; market?: string; status?: string } = {},
): Promise<CrawlRunItemPage> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  });
  if (options.market) params.set("market", options.market);
  if (options.status) params.set("status", options.status);
  const res = await fetch(`${API_BASE}/api/scraper/runs/${runId}/items?${params}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await readErrorDetail(res, "Không lấy được chi tiết job"));
  return res.json();
}

export function exportRunUrl(runId: number): string {
  return `${API_BASE}/api/scraper/runs/${runId}/export`;
}

export function artifactUrl(itemId: number, kind: "html" | "screenshot"): string {
  return `${API_BASE}/api/scraper/items/${itemId}/artifact/${kind}`;
}

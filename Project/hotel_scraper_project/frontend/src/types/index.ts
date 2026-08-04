export interface Hotel {
  id?: number
  hotel_name: string
  hotel_link: string
  price_after_discount?: number
  price_original?: number
  discount_percent?: string
  review_count?: number
  review_score?: number
  room_type: string
  num_people?: number
  bed_info: string
  room_area: string
  room_choices?: string
  popular_facilities?: string
  check_in?: string
  check_out?: string
  options: Record<string, any>
}

export interface CrawlHistory {
  id: number
  crawl_date: string
  source: string
  scrape_type: 'info' | 'price'
  total_records: number
  created_at: string
}

export interface LinkInfo {
  row: number
  col: string
  link: string
  cell_value: string
  hotel_name?: string
  is_valid: boolean
  market: string
  note?: string
  status?: string
}

export interface DateRange {
  checkin: string
  checkout: string
}

export interface ScrapeRequest {
  links: LinkInfo[]
  date_ranges: DateRange[]
  source: string
  scrape_type?: 'info' | 'price'
  market?: string | null
}

export interface ScrapeProgress {
  type: 'started' | 'progress' | 'success' | 'error' | 'completed' | 'date_range_start'
  current?: number
  total?: number
  status?: string
  message: string
  hotel_name?: string
  row?: number
  history_id?: number
  total_success?: number
  total_errors?: number
  results?: any[]
  errors?: any[]
  date_range?: DateRange
  date_index?: number
  total_dates?: number
  rooms_count?: number
}

export interface HistoryListResponse {
  items: CrawlHistory[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface HistoryDetailResponse {
  history: CrawlHistory
  data: Hotel[]
  total_records: number
  page: number
  page_size: number
  total_pages: number
}

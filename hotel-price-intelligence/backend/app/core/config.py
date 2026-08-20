from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "hotel_price_intel"
    DB_USER: str = "root"
    DB_PASSWORD: str

    DB_POOL_SIZE: int = 5

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    APP_TITLE: str = "Hotel Price Intelligence API"
    APP_VERSION: str = "1.0.0"

    UPLOAD_DIR: str = "./uploaded_files"
    ARTIFACT_DIR: str = "./crawl_artifacts"
    DEFAULT_HOTEL_LIST_PATH: str = "./default_hotel_list.xlsx"

    DEFAULT_STAY_NIGHTS: int = 1
    DEFAULT_LEAD_TIME_BUCKETS: str = "1,3,7,14,30,60"
    STALE_RUN_MINUTES: int = 120
    WORKER_POLL_SECONDS: int = 3
    WORKER_LEASE_SECONDS: int = 180
    WORKER_MAX_ATTEMPTS: int = 2
    DRIVER_BATCH_SIZE: int = 10
    NETWORK_FAILURE_THRESHOLD: int = 3
    NETWORK_PROBE_BACKOFF_SECONDS: str = "30,60,120,300"
    NETWORK_RECOVERY_SUCCESSES: int = 2
    NETWORK_RECOVERY_CONFIRM_SECONDS: int = 15
    NETWORK_PROBE_TIMEOUT_SECONDS: int = 10
    NETWORK_FAILURE_REQUEUE_SECONDS: int = 15
    REFERENCE_MIN_RUNS: int = 3
    REFERENCE_MIN_COVERAGE: float = 0.80
    UPLOAD_RETENTION_DAYS: int = 90
    ARTIFACT_RETENTION_DAYS: int = 30
    SCRAPER_VERSION: str = "2.3.0"
    SELECTOR_VERSION: str = "booking-2026-08-17"
    DISPLAY_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    PROXY_SERVER: str = ""
    PROXY_USERNAME: str = ""
    PROXY_PASSWORD: str = ""

    # Booking có thể chèn thêm loại phòng (không chỉ thêm rate của phòng đã thấy) trễ hơn cả
    # phút so với lúc bảng phòng "trông có vẻ" đã ổn định - phát hiện 2026-08-20 khi audit qua
    # proxy VN (thêm 1 chặng relay khiến request tải phòng phụ bị chậm hơn kết nối trực tiếp).
    # Tăng so với mặc định cũ (timeout=20s, minimum_wait=8s, 4 vòng ổn định) để giảm khả năng cắt
    # ngang trước khi các loại phòng tải chậm kịp xuất hiện.
    AVAILABILITY_WAIT_TIMEOUT_SECONDS: float = 35.0
    AVAILABILITY_WAIT_MINIMUM_SECONDS: float = 12.0
    AVAILABILITY_WAIT_STABLE_ROUNDS: int = 6

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def default_lead_time_list(self) -> List[int]:
        return [int(x.strip()) for x in self.DEFAULT_LEAD_TIME_BUCKETS.split(",") if x.strip()]

    @property
    def network_probe_backoff_list(self) -> List[int]:
        return [
            int(x.strip()) for x in self.NETWORK_PROBE_BACKOFF_SECONDS.split(",")
            if x.strip()
        ]

settings = Settings()

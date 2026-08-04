from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "hotel_price_intel"
    DB_USER: str = "root"
    DB_PASSWORD: str

    DB_POOL_SIZE: int = 5

    CORS_ORIGINS: str = "http://localhost:3000"

    APP_TITLE: str = "Hotel Price Intelligence API"
    APP_VERSION: str = "1.0.0"

    UPLOAD_DIR: str = "./uploaded_files"
    DEFAULT_HOTEL_LIST_PATH: str = "./default_hotel_list.xlsx"

    DEFAULT_STAY_NIGHTS: int = 1
    DEFAULT_LEAD_TIME_BUCKETS: str = "1,3,7,14,30,60"
    STALE_RUN_MINUTES: int = 120

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def default_lead_time_list(self) -> List[int]:
        return [int(x.strip()) for x in self.DEFAULT_LEAD_TIME_BUCKETS.split(",") if x.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()

from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "hotel_scraper"
    DB_USER: str = "root"
    DB_PASSWORD: str
    
    DB_POOL_SIZE: int = 10
    
    CORS_ORIGINS: str = "http://localhost:5173"
    
    APP_TITLE: str = "Hotel Data Scraper API"
    APP_VERSION: str = "2.0.0"
    
    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

settings = Settings()

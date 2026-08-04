from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import scraper, history, sources, config, competitor, tracking

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description="Hotel Data Scraper API with WebSocket support"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scraper.router, tags=["Scraper"])
app.include_router(history.router, tags=["History"])
app.include_router(sources.router, tags=["Sources"])
app.include_router(config.router, tags=["Config"])
app.include_router(competitor.router, tags=["Competitors"])
app.include_router(tracking.router, prefix="/api/tracking", tags=["Tracking"])

@app.get("/")
async def root():
    return {
        "message": "Hotel Data Scraper API",
        "version": settings.APP_VERSION,
        "endpoints": {
            "scraper_ws": "/ws/scrape",
            "histories": "/histories",
            "api_data": "/api"
        }
    }

@app.get("/health")
async def health_check():
    from app.core.database import test_connection
    db_ok, db_msg = test_connection()
    
    return {
        "status": "healthy" if db_ok else "unhealthy",
        "database": db_msg
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

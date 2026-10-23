"""Entry RIÊNG của dịch vụ dashboard (KHÔNG phải `main.py` vận hành). Loopback, cổng 8100, một worker, không --reload.

    cd hotel-price-intelligence\\backend
    C:\\Users\\ADMIN\\anaconda3\\python.exe -B -m uvicorn dashboard_main:app --host 127.0.0.1 --port 8100

Chạy được cả bằng `backend\\venv` (chỉ cần fastapi/pydantic/uvicorn). Không import `app.core`, không DB, không .env.
"""
from app.dashboard.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    from app.dashboard.settings import load_settings

    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_level="info")

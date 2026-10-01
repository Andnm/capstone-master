from contextlib import contextmanager
from mysql.connector import pooling
from app.core.config import settings

_pool = pooling.MySQLConnectionPool(
    pool_name="hotel_price_intel_pool",
    pool_size=settings.DB_POOL_SIZE,
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    database=settings.DB_NAME,
    user=settings.DB_USER,
    password=settings.DB_PASSWORD,
    time_zone='+00:00',
    autocommit=False,
)


@contextmanager
def get_db_connection():
    conn = _pool.get_connection()
    try:
        yield conn
    finally:
        conn.close()

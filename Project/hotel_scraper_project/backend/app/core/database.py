import mysql.connector
from mysql.connector import pooling
from contextlib import contextmanager
from typing import Generator
from app.core.config import settings

class DatabaseManager:
    _instance = None
    _connection_pool = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._connection_pool is None:
            self._initialize_pool()
    
    def _initialize_pool(self):
        try:
            self._connection_pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name="hotel_scraper_pool",
                pool_size=settings.DB_POOL_SIZE,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                database=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                autocommit=False
            )
        except Exception as e:
            raise Exception(f"Error creating connection pool: {str(e)}")
    
    def get_connection(self):
        try:
            if self._connection_pool is None:
                self._initialize_pool()
            return self._connection_pool.get_connection()
        except Exception as e:
            raise Exception(f"Error getting connection: {str(e)}")
    
    def close_all_connections(self):
        try:
            if self._connection_pool:
                self._connection_pool = None
        except Exception as e:
            print(f"Error closing connections: {str(e)}")

db_manager = DatabaseManager()

def get_db():
    """
    Get database connection from the pool.
    Returns the connection for direct use.
    """
    return db_manager.get_connection()

@contextmanager
def get_db_connection() -> Generator:
    conn = db_manager.get_connection()
    try:
        yield conn
    finally:
        conn.close()

def test_connection() -> tuple[bool, str]:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True, "Connection successful"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"

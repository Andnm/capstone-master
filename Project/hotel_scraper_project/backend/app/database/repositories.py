import json
from datetime import datetime, date
from typing import List, Dict, Optional
from app.core.database import get_db_connection

class CrawlHistoryRepository:
    
    def create_history(
        self, 
        crawl_date: date, 
        source: str = 'booking',
        scrape_type: str = 'info'
    ) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                from datetime import datetime
                crawl_time = datetime.now().time()
                
                query = """
                    INSERT INTO crawl_history 
                    (crawl_date, crawl_time, source, scrape_type, total_records)
                    VALUES (%s, %s, %s, %s, 0)
                """
                cursor.execute(query, (
                    crawl_date, 
                    crawl_time,
                    source, 
                    scrape_type
                ))
                conn.commit()
                history_id = cursor.lastrowid
                return history_id
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error creating history: {str(e)}")
            finally:
                cursor.close()
    
    def get_all_histories(
        self, 
        limit: int = 50, 
        offset: int = 0, 
        source_filter: Optional[str] = None, 
        date_from: Optional[date] = None, 
        date_to: Optional[date] = None,
        scrape_type: Optional[str] = None
    ) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM crawl_history WHERE 1=1"
                params = []
                
                if source_filter and source_filter.lower() != "tất cả":
                    query += " AND source = %s"
                    params.append(source_filter.lower())
                
                if date_from:
                    query += " AND crawl_date >= %s"
                    params.append(date_from)
                
                if date_to:
                    query += " AND crawl_date <= %s"
                    params.append(date_to)

                if scrape_type and scrape_type.lower() != "all":
                    query += " AND scrape_type = %s"
                    params.append(scrape_type.lower())
                
                query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                
                cursor.execute(query, params)
                results = cursor.fetchall()
                return results
            except Exception as e:
                raise Exception(f"Error getting histories: {str(e)}")
            finally:
                cursor.close()
    
    def get_history_by_id(self, history_id: int) -> Optional[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM crawl_history WHERE id = %s"
                cursor.execute(query, (history_id,))
                result = cursor.fetchone()
                return result
            except Exception as e:
                raise Exception(f"Error getting history: {str(e)}")
            finally:
                cursor.close()
    
    def update_total_records(self, history_id: int, total_records: int):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "UPDATE crawl_history SET total_records = %s WHERE id = %s"
                cursor.execute(query, (total_records, history_id))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error updating total records: {str(e)}")
            finally:
                cursor.close()
    
    def delete_history(self, history_id: int):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "DELETE FROM crawl_history WHERE id = %s"
                cursor.execute(query, (history_id,))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error deleting history: {str(e)}")
            finally:
                cursor.close()
    
    def get_total_count(
        self, 
        source_filter: Optional[str] = None, 
        date_from: Optional[date] = None, 
        date_to: Optional[date] = None,
        scrape_type: Optional[str] = None
    ) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "SELECT COUNT(*) FROM crawl_history WHERE 1=1"
                params = []
                
                if source_filter and source_filter.lower() != "tất cả":
                    query += " AND source = %s"
                    params.append(source_filter.lower())
                
                if date_from:
                    query += " AND crawl_date >= %s"
                    params.append(date_from)
                
                if date_to:
                    query += " AND crawl_date <= %s"
                    params.append(date_to)

                if scrape_type and scrape_type.lower() != "all":
                    query += " AND scrape_type = %s"
                    params.append(scrape_type.lower())
                
                cursor.execute(query, params)
                count = cursor.fetchone()[0]
                return count
            except Exception as e:
                raise Exception(f"Error getting count: {str(e)}")
            finally:
                cursor.close()

    def get_latest_history(self, source: Optional[str] = None, scrape_type: Optional[str] = None) -> Optional[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM crawl_history WHERE 1=1"
                params = []

                if source:
                    query += " AND source = %s"
                    params.append(source.lower())

                if scrape_type and scrape_type.lower() != 'all':
                    query += " AND scrape_type = %s"
                    params.append(scrape_type.lower())

                query += " ORDER BY created_at DESC LIMIT 1"

                cursor.execute(query, params)
                result = cursor.fetchone()
                return result
            except Exception as e:
                raise Exception(f"Error getting latest history: {str(e)}")
            finally:
                cursor.close()


class CrawlDataRepository:
    
    def save_batch_data(self, history_id: int, data_list: List[Dict]) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                values = []
                for data in data_list:
                    price_after = self._parse_numeric(data.get('Giá sau giảm', 'N/A'))
                    price_orig = self._parse_numeric(data.get('Giá gốc', 'N/A'))
                    review_count = self._parse_int(data.get('Số lượng review', 'N/A'))
                    review_score = self._parse_float(data.get('Điểm review', 'N/A'))
                    num_people = self._parse_int(data.get('Số lượng người', 'N/A'))
                    
                    # Get check-in/check-out dates
                    check_in = data.get('Check in', None)
                    check_out = data.get('Check out', None)
                    
                    options = {
                        'row_number': data.get('Hàng_gốc'),
                        'facilities': data.get('Các lựa chọn', 'N/A'),
                        'target_date': data.get('Ngày cần cào', '')
                    }
                    
                    popular_facilities = data.get('Các tiện nghi được ưa chuộng nhất', '')
                    if isinstance(popular_facilities, list):
                        popular_facilities = ', '.join(popular_facilities)
                    
                    room_choices = data.get('Các lựa chọn', '')
                    if isinstance(room_choices, list):
                        room_choices = ', '.join(room_choices)
                    
                    values.append((
                        history_id,
                        data.get('Tên khách sạn', 'N/A'),
                        data.get('Link khách sạn', ''),
                        popular_facilities,
                        price_after,
                        price_orig,
                        data.get('Giảm giá', ''),
                        review_count,
                        review_score,
                        data.get('Tên hạng phòng', 'N/A'),
                        num_people,
                        data.get('Giường', 'N/A'),
                        data.get('Diện tích phòng', 'N/A'),
                        room_choices,
                        check_in,
                        check_out,
                        json.dumps(options, ensure_ascii=False)
                    ))
                
                query = """
                    INSERT INTO crawl_data (
                        history_id, hotel_name, hotel_link, popular_facilities,
                        price_after_discount, price_original, discount_percent,
                        review_count, review_score, room_type,
                        num_people, bed_info, room_area, room_choices,
                        check_in, check_out, options
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                cursor.executemany(query, values)
                conn.commit()
                rows_inserted = cursor.rowcount
                return rows_inserted
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error saving batch data: {str(e)}")
            finally:
                cursor.close()
    
    def get_data_by_history(self, history_id: int, limit: int = 100, offset: int = 0) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT 
                        cd.*,
                        ch.crawl_date,
                        ch.crawl_time,
                        ch.source,
                        ch.scrape_type,
                        cl.market AS Market,
                        cl.cluster AS Cluster,
                        cl.competitor_level AS `Level đối thủ`,
                        cl.breakfast_included AS `Giá bao gồm bữa sáng`,
                        cl.room_group AS `Nhóm hạng phòng`,
                        cl.level AS Level
                    FROM crawl_data cd
                    JOIN crawl_history ch ON cd.history_id = ch.id
                    LEFT JOIN competitor_list cl ON 
                        TRIM(cd.hotel_name) = TRIM(cl.hotel_name) 
                        AND (
                            cl.room_type IS NULL 
                            OR cl.room_type = '' 
                            OR TRIM(cd.room_type) = TRIM(cl.room_type)
                        )
                    WHERE cd.history_id = %s 
                    ORDER BY cd.id 
                    LIMIT %s OFFSET %s
                """
                cursor.execute(query, (history_id, limit, offset))
                results = cursor.fetchall()
                
                for row in results:
                    if row['options'] and isinstance(row['options'], str):
                        row['options'] = json.loads(row['options'])
                
                return results
            except Exception as e:
                raise Exception(f"Error getting data: {str(e)}")
            finally:
                cursor.close()
    
    def get_data_count(self, history_id: int) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "SELECT COUNT(*) FROM crawl_data WHERE history_id = %s"
                cursor.execute(query, (history_id,))
                count = cursor.fetchone()[0]
                return count
            except Exception as e:
                raise Exception(f"Error getting data count: {str(e)}")
            finally:
                cursor.close()
    
    def export_data_by_history(self, history_id: int) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT 
                        cd.*,
                        ch.crawl_date,
                        ch.crawl_time,
                        ch.source,
                        ch.scrape_type,
                        cl.market AS Market,
                        cl.cluster AS Cluster,
                        cl.competitor_level AS `Level đối thủ`,
                        cl.breakfast_included AS `Giá bao gồm bữa sáng`,
                        cl.room_group AS `Nhóm hạng phòng`,
                        cl.level AS Level
                    FROM crawl_data cd
                    JOIN crawl_history ch ON cd.history_id = ch.id
                    LEFT JOIN competitor_list cl ON 
                        TRIM(cd.hotel_name) = TRIM(cl.hotel_name) 
                        AND (
                            cl.room_type IS NULL 
                            OR cl.room_type = '' 
                            OR TRIM(cd.room_type) = TRIM(cl.room_type)
                        )
                    WHERE cd.history_id = %s
                    ORDER BY cd.id
                """
                cursor.execute(query, (history_id,))
                results = cursor.fetchall()
                
                for row in results:
                    if row['options'] and isinstance(row['options'], str):
                        row['options'] = json.loads(row['options'])
                
                return results
            except Exception as e:
                raise Exception(f"Error exporting data: {str(e)}")
            finally:
                cursor.close()

    def export_all_data(self) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT 
                        cd.*,
                        ch.crawl_date,
                        ch.crawl_time,
                        ch.source,
                        ch.scrape_type,
                        cl.market AS Market,
                        cl.cluster AS Cluster,
                        cl.competitor_level AS `Level đối thủ`,
                        cl.breakfast_included AS `Giá bao gồm bữa sáng`,
                        cl.room_group AS `Nhóm hạng phòng`,
                        cl.level AS Level
                    FROM crawl_data cd
                    JOIN crawl_history ch ON cd.history_id = ch.id
                    LEFT JOIN competitor_list cl ON 
                        TRIM(cd.hotel_name) = TRIM(cl.hotel_name) 
                        AND (
                            cl.room_type IS NULL 
                            OR cl.room_type = '' 
                            OR TRIM(cd.room_type) = TRIM(cl.room_type)
                        )
                    ORDER BY ch.created_at, cd.id
                """
                cursor.execute(query)
                results = cursor.fetchall()
                
                for row in results:
                    if row['options'] and isinstance(row['options'], str):
                        row['options'] = json.loads(row['options'])
                
                return results
            except Exception as e:
                raise Exception(f"Error exporting all data: {str(e)}")
            finally:
                cursor.close()

    def export_data_by_filters(
        self,
        source_filter: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None
    ) -> List[Dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT 
                        cd.*,
                        ch.crawl_date,
                        ch.crawl_time,
                        ch.source,
                        ch.scrape_type,
                        cl.market AS Market,
                        cl.cluster AS Cluster,
                        cl.competitor_level AS `Level đối thủ`,
                        cl.breakfast_included AS `Giá bao gồm bữa sáng`,
                        cl.room_group AS `Nhóm hạng phòng`,
                        cl.level AS Level
                    FROM crawl_data cd
                    JOIN crawl_history ch ON cd.history_id = ch.id
                    LEFT JOIN competitor_list cl ON 
                        TRIM(cd.hotel_name) = TRIM(cl.hotel_name) 
                        AND (
                            cl.room_type IS NULL 
                            OR cl.room_type = '' 
                            OR TRIM(cd.room_type) = TRIM(cl.room_type)
                        )
                    WHERE 1=1
                """
                params = []

                if source_filter and source_filter.lower() != "tất cả":
                    query += " AND ch.source = %s"
                    params.append(source_filter.lower())

                if date_from:
                    query += " AND ch.crawl_date >= %s"
                    params.append(date_from)

                if date_to:
                    query += " AND ch.crawl_date <= %s"
                    params.append(date_to)

                query += " ORDER BY ch.crawl_date, cd.id"

                cursor.execute(query, params)
                results = cursor.fetchall()
                
                for row in results:
                    if row['options'] and isinstance(row['options'], str):
                        row['options'] = json.loads(row['options'])
                
                return results
            except Exception as e:
                raise Exception(f"Error exporting filtered data: {str(e)}")
            finally:
                cursor.close()
    
    @staticmethod
    def _parse_numeric(value):
        if value in ['N/A', '', None]:
            return None
        try:
            cleaned = ''.join(c for c in str(value) if c.isdigit() or c == '.')
            return float(cleaned) if cleaned else None
        except:
            return None
    
    @staticmethod
    def _parse_int(value):
        if value in ['N/A', '', None]:
            return None
        try:
            cleaned = ''.join(c for c in str(value) if c.isdigit())
            return int(cleaned) if cleaned else None
        except:
            return None
    
    @staticmethod
    def _parse_float(value):
        if value in ['N/A', '', None]:
            return None
        try:
            return float(value)
        except:
            return None


class SavedDataSourceRepository:
    
    def create_source(self, name: str, source_type: str, file_path: str = None, sheets_url: str = None) -> int:
        """Tạo saved data source mới"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    INSERT INTO saved_data_sources (name, source_type, file_path, sheets_url, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                """
                cursor.execute(query, (name, source_type, file_path, sheets_url))
                conn.commit()
                source_id = cursor.lastrowid
                return source_id
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error creating saved source: {str(e)}")
            finally:
                cursor.close()
    
    def get_all_sources(self, active_only: bool = True) -> List[Dict]:
        """Lấy tất cả saved sources"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM saved_data_sources"
                if active_only:
                    query += " WHERE is_active = TRUE"
                query += " ORDER BY created_at DESC"
                
                cursor.execute(query)
                results = cursor.fetchall()
                return results
            except Exception as e:
                raise Exception(f"Error getting saved sources: {str(e)}")
            finally:
                cursor.close()
    
    def get_source_by_id(self, source_id: int) -> Optional[Dict]:
        """Lấy source theo ID"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM saved_data_sources WHERE id = %s"
                cursor.execute(query, (source_id,))
                result = cursor.fetchone()
                return result
            except Exception as e:
                raise Exception(f"Error getting saved source: {str(e)}")
            finally:
                cursor.close()
    
    def update_source(self, source_id: int, name: str = None, file_path: str = None, sheets_url: str = None):
        """Cập nhật saved source"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                updates = []
                params = []
                
                if name:
                    updates.append("name = %s")
                    params.append(name)
                if file_path:
                    updates.append("file_path = %s")
                    params.append(file_path)
                if sheets_url:
                    updates.append("sheets_url = %s")
                    params.append(sheets_url)
                
                if not updates:
                    return
                
                updates.append("updated_at = NOW()")
                params.append(source_id)
                
                query = f"UPDATE saved_data_sources SET {', '.join(updates)} WHERE id = %s"
                cursor.execute(query, params)
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error updating saved source: {str(e)}")
            finally:
                cursor.close()
    
    def delete_source(self, source_id: int):
        """Xóa saved source"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "DELETE FROM saved_data_sources WHERE id = %s"
                cursor.execute(query, (source_id,))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error deleting saved source: {str(e)}")
            finally:
                cursor.close()
    
    def set_active_source(self, source_id: int):
        """Đặt source làm active (và deactivate các source khác)"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # Deactivate all
                cursor.execute("UPDATE saved_data_sources SET is_active = FALSE")
                # Activate the selected one
                cursor.execute("UPDATE saved_data_sources SET is_active = TRUE WHERE id = %s", (source_id,))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error setting active source: {str(e)}")
            finally:
                cursor.close()


class ConfigRepository:
    """Repository for config_items table"""
    
    def get_all_configs(self) -> List[Dict]:
        """Get all config items grouped by category"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM config_items ORDER BY category, config_key"
                cursor.execute(query)
                return cursor.fetchall()
            finally:
                cursor.close()
    
    def get_configs_by_category(self, category: str) -> List[Dict]:
        """Get config items for a specific category"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM config_items WHERE category = %s ORDER BY config_key"
                cursor.execute(query, (category,))
                return cursor.fetchall()
            finally:
                cursor.close()
    
    def create_config(self, category: str, config_key: str, config_value: str) -> int:
        """Create a new config item"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    INSERT INTO config_items (category, config_key, config_value)
                    VALUES (%s, %s, %s)
                """
                cursor.execute(query, (category, config_key, config_value))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error creating config: {str(e)}")
            finally:
                cursor.close()
    
    def update_config(self, config_id: int, config_key: str, config_value: str) -> bool:
        """Update an existing config item"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    UPDATE config_items 
                    SET config_key = %s, config_value = %s
                    WHERE id = %s
                """
                cursor.execute(query, (config_key, config_value, config_id))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error updating config: {str(e)}")
            finally:
                cursor.close()
    
    def delete_config(self, config_id: int) -> bool:
        """Delete a config item"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "DELETE FROM config_items WHERE id = %s"
                cursor.execute(query, (config_id,))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error deleting config: {str(e)}")
            finally:
                cursor.close()


class CompetitorListRepository:
    """Repository for competitor_list table (matching based on hotel_name + room details)"""
    
    def get_all_competitors(self, limit: int = 1000, offset: int = 0) -> List[Dict]:
        """Get all competitors with pagination"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT * FROM competitor_list 
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """
                cursor.execute(query, (limit, offset))
                return cursor.fetchall()
            finally:
                cursor.close()
    
    def find_competitor(self, hotel_name: str, room_type: str, num_people: int = None, 
                       bed_info: str = None, room_area: str = None) -> Optional[Dict]:
        """
        Find competitor by matching hotel_name + room details.
        hotel_name and room_type are REQUIRED.
        Other fields are optional - only match if they have values.
        """
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                # Build dynamic query based on available fields
                conditions = ["hotel_name = %s", "room_type = %s"]
                params = [hotel_name, room_type]
                
                # Add optional fields if they have values
                if num_people is not None:
                    conditions.append("num_people = %s")
                    params.append(num_people)
                
                if bed_info:
                    conditions.append("bed_info = %s")
                    params.append(bed_info)
                
                if room_area:
                    conditions.append("room_area = %s")
                    params.append(room_area)
                
                query = f"SELECT * FROM competitor_list WHERE {' AND '.join(conditions)} LIMIT 1"
                cursor.execute(query, tuple(params))
                return cursor.fetchone()
            finally:
                cursor.close()
    
    def get_competitor_by_id(self, competitor_id: int) -> Optional[Dict]:
        """Get competitor by ID"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = "SELECT * FROM competitor_list WHERE id = %s"
                cursor.execute(query, (competitor_id,))
                return cursor.fetchone()
            finally:
                cursor.close()
    
    def create_competitor(self, data: Dict) -> int:
        """Create a new competitor"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    INSERT INTO competitor_list (
                        hotel_name, hotel_link, room_type, num_people,
                        bed_info, room_area, room_choices, popular_facilities,
                        market, cluster, competitor_level, breakfast_included,
                        room_group, level
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                cursor.execute(query, (
                    data.get('hotel_name'),
                    data.get('hotel_link'),
                    data.get('room_type'),
                    data.get('num_people'),
                    data.get('bed_info'),
                    data.get('room_area'),
                    data.get('room_choices'),
                    data.get('popular_facilities'),
                    data.get('market'),
                    data.get('cluster'),
                    data.get('competitor_level'),
                    data.get('breakfast_included'),
                    data.get('room_group'),
                    data.get('level')
                ))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error creating competitor: {str(e)}")
            finally:
                cursor.close()
    
    def update_competitor(self, competitor_id: int, data: Dict) -> bool:
        """Update an existing competitor by ID"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    UPDATE competitor_list SET
                        hotel_name = %s, hotel_link = %s, room_type = %s, num_people = %s,
                        bed_info = %s, room_area = %s, room_choices = %s, popular_facilities = %s,
                        market = %s, cluster = %s, competitor_level = %s, breakfast_included = %s,
                        room_group = %s, level = %s
                    WHERE id = %s
                """
                cursor.execute(query, (
                    data.get('hotel_name'),
                    data.get('hotel_link'),
                    data.get('room_type'),
                    data.get('num_people'),
                    data.get('bed_info'),
                    data.get('room_area'),
                    data.get('room_choices'),
                    data.get('popular_facilities'),
                    data.get('market'),
                    data.get('cluster'),
                    data.get('competitor_level'),
                    data.get('breakfast_included'),
                    data.get('room_group'),
                    data.get('level'),
                    competitor_id
                ))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error updating competitor: {str(e)}")
            finally:
                cursor.close()
    
    def upsert_competitor(self, data: Dict) -> int:
        """
        Insert or update competitor based on hotel_name + room details matching.
        Matches on hotel_name + room_type (required) + optional fields.
        """
        existing = self.find_competitor(
            hotel_name=data.get('hotel_name'),
            room_type=data.get('room_type'),
            num_people=data.get('num_people'),
            bed_info=data.get('bed_info'),
            room_area=data.get('room_area')
        )
        if existing:
            self.update_competitor(existing['id'], data)
            return existing['id']
        else:
            return self.create_competitor(data)
    
    def delete_competitor(self, competitor_id: int) -> bool:
        """Delete a competitor by ID"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "DELETE FROM competitor_list WHERE id = %s"
                cursor.execute(query, (competitor_id,))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error deleting competitor: {str(e)}")
            finally:
                cursor.close()
    
    def get_total_count(self) -> int:
        """Get total number of competitors"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = "SELECT COUNT(*) FROM competitor_list"
                cursor.execute(query)
                result = cursor.fetchone()
                return result[0] if result else 0
            finally:
                cursor.close()


class TrackingRepository:
    """Repository for tracking functionality"""
    
    def __init__(self, db_conn_func):
        self.get_db = db_conn_func
    
    def get_price_crawl_histories(self) -> List[Dict]:
        """Lấy danh sách các phiên cào với type = 'price'"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT id, crawl_date, crawl_time, source, scrape_type
                    FROM crawl_history
                    WHERE scrape_type = 'price'
                    ORDER BY crawl_date DESC, crawl_time DESC
                """
                cursor.execute(query)
                return cursor.fetchall()
            finally:
                cursor.close()
    
    def get_tracking_data(
        self,
        history_id: int,
        year: int,
        month: int,
        market: str,
        cluster: str,
        breakfast: str,
        room_group: str,
        level: str
    ) -> List[Dict]:
        """Lấy dữ liệu tracking với filters"""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                query = """
                    SELECT 
                        cd.hotel_name,
                        cd.room_type,
                        cd.price_after_discount,
                        cd.check_in,
                        cd.check_out,
                        cl.market AS Market,
                        cl.cluster AS Cluster,
                        cl.breakfast_included AS 'Giá bao gồm bữa sáng',
                        cl.room_group AS 'Nhóm hạng phòng',
                        cl.level AS Level,
                        cl.competitor_level AS 'Level đối thủ'
                    FROM crawl_data cd
                    INNER JOIN competitor_list cl ON 
                        TRIM(cd.hotel_name) = TRIM(cl.hotel_name)
                        AND (cl.room_type IS NULL OR cl.room_type = '' OR TRIM(cd.room_type) = TRIM(cl.room_type))
                    WHERE cd.history_id = %s
                        AND cl.market = %s
                        AND cl.cluster = %s
                        AND cl.breakfast_included = %s
                        AND cl.room_group = %s
                        AND cl.level = %s
                    ORDER BY cd.hotel_name, cd.room_type
                """
                print(f"\n=== DEBUG Tracking Query ===")
                print(f"Parameters: history_id={history_id}, market={market}, cluster={cluster}")
                print(f"            breakfast={breakfast}, room_group={room_group}, level={level}")
                
                cursor.execute(query, (history_id, market, cluster, breakfast, room_group, level))
                results = cursor.fetchall()
                
                print(f"Query returned {len(results)} rows")
                if results:
                    print(f"Sample row: {results[0]}")
                    levels = [r.get('Level đối thủ') for r in results]
                    print(f"Competitor levels in results: {set(levels)}")
                
                return results
            finally:
                cursor.close()
    
    def generate_month_dates(self, year: int, month: int) -> List[Dict]:
        """Generate danh sách ngày trong tháng với thứ"""
        from datetime import date, timedelta
        import calendar
        
        # Get number of days in month
        _, num_days = calendar.monthrange(year, month)
        
        # Vietnamese day names
        day_names = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'CN']
        
        dates = []
        for day in range(1, num_days + 1):
            current_date = date(year, month, day)
            weekday = current_date.weekday()  # 0 = Monday, 6 = Sunday
            
            dates.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'day': day,
                'weekday': day_names[weekday],
                'display': f"{day_names[weekday]}<br/>({day:02d}/{month:02d}/{year})"
            })
        
        return dates

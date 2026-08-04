from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from typing import List
import asyncio
import json
from datetime import datetime
from app.schemas.scraper import ScrapeRequest, ScrapeProgress
from app.database.repositories import CrawlHistoryRepository, CrawlDataRepository
from app.services.booking_scraper import scrape_booking_data
from app.services.agoda_scraper import scrape_agoda_data
import re
import random # Add random for delay

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

def update_url_dates(url: str, checkin: str, checkout: str):
    checkin_str = checkin
    checkout_str = checkout
    
    url = re.sub(r'[&?]checkin=\d{4}-\d{2}-\d{2}', f'&checkin={checkin_str}', url)
    url = re.sub(r'[&?]checkout=\d{4}-\d{2}-\d{2}', f'&checkout={checkout_str}', url)
    
    if 'checkin=' not in url:
        separator = '&' if '?' in url else '?'
        url = f"{url}{separator}checkin={checkin_str}&checkout={checkout_str}"
    elif 'checkout=' not in url:
        url = f"{url}&checkout={checkout_str}"
    
    url = re.sub(r'&&+', '&', url)
    url = re.sub(r'\?&+', '?', url)
    
    return url

@router.websocket("/ws/scrape")
async def websocket_scrape_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            request_data = json.loads(data)
            
            links = request_data.get('links', [])
            date_ranges = request_data.get('date_ranges', [])
            source = request_data.get('source', 'booking')
            scrape_type = request_data.get('scrape_type', 'info')  # 'info' hoặc 'price'
            market = request_data.get('market', None)
            
            if not links or not date_ranges:
                await manager.send_personal_message({
                    'type': 'error',
                    'message': 'Invalid request data'
                }, websocket)
                continue
            
            valid_links = [link for link in links if link.get('is_valid', True)]
            total_links = len(valid_links) * len(date_ranges)
            processed_links = 0
            
            results = []
            errors = []
            
            history_repo = CrawlHistoryRepository()
            data_repo = CrawlDataRepository()
            
            history_id = history_repo.create_history(
                crawl_date=datetime.now().date(),
                source=source,
                scrape_type=scrape_type
            )
            
            await manager.send_personal_message({
                'type': 'started',
                'message': 'Scraping started',
                'history_id': history_id,
                'total': total_links
            }, websocket)
            
            # === DANH SÁCH CHỜ XỬ LÝ LẠI (FAILED RETRY QUEUE) ===
            failed_retry_queue = []

            for date_idx, date_range in enumerate(date_ranges):
                checkin_date = date_range['checkin']
                checkout_date = date_range['checkout']
                target_date_str = f"{checkin_date} - {checkout_date}"
                
                await manager.send_personal_message({
                    'type': 'date_range_start',
                    'message': f'Starting date range: {target_date_str}',
                    'date_range': date_range,
                    'date_index': date_idx + 1,
                    'total_dates': len(date_ranges)
                }, websocket)
                
                for idx, link_info in enumerate(valid_links):
                    original_url = link_info['link']
                    url = update_url_dates(original_url, checkin_date, checkout_date)
                    row_num = link_info.get('row', idx + 1)
                    
                    await manager.send_personal_message({
                        'type': 'progress',
                        'current': processed_links + 1,
                        'total': total_links,
                        'status': 'scraping',
                        'message': f'Scraping link {idx + 1}/{len(valid_links)} in date range {date_idx + 1}/{len(date_ranges)}',
                        'hotel_name': link_info.get('cell_value', 'Unknown'),
                        'row': row_num
                    }, websocket)

                    #Trigger debug mode nếu muốn tải html để debug luu vào folder debug_html
                    try:
                        if source == 'booking':
                            data, error_msg = scrape_booking_data(url, debug_mode=False, row_num=row_num)
                        else:
                            data, error_msg = scrape_agoda_data(url)
                        
                        # Nếu lỗi, thêm vào danh sách chờ xử lý lại
                        if error_msg:
                            print(f"[Main Loop] Failed: {url}. Error: {error_msg}. Adding to retry queue.")
                            failed_retry_queue.append({
                                'link_info': link_info,
                                'url': url,
                                'row_num': row_num,
                                'date_idx': date_idx,
                                'date_range': date_range,
                                'original_error': error_msg
                            })
                            # Send initial error notification but don't mark as final failure yet
                            await manager.send_personal_message({
                                'type': 'section_error', 
                                'message': f"Lỗi tạm thời: {error_msg}. Sẽ thử lại sau.",
                                'row': row_num
                            }, websocket)

                        elif data:
                            if data.get('rooms') and len(data['rooms']) > 0:
                                popular_fac_list = data.get('popular_facilities', [])
                                popular_fac_text = ', '.join(popular_fac_list) if popular_fac_list else ''
                                
                                for room in data['rooms']:
                                    price_clean = room.get('price', '')
                                    if price_clean:
                                        price_clean = re.sub(r'[^\d]', '', str(price_clean))
                                    
                                    price_orig_clean = room.get('price_original', '')
                                    if price_orig_clean:
                                        price_orig_clean = re.sub(r'[^\d]', '', str(price_orig_clean))
                                    
                                    bed_list = room.get('bed_options', [])
                                    bed_text = ' hoặc '.join(bed_list) if bed_list else ''
                                    facilities_list = room.get('facilities', [])
                                    facilities_text = '\n'.join(facilities_list) if facilities_list else ''
                                    discount_pct = room.get('discount_percent', '')
                                    
                                    # Common fields
                                    common_data = {
                                        'Hàng_gốc': row_num,
                                        'Mã số': link_info.get('code', ''),
                                        'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                        'Giờ cào': datetime.now().strftime('%H:%M:%S'),
                                        'Check in': checkin_date,
                                        'Check out': checkout_date,
                                        'Tên khách sạn': data.get('hotel_name', ''),
                                        'Tên hạng phòng': room.get('room_type', ''),
                                        'Số lượng người': room.get('num_guests', '')
                                    }
                                    
                                    if scrape_type == 'info':
                                        # Cào thông tin: bao gồm tất cả thông tin chi tiết
                                        results.append({
                                            **common_data,
                                            'Link khách sạn': url,
                                            'Số lượng review': data.get('review_count', ''),
                                            'Điểm review': data.get('rating', ''),
                                            'Các tiện nghi được ưa chuộng nhất': popular_fac_text,
                                            'Giường': bed_text,
                                            'Diện tích phòng': room.get('room_size', ''),
                                            'Các lựa chọn': facilities_text,
                                            'Ngày cần cào': target_date_str
                                        })
                                    else:
                                        # Cào giá: chỉ có thông tin về giá
                                        results.append({
                                            **common_data,
                                            'Giá sau giảm': price_clean,
                                            'Giá gốc': price_orig_clean,
                                            'Giảm giá': discount_pct,
                                            'Ngày cần cào': target_date_str,
                                            'Link khách sạn': url
                                        })
                                
                                await manager.send_personal_message({
                                    'type': 'success',
                                    'message': f'Successfully scraped {len(data["rooms"])} rooms',
                                    'row': row_num,
                                    'rooms_count': len(data['rooms'])
                                }, websocket)
                            else:
                                # No room data found - CHỈ thêm vào retry queue, KHÔNG thêm vào results
                                # Để tránh duplicate khi retry
                                print(f"[Main Loop] No Rooms Found: {url}. Adding to retry queue only.")
                                
                                # Thêm vào retry queue để thử lại
                                failed_retry_queue.append({
                                    'link_info': link_info,
                                    'url': url,
                                    'row_num': row_num,
                                    'date_idx': date_idx,
                                    'date_range': date_range,
                                    'original_error': "No room data found",
                                    'has_hotel_info': True,  # Đánh dấu có thông tin KS
                                    'hotel_data': data  # Lưu data để dùng sau
                                })
                                
                                await manager.send_personal_message({
                                    'type': 'warning',
                                    'message': f'Không tìm thấy phòng, sẽ thử lại sau',
                                    'row': row_num
                                }, websocket)
                                
                    except Exception as e:
                        # Thêm vào retry queue khi có Exception
                        error_msg = str(e)
                        print(f"[Main Loop] Exception: {url}. Error: {error_msg}. Adding to retry queue.")
                        failed_retry_queue.append({
                            'link_info': link_info,
                            'url': url,
                            'row_num': row_num,
                            'date_idx': date_idx,
                            'date_range': date_range,
                            'original_error': error_msg
                        })
                        await manager.send_personal_message({
                             'type': 'section_error',
                             'message': f"Lỗi ngoại lệ: {error_msg}. Sẽ thử lại sau.",
                             'row': row_num
                        }, websocket)

                    processed_links += 1
                    
                    # === RANDOM DELAY TO AVOID IP BAN ===
                    # Nghỉ ngẫu nhiên từ 3 đến 6 giây giữa các lần request
                    delay = random.uniform(3, 6)
                    await asyncio.sleep(delay)

            # === XỬ LÝ LẠI DANH SÁCH LỖI (RETRY PHASE - PHASE 2) ===
            if failed_retry_queue:
                await manager.send_personal_message({
                    'type': 'status_update', # Custom message types handled in FE? Or just general info
                    'message': f'Bắt đầu xử lý lại {len(failed_retry_queue)} link bị lỗi...',
                }, websocket)
                
                print(f"--- STARTING RETRY PHASE for {len(failed_retry_queue)} items ---")
                
                for idx, item in enumerate(failed_retry_queue):
                    url = item['url']
                    row_num = item['row_num']
                    checkin_date = item['date_range']['checkin']
                    checkout_date = item['date_range']['checkout']
                    target_date_str = f"{checkin_date} - {checkout_date}"
                    
                    await manager.send_personal_message({
                        'type': 'progress',
                        'current': processed_links, # Keep total progress bar full or static
                        'total': total_links,
                        'status': 'retrying',
                        'message': f'Thử lại ({idx+1}/{len(failed_retry_queue)}): {item["link_info"].get("cell_value", "Unknown")}',
                        'hotel_name': item["link_info"].get("cell_value", 'Unknown'),
                        'row': row_num
                    }, websocket)

                    try:
                        # Retry scraping
                        if source == 'booking':
                            data, error_msg = scrape_booking_data(url, debug_mode=False, row_num=row_num)
                        else:
                            data, error_msg = scrape_agoda_data(url)
                        
                        if error_msg:
                            # Final failure -> Add to error list and results
                            print(f"[Retry Phase] Final Failure: {url}. Error: {error_msg}")
                            errors.append({
                                'Tên': item['link_info'].get('cell_value', ''),
                                'Link': url,
                                'Lỗi': f"Lỗi sau khi thử lại: {error_msg}"
                            })
                            await manager.send_personal_message({
                                'type': 'error',
                                'message': f"Thất bại vĩnh viễn: {error_msg}",
                                'row': row_num
                            }, websocket)
                            
                            # Add row with hotel info if available, otherwise empty
                            saved_hotel_data = item.get('hotel_data')
                            if saved_hotel_data:
                                popular_fac_list = saved_hotel_data.get('popular_facilities', [])
                                popular_fac_text = ', '.join(popular_fac_list) if popular_fac_list else ''
                                
                                common_data = {
                                    'Hàng_gốc': row_num,
                                    'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                    'Giờ cào': datetime.now().strftime('%H:%M:%S'),
                                    'Check in': checkin_date,
                                    'Check out': checkout_date,
                                    'Tên khách sạn': saved_hotel_data.get('hotel_name', ''),
                                    'Tên hạng phòng': '',
                                    'Số lượng người': ''
                                }
                                
                                if scrape_type == 'info':
                                    results.append({
                                        **common_data,
                                        'Link khách sạn': url,
                                        'Số lượng review': saved_hotel_data.get('review_count', ''),
                                        'Điểm review': saved_hotel_data.get('rating', ''),
                                        'Các tiện nghi được ưa chuộng nhất': popular_fac_text,
                                        'Giường': '',
                                        'Diện tích phòng': '',
                                        'Các lựa chọn': '',
                                        'Ngày cần cào': target_date_str
                                    })
                                else:
                                    results.append({
                                        **common_data,
                                        'Giá sau giảm': '',
                                        'Giá gốc': '',
                                        'Giảm giá': '',
                                        'Ngày cần cào': target_date_str,
                                        'Link khách sạn': url
                                    })
                            else:
                                # No hotel data available - add completely empty row
                                results.append({
                                    'Hàng_gốc': row_num,
                                    'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                    'Ngày cần cào': target_date_str,
                                    'Tên khách sạn': '',
                                    'Link khách sạn': url,
                                    'Giá sau giảm': '',
                                    'Giá gốc': '',
                                    'Số lượng review': '',
                                    'Điểm review': '',
                                    'Tên hạng phòng': '',
                                    'Số lượng người': '',
                                    'Giường': '',
                                    'Diện tích phòng': '',
                                    'Các lựa chọn': ''
                                })
                        elif data and data.get('rooms') and len(data['rooms']) > 0:
                            print(f"[Retry Phase] Success: {url}")
                            # --- SUCCESS PROCESSING (Copied logic) ---
                            popular_fac_list = data.get('popular_facilities', [])
                            popular_fac_text = ', '.join(popular_fac_list) if popular_fac_list else ''
                            
                            for room in data['rooms']:
                                price_clean = room.get('price', '')
                                if price_clean:
                                    price_clean = re.sub(r'[^\d]', '', str(price_clean))
                                
                                price_orig_clean = room.get('price_original', '')
                                if price_orig_clean:
                                    price_orig_clean = re.sub(r'[^\d]', '', str(price_orig_clean))
                                
                                bed_list = room.get('bed_options', [])
                                bed_text = ' hoặc '.join(bed_list) if bed_list else ''
                                facilities_list = room.get('facilities', [])
                                facilities_text = '\n'.join(facilities_list) if facilities_list else ''
                                discount_pct = room.get('discount_percent', '')
                                
                                common_data = {
                                    'Hàng_gốc': row_num,
                                    'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                    'Giờ cào': datetime.now().strftime('%H:%M:%S'),
                                    'Check in': checkin_date,
                                    'Check out': checkout_date,
                                    'Tên khách sạn': data.get('hotel_name', ''),
                                    'Tên hạng phòng': room.get('room_type', ''),
                                    'Số lượng người': room.get('num_guests', '')
                                }
                                
                                if scrape_type == 'info':
                                    results.append({
                                        **common_data,
                                        'Link khách sạn': url,
                                        'Số lượng review': data.get('review_count', ''),
                                        'Điểm review': data.get('rating', ''),
                                        'Các tiện nghi được ưa chuộng nhất': popular_fac_text,
                                        'Giường': bed_text,
                                        'Diện tích phòng': room.get('room_size', ''),
                                        'Các lựa chọn': facilities_text,
                                        'Ngày cần cào': target_date_str
                                    })
                                else:
                                    results.append({
                                        **common_data,
                                        'Giá sau giảm': price_clean,
                                        'Giá gốc': price_orig_clean,
                                        'Giảm giá': discount_pct,
                                        'Ngày cần cào': target_date_str,
                                        'Link khách sạn': url
                                    })
                            
                            await manager.send_personal_message({
                                'type': 'success',
                                'message': f'Successfully extracted data on retry for row {row_num}',
                                'row': row_num,
                                'rooms_count': len(data['rooms'])
                            }, websocket)
                        elif data or item.get('hotel_data'):
                             # No rooms but has hotel info (from retry or saved from first attempt)
                             # Use retry data if available, otherwise use saved data
                             hotel_info = data if data else item.get('hotel_data')
                             print(f"[Retry Phase] No rooms but has hotel info: {url}")
                             
                             popular_fac_list = hotel_info.get('popular_facilities', [])
                             popular_fac_text = ', '.join(popular_fac_list) if popular_fac_list else ''
                             
                             common_data = {
                                 'Hàng_gốc': row_num,
                                 'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                 'Giờ cào': datetime.now().strftime('%H:%M:%S'),
                                 'Check in': checkin_date,
                                 'Check out': checkout_date,
                                 'Tên khách sạn': hotel_info.get('hotel_name', ''),
                                 'Tên hạng phòng': '',
                                 'Số lượng người': ''
                             }
                             
                             if scrape_type == 'info':
                                 results.append({
                                     **common_data,
                                     'Link khách sạn': url,
                                     'Số lượng review': hotel_info.get('review_count', ''),
                                     'Điểm review': hotel_info.get('rating', ''),
                                     'Các tiện nghi được ưa chuộng nhất': popular_fac_text,
                                     'Giường': '',
                                     'Diện tích phòng': '',
                                     'Các lựa chọn': '',
                                     'Ngày cần cào': target_date_str
                                 })
                             else:
                                 results.append({
                                     **common_data,
                                     'Giá sau giảm': '',
                                     'Giá gốc': '',
                                     'Giảm giá': '',
                                     'Ngày cần cào': target_date_str,
                                     'Link khách sạn': url
                                 })
                             
                             # Add to errors list for tracking
                             errors.append({
                                'Tên': item['link_info'].get('cell_value', ''),
                                'Link': url,
                                'Lỗi': "No room data found after retry (hotel info saved)"
                             })
                        else:
                             # Empty data on retry
                             print(f"[Retry Phase] No data on retry: {url}")
                             errors.append({
                                'Tên': item['link_info'].get('cell_value', ''),
                                'Link': url,
                                'Lỗi': "No room data found (Retry failed)"
                             })
                             await manager.send_personal_message({
                                'type': 'error',
                                'message': "No data found after retry",
                                'row': row_num
                             }, websocket)
                             results.append({
                                    'Hàng_gốc': row_num,
                                    'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                                    'Ngày cần cào': target_date_str,
                                    'Tên khách sạn': '',
                                    'Link khách sạn': url,
                                    'Giá sau giảm': '',
                                    'Giá gốc': '',
                                    'Số lượng review': '',
                                    'Điểm review': '',
                                    'Tên hạng phòng': '',
                                    'Số lượng người': '',
                                    'Giường': '',
                                    'Diện tích phòng': '',
                                    'Các lựa chọn': ''
                                })

                    except Exception as e:
                        # Ensure final failure is recorded
                        print(f"[Retry Phase] Final Exception: {url}. Error: {str(e)}")
                        errors.append({
                            'Tên': item['link_info'].get('cell_value', ''),
                            'Link': url,
                            'Lỗi': f"Lỗi ngoại lệ khi thử lại: {str(e)}"
                        })
                        await manager.send_personal_message({
                            'type': 'error',
                            'message': f"Final exception: {str(e)}",
                            'row': row_num
                        }, websocket)
                        results.append({
                            'Hàng_gốc': row_num,
                            'Ngày cào': datetime.now().strftime('%Y-%m-%d'),
                            'Ngày cần cào': target_date_str,
                            'Tên khách sạn': '',
                            'Link khách sạn': url,
                            'Giá sau giảm': '',
                            'Giá gốc': '',
                            'Số lượng review': '',
                            'Điểm review': '',
                            'Tên hạng phòng': '',
                            'Số lượng người': '',
                            'Giường': '',
                            'Diện tích phòng': '',
                            'Các lựa chọn': ''
                        })
                    await asyncio.sleep(0.1)
            
            if results:
                rows_inserted = data_repo.save_batch_data(history_id, results)
                history_repo.update_total_records(history_id, rows_inserted)
            
            await manager.send_personal_message({
                'type': 'completed',
                'message': 'Scraping completed',
                'history_id': history_id,
                'total_success': len(results),
                'total_errors': len(errors),
                'results': results,
                'errors': errors
            }, websocket)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        try:
            await manager.send_personal_message({
                'type': 'error',
                'message': f'Server error: {str(e)}'
            }, websocket)
        except:
            pass
        manager.disconnect(websocket)

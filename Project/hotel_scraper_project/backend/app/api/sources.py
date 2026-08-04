from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from typing import List, Optional
from pydantic import BaseModel
from app.database.repositories import SavedDataSourceRepository
from app.services.booking_scraper import extract_hyperlinks_from_excel, get_markets_from_excel
import os
from datetime import datetime

router = APIRouter()

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class GoogleSheetImportRequest(BaseModel):
    url: str
    save_for_reuse: bool = False
    name: Optional[str] = None
    market: Optional[str] = "Default"

@router.post("/sources/upload")
async def upload_source(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    save_for_reuse: bool = Form(False)
):
    """Upload Excel file và extract markets"""
    try:
        # Đọc file content
        file_bytes = await file.read()
        
        # Lấy danh sách markets (sheet names)
        markets = get_markets_from_excel(file_bytes)
        
        if not markets:
            raise HTTPException(status_code=400, detail="Không tìm thấy sheet nào trong file Excel")
        
        # Extract links từ tất cả sheets
        links = extract_hyperlinks_from_excel(file_bytes, market=None)
        
        source_id = None
        file_path = None
        
        # Nếu user muốn lưu lại để dùng sau
        if save_for_reuse:
            # Lưu file vào disk
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{file.filename}"
            file_path = os.path.join(UPLOAD_DIR, filename)
            
            with open(file_path, "wb") as f:
                f.write(file_bytes)
            
            # Lưu thông tin vào database
            repo = SavedDataSourceRepository()
            source_name = name or file.filename
            source_id = repo.create_source(
                name=source_name,
                source_type='file',
                file_path=file_path
            )
        
        return {
            "success": True,
            "markets": markets,
            "links": links,
            "total_links": len(links),
            "source_id": source_id,
            "file_path": file_path
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.get("/sources")
async def get_sources():
    """Lấy danh sách saved sources"""
    try:
        repo = SavedDataSourceRepository()
        sources = repo.get_all_sources(active_only=False)
        return {"sources": sources}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.get("/sources/{source_id}")
async def get_source(source_id: int):
    """Lấy thông tin và links từ saved source"""
    try:
        repo = SavedDataSourceRepository()
        source = repo.get_source_by_id(source_id)
        
        if not source:
            raise HTTPException(status_code=404, detail="Không tìm thấy source")
        
        # Đọc file và extract links
        if source['source_type'] == 'file' and source['file_path'] and os.path.exists(source['file_path']):
            with open(source['file_path'], 'rb') as f:
                file_bytes = f.read()
            
            markets = get_markets_from_excel(file_bytes)
            links = extract_hyperlinks_from_excel(file_bytes, market=None)
            
            return {
                "source": source,
                "markets": markets,
                "links": links,
                "total_links": len(links)
            }
        elif source['source_type'] == 'google_sheets' and source['sheets_url']:
            # Load TẤT CẢ sheets từ Google Sheets
            from app.services.booking_scraper import load_google_sheet_all_sheets
            
            all_sheets_data, error = load_google_sheet_all_sheets(source['sheets_url'])
            
            if error:
                raise HTTPException(status_code=400, detail=error)
            
            if not all_sheets_data:
                raise HTTPException(status_code=400, detail="Không tìm thấy link nào trong Google Sheet")
            
            # Format links theo cấu trúc mong muốn (giống Excel)
            formatted_links = []
            markets = list(all_sheets_data.keys())
            
            for market, links_info in all_sheets_data.items():
                for link_data in links_info:
                    formatted_links.append({
                        "market": market,
                        "hotel_name": link_data.get("hotel_name", ""),
                        "cell_value": link_data.get("cell_value", ""),
                        "link": link_data["link"],
                        "is_valid": link_data["is_valid"]
                    })
            
            return {
                "source": source,
                "markets": markets,
                "links": formatted_links,
                "total_links": len(formatted_links)
            }
        else:
            raise HTTPException(status_code=400, detail="File không tồn tại hoặc source type không được hỗ trợ")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.post("/sources/{source_id}/activate")
async def activate_source(source_id: int):
    """Đặt source làm active"""
    try:
        repo = SavedDataSourceRepository()
        repo.set_active_source(source_id)
        return {"success": True, "message": "Source đã được kích hoạt"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int):
    """Xóa saved source"""
    try:
        repo = SavedDataSourceRepository()
        source = repo.get_source_by_id(source_id)
        
        if source and source['file_path'] and os.path.exists(source['file_path']):
            # Xóa file
            os.remove(source['file_path'])
        
        repo.delete_source(source_id)
        return {"success": True, "message": "Source đã được xóa"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.post("/sources/{source_id}/markets/{market}/links")
async def get_links_by_market(source_id: int, market: str):
    """Lấy links của một market cụ thể từ saved source"""
    try:
        repo = SavedDataSourceRepository()
        source = repo.get_source_by_id(source_id)
        
        if not source:
            raise HTTPException(status_code=404, detail="Không tìm thấy source")
        
        if source['source_type'] == 'file' and source['file_path'] and os.path.exists(source['file_path']):
            with open(source['file_path'], 'rb') as f:
                file_bytes = f.read()
            
            # Extract links chỉ từ market được chỉ định
            links = extract_hyperlinks_from_excel(file_bytes, market=market)
            
            return {
                "market": market,
                "links": links,
                "total_links": len(links)
            }
        else:
            raise HTTPException(status_code=400, detail="File không tồn tại")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")


@router.post("/sources/import-google-sheet")
async def import_google_sheet(request: GoogleSheetImportRequest):
    """Import links từ TẤT CẢ sheets trong Google Spreadsheet"""
    try:
        # Load TẤT CẢ sheets từ Google Sheets
        from app.services.booking_scraper import load_google_sheet_all_sheets
        
        all_sheets_data, error = load_google_sheet_all_sheets(request.url)
        
        if error:
            # Enhance error message if it's a permission/access issue
            if "400" in error or "Bad Request" in error or "access" in error.lower():
                detailed_error = (
                    f"{error}\n\n"
                    "💡 Cách khắc phục:\n"
                    "1. Mở Google Sheet và click nút 'Share' (góc trên phải)\n"
                    "2. Chọn 'Anyone with the link' có quyền 'Viewer'\n"
                    "3. Copy link và thử lại\n\n"
                    "Hoặc tải xuống Excel (.xlsx) và upload file thay vì dùng link."
                )
                raise HTTPException(status_code=400, detail=detailed_error)
            raise HTTPException(status_code=400, detail=error)
        
        if not all_sheets_data:
            raise HTTPException(status_code=400, detail="Không tìm thấy link nào trong Google Sheet")
        
        # Format links theo cấu trúc mong muốn (giống Excel với nhiều markets)
        formatted_links = []
        markets = list(all_sheets_data.keys())
        
        for market, links_info in all_sheets_data.items():
            for link_data in links_info:
                formatted_links.append({
                    "market": market,
                    "hotel_name": link_data.get("hotel_name", ""),
                    "cell_value": link_data.get("cell_value", ""),
                    "link": link_data["link"],
                    "is_valid": link_data["is_valid"]
                })
        
        source_id = None
        
        # Nếu user muốn lưu lại để dùng sau
        if request.save_for_reuse:
            repo = SavedDataSourceRepository()
            source_name = request.name or f"Google Sheet - {', '.join(markets[:3])}"
            source_id = repo.create_source(
                name=source_name,
                source_type='google_sheets',
                sheets_url=request.url
            )
        
        return {
            "success": True,
            "markets": markets,
            "links": formatted_links,
            "total_links": len(formatted_links),
            "source_id": source_id
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi: {str(e)}")

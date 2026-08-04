# ✅ Checklist - Chạy Project từ Source Code

Sử dụng checklist này để đảm bảo mọi thứ đã được setup đúng.

## 📋 Trước khi bắt đầu

- [ ] Python 3.8+ đã cài đặt (`python --version`)
- [ ] Node.js 20+ đã cài đặt (`node --version`)
- [ ] MySQL 8.0+ đã cài đặt và đang chạy
- [ ] Microsoft Edge đã cài (cho Selenium WebDriver)
- [ ] PowerShell có quyền chạy script

## 🗄️ Database Setup

- [ ] MySQL service đang chạy
- [ ] Database `hotel_scraper` đã được tạo
  ```sql
  CREATE DATABASE hotel_scraper CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  ```
- [ ] Schema đã được import
  ```powershell
  mysql -u root -p hotel_scraper < backend\app\database\setup.sql
  ```
- [ ] Có thể kết nối được với MySQL
  ```powershell
  mysql -u root -p -e "USE hotel_scraper; SHOW TABLES;"
  ```

## 🔧 Backend Setup

- [ ] Đã chạy `backend\setup.ps1`
- [ ] Thư mục `backend\venv` đã được tạo
- [ ] File `backend\.env` đã được tạo
- [ ] File `.env` đã được cập nhật với MySQL password
- [ ] Test chạy backend:
  ```powershell
  cd backend
  .\venv\Scripts\Activate.ps1
  python main.py
  ```
- [ ] Backend chạy thành công tại http://localhost:8000
- [ ] API Docs hiển thị tại http://localhost:8000/docs

## 💻 Frontend Setup

- [ ] Đã chạy `frontend\setup.ps1`
- [ ] Thư mục `frontend\node_modules` đã được tạo
- [ ] File `frontend\.env` đã được tạo
- [ ] Các dependencies quan trọng đã cài:
  - [ ] vue, vue-router, pinia
  - [ ] primevue, primeicons, @primevue/themes
  - [ ] axios, xlsx
- [ ] Test chạy frontend:
  ```powershell
  cd frontend
  npm run dev
  ```
- [ ] Frontend chạy thành công tại http://localhost:5173

## 🚀 Chạy Application

- [ ] Chạy `start.ps1` từ thư mục gốc
- [ ] 2 cửa sổ PowerShell mới được mở (Backend + Frontend)
- [ ] Backend terminal hiển thị "Uvicorn running on..."
- [ ] Frontend terminal hiển thị "Local: http://localhost:5173"
- [ ] Truy cập http://localhost:5173 thành công
- [ ] Giao diện hiển thị đúng với 2 tabs

## ✨ Test Chức năng

- [ ] Tab "Booking.com" hiển thị đầy đủ
- [ ] Tab "Agoda.com" bị disabled (đúng)
- [ ] Upload file Excel hoạt động
- [ ] Thêm cặp ngày hoạt động
- [ ] Button "Bắt đầu cào" xuất hiện sau khi upload + chọn ngày
- [ ] Click vào tab "Lịch sử" hoạt động

## 🔍 Kiểm tra kết nối

### Backend → MySQL
```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -c "from app.core.database import MySQLConnectionPool; pool = MySQLConnectionPool(); print('✓ MySQL connected!')"
```

### Frontend → Backend
- [ ] Mở http://localhost:5173
- [ ] F12 → Console → Không có lỗi CORS
- [ ] F12 → Network → Các API call thành công

## 📁 Cấu trúc Files

### Root level
- [ ] `README.md` - Hướng dẫn chi tiết
- [ ] `QUICK_START.md` - Hướng dẫn nhanh
- [ ] `start.ps1` - Script chạy cả 2 servers
- [ ] `.gitignore` - Git ignore file

### Backend
- [ ] `backend/setup.ps1` - Script setup backend
- [ ] `backend/.env` - Environment variables (với password đúng)
- [ ] `backend/venv/` - Virtual environment
- [ ] `backend/main.py` - Entry point
- [ ] `backend/requirements.txt` - Dependencies
- [ ] `backend/app/database/setup.sql` - Database schema

### Frontend
- [ ] `frontend/setup.ps1` - Script setup frontend
- [ ] `frontend/.env` - Environment variables
- [ ] `frontend/node_modules/` - Dependencies
- [ ] `frontend/package.json` - Package config
- [ ] `frontend/vite.config.js` - Vite config với proxy
- [ ] `frontend/src/main.js` - Entry point với PrimeVue 4.x

## 🎯 Dependencies Versions

### Backend (requirements.txt)
- [ ] fastapi==0.109.0
- [ ] uvicorn[standard]==0.27.0
- [ ] mysql-connector-python==8.3.0
- [ ] pydantic==2.5.0
- [ ] selenium==4.15.0
- [ ] pandas==1.5.0

### Frontend (package.json)
- [ ] vue: ^3.5.27
- [ ] vue-router: ^5.0.2
- [ ] pinia: ^3.0.4
- [ ] primevue: ^4.5.4
- [ ] @primevue/themes: ^4.5.4
- [ ] axios: ^1.13.5
- [ ] xlsx: ^0.18.5

## ❗ Common Issues

### ExecutionPolicy Error
```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Port Already in Use
- Backend port 8000: Tìm và tắt process đang dùng port
- Frontend port 5173: Tìm và tắt process đang dùng port

### MySQL Connection Error
- Check MySQL service đang chạy
- Check username/password trong `.env`
- Check database tồn tại

### PrimeVue Theme Error
- Đảm bảo `@primevue/themes` đã được cài
- Check `main.js` import đúng theme Aura
- Restart dev server sau khi sửa

## ✅ Hoàn thành!

Nếu tất cả items trên đều checked ✅, project sẵn sàng để sử dụng!

**Chạy lại mọi lúc:**
```powershell
.\start.ps1
```

**Dừng server:**
- Đóng 2 cửa sổ PowerShell
- Hoặc Ctrl+C trong mỗi terminal

# 🚀 Hướng Dẫn Deploy - Bắt Đầu Từ Đây

Chọn môi trường deploy của bạn:

---

## 🖥️ Option 1: Local Development (Windows)

**Mục đích:** Chạy ứng dụng trên máy local để develop/test

### Bước 1: Cài đặt
```powershell
# Cài các tools cần thiết
- Python 3.8+
- Node.js 20+
- MySQL 8.0+
```

### Bước 2: Tạo Database
```powershell
# Mở MySQL
mysql -u root -p

# Trong MySQL:
CREATE DATABASE hotel_scraper CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
exit;

# Import schema
cd d:\Github\booking\hotel_scraper_project
mysql -u root -p hotel_scraper < backend/app/database/setup.sql
```

### Bước 3: Config Backend
```powershell
cd backend

# 1. Copy .env
cp .env.example .env

# 2. Sửa file .env
notepad .env
# Thay đổi:
# DB_PASSWORD=your_mysql_password_here
```

### Bước 4: Config Frontend
```powershell
cd ../frontend

# 1. Copy .env (optional - đã có default)
cp .env.example .env

# 2. Kiểm tra file .env
notepad .env
# Phải có:
# VITE_API_URL=http://localhost:8000
# VITE_WS_URL=ws://localhost:8000
```

### Bước 5: Chạy Backend
```powershell
cd ../backend
.\setup-backend.ps1  # Tự động tạo venv và install packages

# Activate venv
.\venv\Scripts\Activate.ps1

# Run server
python main.py
# Server chạy tại: http://localhost:8000
```

### Bước 6: Chạy Frontend
```powershell
# Mở terminal mới
cd frontend
.\setup-frontend.ps1  # Tự động npm install

# Run dev server
npm run dev
# Frontend chạy tại: http://localhost:5173
```

### ✅ Kiểm tra
- 🌐 Ứng dụng: http://localhost:5173
- 📚 API Docs: http://localhost:8000/docs

---

## 🐳 Option 2: Production on VPS (Docker)

**Mục đích:** Deploy lên VPS với Docker Compose

### Bước 1: Chuẩn bị VPS
```bash
# SSH vào VPS
ssh root@your-vps-ip

# Cài Docker
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install docker-compose-plugin git -y

# Verify
docker --version
docker compose version
```

### Bước 2: Clone Code
```bash
cd /var/www
git clone https://github.com/Andnm/hotel_scraper_project.git
cd hotel_scraper_project
git checkout refactor-code  # Hoặc branch bạn muốn deploy
```

### Bước 3: Config Root .env
```bash
# 1. Copy file
cp .env.example .env

# 2. Edit
nano .env

# 3. Thay đổi passwords (quan trọng!)
DB_ROOT_PASSWORD=YourStrongRootPassword123!
DB_NAME=hotel_scraper
DB_USER=hotel_user
DB_PASSWORD=YourStrongPassword456!
APP_ENV=production

# Lưu: Ctrl+O, Enter, Ctrl+X
```

### Bước 4: Config Backend .env.production
```bash
cd backend

# File đã có sẵn, kiểm tra:
cat .env.production

# Nếu cần sửa, copy và edit:
# cp .env.production .env
# nano .env

cd ..
```

### Bước 5: Config Frontend .env **[QUAN TRỌNG!]**
```bash
cd frontend

# 1. Copy file .env
cp .env.example .env

# 2. Edit file
nano .env
```

**Sửa thành (dùng HTTP - không cần SSL):**
```env
VITE_API_URL=http://projecthub.io.vn/api
VITE_WS_URL=ws://projecthub.io.vn/ws
```

```bash
# Lưu: Ctrl+O, Enter, Ctrl+X
cd ..
```

⚠️ **Quan trọng:** Deploy lần đầu dùng HTTP. Sau khi có SSL sẽ chuyển sang HTTPS (xem thêm bước 8).

### Bước 6: Sửa Nginx config (dùng HTTP only)
```bash
# Vì chưa có SSL, dùng config HTTP only
cd frontend
cp nginx-http.conf nginx.conf
cd ..
```

Hoặc nếu muốn sửa manual:
```bash
nano frontend/nginx.conf
# Comment dòng redirect (dòng 10):
# return 301 https://$host$request_uri;  
# Comment toàn bộ server block SSL
```

### Bước 7: Deploy Auto
```bash
# Từ thư mục gốc hotel_scraper_project

# Option A: Script tự động (khuyên dùng)
chmod +x quick-deploy.sh
./quick-deploy.sh

# Script sẽ tự động:
# - Build images
# - Start containers
# - Init database từ setup.sql
# - Verify tables
# - Show logs
```

**Hoặc Manual:**
```bash
# Option B: Manual deploy
docker compose build
docker compose up -d

# Kiểm tra
docker compose ps
docker compose logs -f
```

### Bước 8: Verify
```bash
# 1. Check containers
docker compose ps
# Phải có 3 containers: backend, frontend, db

# 2. Check database
docker compose exec backend cat /var/log/init-db.log
# Phải thấy: "Database initialization completed successfully"

# 3. Check tables
docker compose exec db mysql -u hotel_user -p hotel_scraper -e "SHOW TABLES;"
# Nhập password khi được hỏi
```

### ✅ Truy cập (HTTP - chưa có SSL)
- 🌐 Frontend: http://116.118.9.65 hoặc http://projecthub.io.vn
- 📚 API Docs: http://116.118.9.65:8000/docs

---

### Bước 9: Setup SSL (Optional - sau khi deploy xong)

**Nếu muốn HTTPS, làm tiếp:**

```bash
# 1. Restore nginx.conf về version có SSL
git checkout frontend/nginx.conf

# 2. Chạy script tạo SSL certificate
chmod +x init-letsencrypt.sh
./init-letsencrypt.sh

# 3. Sửa frontend .env sang HTTPS
cd frontend
nano .env
# Đổi:
# VITE_API_URL=https://projecthub.io.vn/api
# VITE_WS_URL=wss://projecthub.io.vn/ws

# 4. Rebuild frontend với HTTPS
cd ..
docker compose down
docker compose build frontend --no-cache
docker compose up -d

# 5. Truy cập với HTTPS
# https://projecthub.io.vn
```

---

## 📋 Quick Command Reference

### Local Development
```powershell
# Run tất cả (từ root)
.\start.ps1

# Hoặc manual:
# Terminal 1: cd backend; .\venv\Scripts\Activate.ps1; python main.py
# Terminal 2: cd frontend; npm run dev
```

### Production Management
```bash
# Start
docker compose up -d

# Stop
docker compose down

# Restart
docker compose restart

# Logs
docker compose logs -f

# Rebuild
docker compose build --no-cache
docker compose up -d

# Database backup
docker compose exec db mysqldump -u hotel_user -p hotel_scraper > backup.sql
```

---

## 🆘 Need Help?

Gặp lỗi? Xem:
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Giải quyết lỗi thường gặp
- [ENV_CONFIGURATION.md](ENV_CONFIGURATION.md) - Chi tiết về .env files
- [DATABASE.md](DATABASE.md) - Database documentation
- [DEPLOY_VPS.md](DEPLOY_VPS.md) - Chi tiết deploy VPS

---

**✨ Pro Tip:** Luôn test trên local trước khi deploy production!

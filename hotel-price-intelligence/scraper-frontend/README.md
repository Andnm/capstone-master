# Scraper Frontend

Giao diện nội bộ để upload danh sách khách sạn Booking.com, kiểm tra trước cấu trúc file, chọn ngày
check-in, theo dõi job và xuất toàn bộ dữ liệu đã cào ra Excel.

## Chạy local

Backend cần chạy tại `http://127.0.0.1:8000`:

```powershell
cd ..\backend
.\venv\Scripts\python.exe main.py
```

Sau đó chạy frontend:

```powershell
npm install
npm run dev
```

Mở `http://127.0.0.1:3000`. File `.env.local` mặc định dùng
`NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` để tránh lỗi phân giải `localhost` sang IPv6 trên Windows.

## File đầu vào và ngữ cảnh cào

- Chỉ nhận `.xlsx`, tối đa 10 MB.
- Mỗi sheet: cột A là tên gợi ý, cột B là URL Booking.com.
- Preflight hiển thị link hợp lệ, link lỗi/trùng và sheet ngoài 5 thành phố trong scope.
- Mọi URL được chuẩn hoá về 2 người lớn, 0 trẻ em, 1 phòng, 1 đêm, VND và tiếng Việt.

Trang chi tiết job hiển thị số option crawler đọc/DB lưu, trạng thái `partial` nếu hai số lệch,
URL cào thực tế và chi tiết thuế/phí. Nút **Xuất Excel** chỉ xuất dữ liệu đã lưu của đúng run đó.

## Kiểm tra trước khi bàn giao

```powershell
npm run lint
npm run build
```

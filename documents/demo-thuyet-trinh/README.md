# Bộ tài liệu demo và thuyết trình

Bản chuẩn bị ngày 10/10/2026 cho **30 phút thuyết trình**, không tính hỏi đáp.
Đây là báo cáo tiến độ với demo dashboard mock, chưa phải báo cáo kết quả model cuối cùng.

## File dùng khi trình bày

- `trinh-chieu/Bai-thuyet-trinh-30-phut-v3.pptx`: 18 slide chính và 3 slide phụ lục, có lời thoại trong Speaker Notes.
- `trinh-chieu/Bai-thuyet-trinh-30-phut-v3.pdf`: bản xem và trình chiếu dự phòng, giữ ảnh render từng slide.
- `Kich-ban-demo-v2.pdf`: checklist chuẩn bị, thao tác, lời thoại demo, kết quả cần thấy và xử lý sự cố.
- `Kich-ban-va-loi-thoai.md`: bản chữ chỉnh sửa được, gồm lời thoại từng slide và kịch bản demo.
- `assets/`: ảnh dashboard làm phương án dự phòng. Ảnh thể hiện mock, không phải dữ liệu thật.
- `_build/`: nguồn tạo tài liệu và bằng chứng kiểm tra, không phải file để trình chiếu.

## Phân bổ 30 phút

| Phần | Slide | Thời gian |
| --- | --- | --- |
| Bài toán, dữ liệu, phương pháp và giới hạn | 1–12 | 20 phút 30 giây |
| Demo Hotelier, Consumer enriched và cold-start | 13–15 | 6 phút |
| Tiến độ, nghiệm thu và kết luận | 16–18 | 3 phút 30 giây |
| Hỏi đáp | 19–21 | Ngoài 30 phút |

Lời thoại là khung để tập nói kết hợp giải thích slide, thao tác và khoảng dừng cho người xem.
Không đọc nguyên văn nhanh rồi chờ cho đủ thời gian. Tập một lần với đồng hồ trước buổi trình bày.

## Chạy demo

```powershell
cd D:\MSE\CAPSTONE\hotel-price-intelligence\dashboard-frontend
npm run dev
```

Mở `http://localhost:3100/hotelier` và `http://localhost:3100/consumer`.
Không cần backend, MySQL, `.env` hay crawl. Không bật crawler hoặc training để phục vụ demo.

Ngày giả lập của ứng dụng là **18/11/2026**. Check-in mặc định là **28/11/2026**.
Đây không phải ngày hiện tại của máy.

## Những điểm phải nói rõ

- Toàn bộ khách sạn, giá, tình trạng phòng và dự báo trong demo là hư cấu.
- Confidence tier không phải xác suất và chưa có hiệu chuẩn của model thật.
- Mục tiêu Accuracy@20% ≥ 80% giữ nguyên. Slide không tuyên bố lần chạy mới đã đạt mục tiêu.
- UI minh họa đủ bốn horizon. Việc model thật hỗ trợ h7/h14 chưa được xác nhận.
- h14 tính từ 18/11 rơi sau check-in 28/11. Thẻ mock không chứng minh horizon này hợp lệ cho serving thật.
- Reference proposed/approved và số snapshot trong demo không phải kết quả duyệt chuỗi thật.
- Prototype dùng stable tại đúng ±2%, nhưng quy tắc gợi ý dùng ≥ +2% / ≤ −2%. Cần thống nhất đặc tả khi tích hợp thật.
- Tình trạng run model phải kiểm lại trước buổi trình bày. Không tự điền accuracy, MAE, CI hoặc lift chưa có bằng chứng.

## Nguồn và giả định

Nguồn: các `sections/*.tex` hiện hành trong `../proposal`, dashboard mock và hồ sơ review 121/122.
Không dùng thư mục proposal `detailed` để lấy phạm vi hiện tại. Các chỉ tiêu 355 cơ sở / 5 thành phố
và bốn horizon là **phạm vi đề xuất**, không phải thành tích dữ liệu đã đủ.

Tên tiếng Anh trong `proposal.tex` và `proposal/README.md` đang khác nhau. Slide dùng tên mô tả
tiếng Việt, chưa thay tên nộp chính thức. Tên học viên và mã học viên lấy từ proposal.tex.
Giả định hình thức: trình bày tiếng Việt, 16:9, Arial, báo cáo tiến độ và demo cho hội đồng,
30 phút không tính hỏi đáp. Chưa sửa nội dung proposal hoặc chạy lại LaTeX.

`proposal` đã chuyển từ `D:\MSE\CAPSTONE\proposal` sang `D:\MSE\CAPSTONE\documents\proposal`.
35 file có SHA-256 trước/sau giống nhau. Các đường dẫn LaTeX tương đối giữ nguyên cấu trúc.
Các log build cũ có thể còn ghi đường dẫn cũ. Chạy build từ vị trí mới khi cần.

Không tự commit hoặc push tài liệu trong yêu cầu này.

## Kiểm tra bản bàn giao

- Tổng thời gian slide 1–18 là 30 phút. Slide 19–21 ngoài thời gian chính.
- Đã kiểm đủ 21 Speaker Notes, hai bảng PowerPoint chỉnh sửa được và ba ảnh demo.
- Đã render và xem từng slide, toàn bộ 21 trang PDF slide và 6 trang PDF kịch bản.
- Kiểm cấu trúc PPTX, hình học, font và import lại đều qua. Chưa mở thử trong PowerPoint native.
- PDF slide dùng ảnh render để giữ nguyên bố cục. Chỉnh nội dung bằng PPTX, không dùng PDF làm nguồn chỉnh sửa.
- Không xuất DOCX vì runtime chưa có bộ render Word đi kèm để kiểm từng trang. Markdown là bản lời thoại chỉnh sửa được.
- `_build/revisions` giữ các bản nháp trước khi chỉnh khung ảnh và nhịp thời gian. Chỉ dùng file liệt kê ở đầu README để trình bày.

Khi cập nhật kết quả thật, sửa nội dung và tạo tên phiên bản mới, không ghi đè bản đã chốt.
Tập trước với đồng hồ là bước cuối cần người thuyết trình thực hiện.

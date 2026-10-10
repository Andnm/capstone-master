# Kịch bản demo và lời thoại 30 phút

Bản chuẩn bị 10/10/2026. Dashboard mock, chưa phải kết quả model cuối. Hỏi đáp ngoài 30 phút.

## Nhịp trình bày 30 phút

Kịch bản diễn tập | Đặng Nguyễn Minh An | 10/10/2026

18 slide chính, 3 slide phụ lục hỏi đáp. Phần demo chạy từ phút 20:30 đến 26:30. Đây là báo cáo tiến độ với dashboard mock, không phải báo cáo kết quả mô hình cuối cùng.

| Slide | Nội dung | Mốc thời gian |
| --- | --- | --- |
| 1 | Giám sát và dự báo ngắn hạn giá phòng khách sạn tại Việt Nam | 00:00-01:00 |
| 2 | Giá hiện tại chưa cho biết xu hướng | 01:00-02:30 |
| 3 | Phạm vi nghiên cứu và sản phẩm | 02:30-04:00 |
| 4 | Hai mốc thời gian trong một chuỗi giá | 04:00-06:00 |
| 5 | Giữ cùng phòng và điều kiện giá | 06:00-08:30 |
| 6 | Luồng dữ liệu và vị trí dashboard | 08:30-10:30 |
| 7 | Mục tiêu dự báo và persistence | 10:30-12:00 |
| 8 | Hai chế độ suy luận | 12:00-13:30 |
| 9 | Train validation test theo thời gian | 13:30-15:30 |
| 10 | Accuracy cao cần đi kèm bằng chứng | 15:30-17:00 |
| 11 | Hướng giá và gợi ý thời điểm | 17:00-19:00 |
| 12 | Giới hạn của bằng chứng hiện tại | 19:00-20:30 |
| 13 | Demo góc nhìn chủ khách sạn | 20:30-23:00 |
| 14 | Demo chuỗi có lịch sử | 23:00-25:00 |
| 15 | Demo cold start và giới hạn ngày | 25:00-26:30 |
| 16 | Tiến độ và bằng chứng cần bổ sung | 26:30-28:00 |
| 17 | Các bước nghiệm thu tiếp theo | 28:00-29:00 |
| 18 | Kết luận | 29:00-30:00 |

### Mốc phải nhớ

20:30 mở web. 23:00 chuyển Consumer. 25:00 đổi sang cold-start. 26:30 quay lại slide tiến độ. 30:00 kết thúc và nhận câu hỏi.

Lời thoại từng slide nằm trong Speaker Notes của PPTX và Kich-ban-va-loi-thoai.md. Tập với đồng hồ, kết hợp nói, thao tác và khoảng dừng. Bảng này là ngân sách thời gian, không phải bảo đảm đọc nguyên văn sẽ đúng 30 phút.

## Chuẩn bị trước buổi demo

Kiểm một lần trước giờ trình bày 15-20 phút

### 1. Mở ứng dụng và file dự phòng

Dùng thư mục frontend hiện có. Nếu node_modules đã sẵn sàng, không cần cài lại hoặc nâng phiên bản ngay trước buổi demo.

```powershell
cd D:\MSE\CAPSTONE\hotel-price-intelligence\dashboard-frontend
npm run dev
```

Mở hai tab: http://localhost:3100/hotelier và http://localhost:3100/consumer. Mở sẵn PDF slide. Giữ terminal chạy, kiểm port mà terminal thông báo. Không khởi động thêm một server nếu port đang được dùng.

### 2. Đặt lại trạng thái

Hotelier: check-in 28/11/2026. Consumer: Sương Mai, 28/11/2026. Chọn lại ngày bằng lịch nếu input dùng định dạng MM/DD/YYYY. Zoom trình duyệt 100%, ưu tiên màn hình rộng, tắt thông báo cá nhân. Không mở file chứa mật khẩu hoặc thông tin cấu hình lên màn chiếu.

Ngày giả lập của app là 18/11/2026, không phải ngày máy tính. Hotelier cho chọn 19/11-02/12. Consumer cho chọn 19/11-18/12. Không dùng ngày ngoài fixture để thử ngẫu hứng.

### 3. Tập luồng enriched và cold-start

Chạy Consumer một lần với Sương Mai + 28/11 để thấy 12 snapshot, approved, medium. Đổi 29/11 và chạy lại để thấy 1 snapshot, proposed, low. Sau khi kiểm, reload tab Consumer để buổi demo bắt đầu ở trạng thái chờ thao tác.

### 4. Lời mở demo phải nói

“Đây là dashboard minh họa bằng dữ liệu hư cấu. Nút kiểm tra giá mô phỏng quy trình, không gọi Booking.com hoặc mô hình thật. Em dùng demo để trình bày luồng sản phẩm và cách thể hiện giới hạn thông tin.”

Không cần backend, MySQL, .env hoặc crawler cho demo này. Không chạy training hay thao tác warehouse để làm màn hình trông như dữ liệu trực tiếp.

## Demo 1: chủ khách sạn

Slide 13 | 20:30-23:00 | 2 phút 30 giây

### 00:00-00:25  Mở /hotelier

Giữ ngày 28/11/2026. Chỉ vào nhãn dữ liệu mẫu và ngày mẫu 18/11. Nói: “Góc nhìn này giúp chủ khách sạn theo dõi vị thế giá, không tự động thay đổi giá bán.”

### 00:25-01:00  Đọc vị thế giá

Chỉ lần lượt 1.120.000 đồng, trung bình 950.764 đồng, +17,8%, hạng 8/9. Nói: “Thứ hạng này xếp giá từ thấp đến cao, không phải chất lượng. Chênh lệch chưa điều chỉnh khác biệt phòng và dịch vụ.”

### 01:00-01:35  Bảng đối thủ

Cuộn xuống bảng, bấm Điểm để sắp xếp, bấm Giá để trở lại. Chỉ hai dòng hết phòng. Nói: “Fixture vẫn dùng giá quan sát gần nhất của các cơ sở hết phòng khi tính trung bình. Khi dùng thật phải kiểm độ tươi và điều kiện so sánh.”

### 01:35-02:10  Biểu đồ và horizon

Quay lại biểu đồ. Nói: “Trục này là các ngày check-in khác nhau, không phải lịch sử của một kỳ lưu trú.” Chỉ h1 +0,8%, h3 +1,5% thuộc stable, h7 +4,6% thuộc tăng. Không đọc h14 như một dự báo thật hợp lệ.

### 02:10-02:30  Chốt và đổi tab

Nói: “Màn hình gom vị thế, xu hướng mẫu và tình trạng phòng vào một góc nhìn. Nó cung cấp thông tin tham khảo, không bảo đảm cần tăng hoặc giảm giá.” Chuyển sang /consumer.

### Đối chiếu nhanh

| Điểm kiểm | Kết quả mẫu |
| --- | --- |
| Check-in | 28/11/2026 |
| Giá / trung bình nhóm | 1.120.000 / 950.764 đồng |
| Chênh lệch / thứ hạng | +17,8% / 8 trên 9 |
| Đối thủ hết phòng | 2 trên 8 |

Nếu số khác bảng, kiểm lại ngày trước. Không sửa code hoặc dữ liệu trong lúc thuyết trình. Nếu vẫn không khớp, dùng ảnh hotelier.jpg và nói rõ chuyển sang bản chụp mock.

## Demo 2: chuỗi có lịch sử

Slide 14 | 23:00-25:00 | 2 phút

### 00:00-00:25  Chọn đúng kịch bản A

Chọn Khách sạn Sương Mai, check-in 28/11/2026. Nói: “Em giữ cùng khách sạn, phòng và kỳ lưu trú để xem diễn biến giá qua các lần quan sát.”

### 00:25-00:45  Bấm Kiểm tra giá

Bấm một lần, đợi năm bước hoàn tất. Nói: “Các bước đang được mô phỏng, không tạo request đến Booking.com.” Mỗi bước khoảng 700 ms, cộng độ trễ trả fixture, không phải độ trễ API thực tế.

### 00:45-01:20  Giải thích lịch sử và mode

Chỉ history_enriched, medium, approved, 12 snapshot trong 11 ngày. Lịch sử 07/11-18/11, giá từ 1.050.000 lên 1.120.000 đồng. Nói: “Nét liền là lịch sử của cùng kỳ lưu trú, nét đứt là dự báo minh họa. Tier không phải xác suất và chưa được hiệu chuẩn.”

### 01:20-02:00  Gợi ý có lý do

Chỉ h3 +2,7%, giá 1.150.240 đồng, chênh 30.240 đồng và gợi ý Nên đặt sớm. Nói: “Quy tắc dùng các horizon gần tạo gợi ý tham khảo. Chênh lệch dự báo không phải khoản tiết kiệm được bảo đảm.” Không dành thời gian đọc cả bốn horizon.

### Đối chiếu nhanh

| Trường | Kết quả mẫu |
| --- | --- |
| Mode / reference / tier | history_enriched / approved / medium |
| Snapshots / span | 12 / 11 ngày |
| Giá hiện tại | 1.120.000 đồng |
| h3 / thay đổi | 1.150.240 đồng / +2,7% |
| Gợi ý | Nên đặt sớm, có cảnh báo tham khảo |

h14 tính từ 18/11 rơi vào 02/12, sau check-in 28/11. UI mock vẫn có thẻ để minh họa hợp đồng. Khi serving thật cần chặn horizon không hợp lệ, không diễn giải thẻ này như bằng chứng model hỗ trợ.

## Demo 3: cold-start và kết thúc

Slide 15 | 25:00-26:30 | 1 phút 30 giây

### 00:00-00:25  Đổi ngày

Giữ Sương Mai, đổi check-in sang 29/11/2026. Bấm Kiểm tra giá, đợi kết quả. Mọi lựa chọn khác với kịch bản A trong phạm vi hỗ trợ đều minh họa cold-start.

### 00:25-01:00  Đọc giới hạn thông tin

Chỉ cold_start, 1 snapshot, span 0 ngày, proposed, low. Nói: “Chuỗi này chưa có lịch sử. Kết quả là đánh giá sơ bộ, mức hỗ trợ thấp hơn, không phải kết luận model cold-start đã tốt.” Chỉ cảnh báo và gợi ý chưa có cơ sở để chờ.

### 01:00-01:30  Kiểm ngày và quay lại slide

Chuyển lịch sang tháng 12, chỉ ngày 18 còn chọn được, 19 đã khóa. Không cần bấm đổi sang một kịch bản thứ tư. Nói: “Phạm vi này tính từ ngày mẫu, không phải ngày hiện tại.” Quay về slide 16 lúc 26:30.

### Nếu web trục trặc: tối đa 20 giây xử lý

Trang không mở: kiểm URL và terminal một lần. Nếu chưa khôi phục được, mở slide 13-15 hoặc ảnh trong assets, dùng cùng lời giải thích. Công bố chuyển sang ảnh chụp, không giả vờ ảnh là tương tác trực tiếp.

Kết quả enriched không xuất hiện: chọn lại đúng Sương Mai + 28/11 và chạy một lần. Khi date input khác locale, dùng lịch. Nếu vẫn lỗi, chuyển consumer-history.jpg. Đừng sửa fixture trên sân khấu.

Quy trình mô phỏng không hoàn tất sau 10 giây: reload một lần. Nếu lặp lại lỗi, dùng ảnh consumer-cold.jpg hoặc consumer-history.jpg. Không truy cập crawler để thay thế.

### Nếu bị chậm thời gian

Giữ đủ ba ý bắt buộc: vị thế giá Hotelier, enriched có lịch sử, cold-start có cảnh báo. Bỏ sắp xếp theo Điểm và phần mở lịch tháng 12 trước. Không bỏ nhãn mock. Tiến độ và kết luận vẫn cần tối thiểu 2 phút, không lấn thời gian hỏi đáp.

## Hỏi đáp và cập nhật trước khi bảo vệ

Không tính vào 30 phút | Không tự điền kết quả chưa kiểm chứng

### Số liệu này có phải giá thật?

Không. Toàn bộ tên, giá, phòng trống và dự báo trên dashboard hiện là fixture hư cấu. Demo chứng minh luồng giao diện, chưa chứng minh chất lượng mô hình hoặc kết nối dữ liệu thật.

### Model đã đạt Accuracy@20% 80% chưa?

80% là mục tiêu đề tài, không phải thành tích của run mới. Chỉ trả lời bằng report đã kiểm đúng version, n, horizon và split. Cần baseline persistence, lift và CI theo cụm khách sạn, không chỉ accuracy cao.

### Tại sao không chia dữ liệu ngẫu nhiên?

Các dòng của cùng chuỗi và thời điểm gần nhau có phụ thuộc. Chia theo thời gian và purge theo horizon để nhãn không vượt biên. Test dev đã lộ chỉ là exploratory, không dùng chọn mô hình hay ngưỡng.

### Medium nghĩa xác suất dự báo đúng bao nhiêu?

Không có ánh xạ xác suất đã hiệu chuẩn. Tier và số snapshot đang minh họa mức hỗ trợ. Không vẽ hoặc giải thích dải tin cậy chưa có căn cứ.

### Giá cao hơn đối thủ có nghĩa cần giảm giá?

Chưa đủ. Cần so sánh cùng loại phòng, rate, chất lượng, độ tươi và trạng thái phòng. Hết phòng không tự chứng minh cầu tăng hoặc doanh thu tốt.

### Hệ thống có đặt phòng hoặc cam kết tiết kiệm?

Không. Gợi ý thời điểm là quy tắc tham khảo từ dự báo giá niêm yết, không tạo giao dịch và không cam kết giá thấp nhất.

### Những chỗ cần cập nhật khi có kết quả thật

Slide 12 và 16: cập nhật trạng thái dataset, Colab, API bằng bằng chứng mới. Slide 7 và 10: bổ sung bảng model/persistence trên cùng mẫu, n, MAE, Accuracy@20%, lift, CI và version. Nếu chèn slide kết quả mới, phân bổ lại thời gian, không cộng vào 30 phút mặc định.

Tên đề tài tiếng Anh trong proposal.tex và README đang khác nhau. Xác nhận tên nộp chính thức trước bản cuối. Nội dung này dùng tên mô tả tiếng Việt, không tự sửa proposal. Sau khi nối API thật, tập lại cả lịch, modes, phòng/rate, missing data và horizon hợp lệ.

## Lời thoại đầy đủ theo slide

Không bắt buộc đọc nguyên văn. Điều chỉnh nhịp nói qua diễn tập, không thay kết luận và không bỏ cảnh báo mock.

### Slide 01. Giám sát và dự báo ngắn hạn giá phòng khách sạn tại Việt Nam

Mốc: 00:00-01:00. Ngân sách: 1 phút.

Kính thưa thầy cô, em là Đặng Nguyễn Minh An, mã học viên 24MSE23217. Em trình bày hệ thống giám sát giá phòng khách sạn và dự báo ngắn hạn tại Việt Nam. Bài trình bày có ba phần: bài toán và thiết kế dữ liệu, phương pháp đánh giá dự báo, sau đó là demo sản phẩm và các việc còn phải hoàn thành.
Em xin phân biệt rõ hai lớp bằng chứng ngay từ đầu. Phần thiết kế và quy trình đánh giá mô tả cách hệ thống cần hoạt động. Dashboard trong buổi demo hiện dùng dữ liệu hư cấu để minh họa luồng người dùng, chưa nối mô hình thật. Em không dùng các con số trên giao diện làm kết quả thực nghiệm. Tổng thời gian trình bày là 30 phút, trong đó khoảng 6 phút dành cho demo. Phần hỏi đáp dùng các slide phụ lục.

### Slide 02. Giá hiện tại chưa cho biết xu hướng

Mốc: 01:00-02:30. Ngân sách: 1.5 phút.

Một mức giá trên trang OTA chỉ mô tả thời điểm đang quan sát. Khi thấy một phòng giá 1,12 triệu đồng, người dùng chưa biết giá đó vừa tăng, vừa giảm hay đã giữ nguyên nhiều ngày. Đối với chủ khách sạn, câu hỏi còn bao gồm so sánh với nhóm đối thủ ở cùng thành phố và cùng ngày check-in. Với người chuẩn bị đặt phòng, câu hỏi là diễn biến gần đây của sản phẩm họ đang quan tâm.
Hai nhóm người dùng có nhu cầu khác nhau nhưng cùng cần một nguồn dữ liệu theo thời gian. Vì vậy, em đặt bài toán thu thập lặp lại trước bài toán dự báo. Một hệ thống chỉ lấy một lần giá rẻ nhất rồi vẽ biểu đồ sẽ có nguy cơ nối các loại phòng khác nhau thành một chuỗi. Khi trình bày demo, em sẽ chỉ ra cả giá quan sát, thời điểm quan sát và điều kiện phòng. Giá cao hơn đối thủ cũng không tự động có nghĩa là định giá sai, vì chất lượng và điều kiện dịch vụ có thể khác. Hệ thống hỗ trợ quan sát và diễn giải, không quyết định mức giá bán thay người vận hành.

### Slide 03. Phạm vi nghiên cứu và sản phẩm

Mốc: 02:30-04:00. Ngân sách: 1.5 phút.

Theo proposal, phạm vi nghiên cứu bao gồm Hà Nội, Thành phố Hồ Chí Minh, Vũng Tàu, Đà Lạt và Phú Quốc, với cohort mục tiêu 355 cơ sở. Đây là phạm vi thiết kế, không phải tuyên bố rằng tất cả cơ sở hiện đã có chuỗi đủ dài hay đủ nhãn cho mọi horizon. Mỗi horizon phải đi qua kiểm tra độ đầy đủ dữ liệu riêng. Phần demo dùng ba khách sạn hư cấu tại Đà Lạt và tám đối thủ hư cấu, nên các tên trên màn hình không phải mẫu nghiên cứu thật.
Nguồn giá là Booking.com và đối tượng dự báo là giá niêm yết trong điều kiện truy vấn cố định. Hệ thống không tích hợp thanh toán, không xử lý đặt phòng và không đo doanh thu hay tỷ lệ lấp đầy. Bốn horizon 1, 3, 7, 14 ngày là mục tiêu của đề tài. Việc giao diện có bốn thẻ minh họa không chứng minh rằng bốn mô hình đã sẵn sàng phục vụ. Khi tích hợp thật, API phải chỉ công bố các horizon có dữ liệu, mô hình và điều kiện sử dụng hợp lệ. Em giữ riêng phạm vi mục tiêu, trạng thái triển khai và bằng chứng thực nghiệm để tránh trộn lẫn chúng.

### Slide 04. Hai mốc thời gian trong một chuỗi giá

Mốc: 04:00-06:00. Ngân sách: 2 phút.

Một điểm quan trọng của bài toán là phân biệt ngày quan sát và ngày lưu trú. Giả sử hôm nay là 18 tháng 11, người dùng đang xem phòng cho check-in ngày 28 tháng 11. Em quan sát cùng kỳ lưu trú này nhiều lần vào các ngày khác nhau. Horizon ba ngày có nghĩa là dự báo mức giá mà trang niêm yết sẽ hiển thị vào ngày 21 tháng 11, vẫn cho kỳ lưu trú ngày 28 tháng 11. Nó không có nghĩa là đổi ngày check-in sang ngày 21.
Lead time ở thời điểm ban đầu là mười ngày. Khi quan sát ngày 21, lead time còn bảy ngày. Cách tổ chức này cho phép học sự thay đổi giá khi ngày đến gần, đồng thời tách hai loại biến động: đổi giá của cùng kỳ lưu trú và khác giá giữa các ngày lưu trú. Biểu đồ Hotelier theo check-in thể hiện loại thứ hai, còn lịch sử Consumer thể hiện loại thứ nhất.
Ví dụ này cũng có một giới hạn cần nói thẳng: h14 tính từ ngày 18 sẽ rơi vào ngày 2 tháng 12, sau check-in ngày 28 tháng 11. Dashboard hiện hiển thị cả bốn thẻ để minh họa hợp đồng giao diện. Khi nối dữ liệu thật, hệ thống phải kiểm horizon còn hợp lệ với kỳ lưu trú và nhãn có sẵn. Em không dùng thẻ h14 trong ví dụ này để kết luận có thể dự báo giá của một kỳ lưu trú đã qua.

### Slide 05. Giữ cùng phòng và điều kiện giá

Mốc: 06:00-08:30. Ngân sách: 2.5 phút.

Để một chuỗi giá có ý nghĩa, các lần quan sát phải mô tả sản phẩm có thể so sánh. Em cần giữ khách sạn, ngày check-in, loại phòng và các điều kiện rate. Bữa sáng, hủy miễn phí, cách thanh toán hoặc số khách đều có thể tạo ra mức giá khác nhau. Nếu hôm nay lấy phòng rẻ nhất không có bữa sáng và ngày mai lấy một phòng có bữa sáng, chênh lệch không còn thuần túy là biến động giá.
Reference là định nghĩa phòng và điều kiện giá dùng để theo dõi chuỗi. Trạng thái proposed cho biết hệ thống mới đề xuất tham chiếu, còn approved cho biết tham chiếu đã qua bước duyệt tương ứng. Trong demo, hai trạng thái này là fixture, không phải kết quả kiểm duyệt trên dữ liệu thật. Khi chưa có reference phù hợp hoặc chuỗi bị gián đoạn, hệ thống phải thể hiện giới hạn thay vì tự coi tất cả các dòng là một chuỗi liên tục.
Em cũng phân biệt hết phòng với lỗi thu thập. Hết phòng có thể là kết quả hợp lệ của lần quan sát, nhưng không tạo ra giá mới để thay thế một cách tùy ý. Một giá quan sát gần nhất cần có thời điểm và trạng thái đi kèm. Với bảng đối thủ hiện tại, trung bình mẫu vẫn dùng giá gần nhất của hai khách sạn hết phòng và giao diện nêu rõ điều đó. Đây là quy tắc minh họa, cần đánh giá lại độ tươi và khả năng so sánh khi dùng dữ liệu thật.

### Slide 06. Luồng dữ liệu và vị trí dashboard

Mốc: 08:30-10:30. Ngân sách: 2 phút.

Luồng thiết kế bắt đầu từ việc thu thập các lần quan sát. Warehouse lưu dữ liệu và thông tin nguồn để bước sau có thể truy ngược một kết quả về dữ liệu đầu vào. Dataset builder tạo mẫu, nhãn, đặc trưng và các tập theo thời gian. Huấn luyện và tuning nặng chạy trên Colab để tránh tranh tài nguyên với máy đang thu thập. Cuối cùng, API suy luận sẽ cung cấp kết quả cho dashboard Hotelier và Consumer.
Điều quan trọng là mỗi lớp có trách nhiệm riêng. Frontend không tự chọn loại phòng từ dữ liệu thô, không tự đọc model và không tự quyết mô hình nào tốt nhất. Nó hiển thị hợp đồng dữ liệu từ API, gồm giá hiện tại, thời điểm quan sát, chế độ suy luận, lịch sử, horizon được hỗ trợ, dự báo và cảnh báo. Cấu trúc hiện tại tập trung việc lấy dữ liệu ở lib/api.ts, nên có thể thay implementation sau khi backend sẵn sàng.
Hình trên mô tả kiến trúc mục tiêu, không chứng minh luồng end-to-end đã nối xong. Trong buổi demo, lib/api.ts chỉ trả dữ liệu hư cấu có độ trễ giả. Không có request đến Booking.com, không chạy crawler và không đọc MySQL. Em chọn cách trình diễn này để kiểm tra tương tác và cách diễn giải thông tin mà không làm thay đổi quá trình vận hành hiện có. Việc nối API và đo độ trễ thật là bước nghiệm thu riêng, phải có bằng chứng sau khi tích hợp.

### Slide 07. Mục tiêu dự báo và persistence

Mốc: 10:30-12:00. Ngân sách: 1.5 phút.

Persistence là baseline giữ nguyên giá: dự báo giá của lần quan sát tương lai bằng giá vừa quan sát. Đây là đối chứng bắt buộc vì giá phòng có thể giữ nguyên trong nhiều lần thu thập liên tiếp. Trong một tập như vậy, một mô hình luôn giữ nguyên giá có thể đạt accuracy cao mà không học được nhiều thông tin mới.
Các ứng viên hồi quy như Random Forest và XGBoost cần so sánh với baseline trên cùng mẫu, cùng split và cùng horizon. Em không chọn mô hình chỉ vì nó có tên phức tạp hơn. Câu hỏi là mức giảm sai số ngoài mẫu có đủ rõ, có ổn định và có phụ thuộc vào một số ít khách sạn hoặc đợt tăng giá đột biến hay không. MAE cho biết sai số theo VND, còn Accuracy@20% giữ vai trò mục tiêu khả thi đã chốt cho đề tài.
Ở bài trình bày này, em không đưa một bảng điểm model mới vì lần chạy sửa dữ liệu chưa có kết quả được kiểm chứng tại thời điểm chuẩn bị. Em có thể trình bày cách đo và tiêu chí chọn mô hình, nhưng phải chờ report, dự đoán và provenance của run hợp lệ mới đưa ra kết luận về model tốt hơn persistence.

### Slide 08. Hai chế độ suy luận

Mốc: 12:00-13:30. Ngân sách: 1.5 phút.

Hai chế độ suy luận giúp người xem biết kết quả dựa trên lượng thông tin nào. History-enriched sử dụng lịch sử của chuỗi khi các điều kiện hỗ trợ đã thỏa mãn. Cold-start dành cho trường hợp chưa có lịch sử phù hợp. Trong demo, kịch bản có lịch sử được cố định ở Khách sạn Sương Mai với check-in ngày 28 tháng 11, gồm 12 snapshot trải 11 ngày. Các lựa chọn khác minh họa cold-start với một snapshot.
Em nhấn mạnh rằng số 12 không phải ngưỡng thống kê đã chứng minh để gắn tier medium. Tương tự, tier low, medium hoặc high không phải xác suất dự báo đúng. Đây là phần giao diện minh họa cách truyền đạt mức độ hỗ trợ và cảnh báo. Khi tích hợp thật, chính sách chuyển chế độ và hiệu chuẩn tier cần có định nghĩa và kiểm chứng riêng.
Với cold-start, giao diện phải giữ dòng đánh giá sơ bộ và cảnh báo thiếu lịch sử. Không nên để cùng một câu gợi ý trông chắc chắn như trường hợp có nhiều quan sát. Em cũng không vẽ dải tin cậy vì chưa có khoảng bất định được hiệu chuẩn. Cách hiển thị thận trọng này giúp người dùng phân biệt một mức giá dự báo với độ tin cậy của cơ sở tạo ra nó.

### Slide 09. Train validation test theo thời gian

Mốc: 13:30-15:30. Ngân sách: 2 phút.

Một lỗi dễ mắc trong bài toán này là chia ngẫu nhiên các dòng. Các quan sát gần nhau của cùng chuỗi có thể đi vào cả train và test, trong khi nhãn ở tương lai làm thông tin vượt qua biên tập. Vì vậy em chia theo thời gian quan sát, sau đó kiểm thời điểm nhãn với horizon tương ứng. Mục đích của purge là tránh mẫu train có nhãn chạm sang giai đoạn validation hoặc test.
Tuning chỉ dùng tập train với cross-validation theo thời gian. Validation dùng để chọn ứng viên và các quy tắc đã đăng ký trước. Test chỉ dùng để báo cáo sau khi đã khóa lựa chọn. Không được thấy kết quả test rồi đổi feature, ngưỡng hoặc loại dòng để làm số đẹp hơn và vẫn gọi đó là đánh giá cuối.
Proposal mô tả embargo 14 ngày cho bài toán đủ bốn horizon. Pipeline dev hiện có thể xây riêng từng horizon với purge tương ứng. Đây là hai cấp thiết kế khác nhau, cần ghi rõ trong báo cáo của từng dataset, không lấy một con số purge áp dụng mù quáng cho mọi run. Test dev đã được xem trong quá trình chẩn đoán nên em coi kết quả đó là exploratory, không phải test độc lập cuối cùng của luận văn. Khi có dữ liệu và dataset mới, mô hình sẽ cần một quy trình khóa lựa chọn và báo cáo phù hợp. Em không dùng việc chạy thêm nhiều lần trên test đã lộ để khẳng định đã loại bỏ leakage.

### Slide 10. Accuracy cao cần đi kèm bằng chứng

Mốc: 15:30-17:00. Ngân sách: 1.5 phút.

Accuracy@20% là tỷ lệ dự báo có sai số tương đối tuyệt đối không vượt quá 20%. Mục tiêu của đề tài là ít nhất 80%. Mục tiêu này không đổi. Tuy nhiên, nếu persistence đã đạt mức cao vì đa số giá không đổi, accuracy của model cao hơn một chút chưa đủ để kết luận model có giá trị rõ ràng.
Em báo cáo cả baseline và phần cải thiện, đồng thời xem MAE theo VND. Khi tính khoảng tin cậy, em cần xét sự phụ thuộc giữa nhiều dòng của cùng khách sạn, thay vì coi hàng nghìn dòng là hàng nghìn quan sát độc lập. Bootstrap theo cụm khách sạn giúp phản ánh vấn đề này tốt hơn trong phép so sánh.
Một bước nữa là kiểm sự tập trung của lợi ích. Nếu toàn bộ cải thiện đến từ một khách sạn hoặc một vài spike, kết quả tổng hợp dễ tạo cảm giác ổn định hơn thực tế. Em sẽ xem theo ngày, chế độ suy luận, nhóm lead time và độ nhạy với các phân nhóm đã đăng ký. Trong slide này, 80% là mục tiêu chứ không phải thông báo run mới đã PASS. Em chưa có căn cứ để trình bày độ chính xác cuối cùng hay khẳng định mô hình không overfit.

### Slide 11. Hướng giá và gợi ý thời điểm

Mốc: 17:00-19:00. Ngân sách: 2 phút.

Giao diện chuyển dự báo hồi quy thành ba nhãn hướng giá. Mức thay đổi tuyệt đối không quá hai phần trăm là ổn định, lớn hơn hai phần trăm là tăng và nhỏ hơn âm hai phần trăm là giảm. Đây là quy tắc phân loại hướng đã chốt, không phải một mô hình phân loại riêng.
Gợi ý thời điểm trong prototype là quy tắc xác định từ các horizon được hỗ trợ trong bảy ngày: có mức tăng ít nhất hai phần trăm thì gợi ý đặt sớm, nếu không có tăng mà có giảm ít nhất hai phần trăm thì có thể chờ, còn lại là chưa có cơ sở để chờ. Nếu xuất hiện cả tăng và giảm trong các mốc gần, prototype ưu tiên nhánh tăng theo thứ tự quy tắc hiện tại. Em cần nói rõ quy tắc này khi báo cáo chứ không giấu nó sau một nhãn AI.
Có một khác biệt tại đúng ranh giới hai phần trăm: nhãn hướng vẫn là stable, trong khi quy tắc gợi ý dùng dấu lớn hơn hoặc bằng. Đây là hai định nghĩa khác nhau trong yêu cầu prototype. Trước khi phục vụ người dùng thật, đặc tả tích hợp cần quyết định cách truyền đạt để tránh gây khó hiểu. Trong demo hiện tại, các ví dụ 2,7% và 5,1% không nằm ở ranh giới này. Gợi ý luôn có số liệu lý do và không tạo giao dịch, không cam kết giá thấp nhất hay tiết kiệm thực tế.

### Slide 12. Giới hạn của bằng chứng hiện tại

Mốc: 19:00-20:30. Ngân sách: 1.5 phút.

Trước phần demo, em tổng kết trạng thái để người xem hiểu đúng những gì sắp thấy. Dashboard đã có hai góc nhìn, bộ chọn ngày, bảng đối thủ, các thẻ dự báo và mô phỏng kiểm tra giá. Phần frontend đã qua lint, build và kiểm tra trình duyệt ở hai kích thước, nhưng đây là kiểm tra phần mềm và luồng giao diện. Nó không đo chất lượng model.
Mô hình và dataset thật đang ở một luồng công việc riêng. Hồ sơ tại thời điểm chuẩn bị tài liệu đã có pre-review cho một bản vá hẹp, còn các bước thực thi tiếp theo phải đợi bằng chứng runtime và cửa sổ tránh crawler. Em không lấy giờ dự kiến chạy làm lời hứa hoàn thành. Khi có kết quả mới, slide tiến độ và bảng đánh giá cần cập nhật từ artifact đã kiểm chứng.
Buổi demo hiện tại trả lời các câu hỏi về sản phẩm: người dùng xem được thông tin gì, diễn giải ra sao và hệ thống thể hiện thiếu dữ liệu như thế nào. Nó chưa trả lời câu hỏi model đạt bao nhiêu accuracy hay tín hiệu đặt phòng tạo lợi ích thực tế bao nhiêu. Em sẽ giữ nhãn dữ liệu mẫu trên màn hình xuyên suốt để tránh người xem hiểu nhầm đây là dữ liệu đang crawl trực tiếp.

### Slide 13. Demo góc nhìn chủ khách sạn

Mốc: 20:30-23:00. Ngân sách: 2.5 phút.

Chuyển sang tab /hotelier, giữ check-in 28/11/2026. Em bắt đầu bằng nhãn dữ liệu mẫu và ngày quan sát cố định 18/11/2026. Giá của khách sạn Sương Mai là 1.120.000 đồng, trung bình tám đối thủ là 950.764 đồng. Từ hai con số đó, chênh lệch làm tròn là 17,8%. Thứ hạng 8 trên 9 xếp từ giá thấp đến cao, tức không phải xếp hạng chất lượng hay điểm review.
Cuộn đến bảng đối thủ, bấm Điểm để cho thấy sắp xếp được, sau đó bấm Giá để quay lại thứ tự theo giá. Em chỉ ra hai trạng thái hết phòng và giải thích trung bình mẫu vẫn tính giá quan sát gần nhất của hai đối thủ này. Điều kiện giá có thể khác giữa các khách sạn nên chênh lệch không đủ để yêu cầu tăng hay giảm giá bán.
Quay lại biểu đồ theo ngày check-in. Hai đường so sánh giá hiện tại cho các ngày lưu trú khác nhau trong 14 ngày tới. Đây không phải lịch sử giá của một kỳ lưu trú. Sau đó chỉ vào dự báo mặt bằng nhóm: h1 và h3 ổn định, h7 minh họa tăng 4,6%. Em nhắc h14 là thẻ minh họa hợp đồng và chưa chứng minh tính hợp lệ của horizon sau ngày check-in. Kết thúc góc nhìn này bằng ba cảnh báo: vị thế giá, xu hướng mẫu và tình trạng phòng. Có thể dành khoảng 20 giây để hội đồng nhìn số liệu trước khi chuyển sang Consumer.

### Slide 14. Demo chuỗi có lịch sử

Mốc: 23:00-25:00. Ngân sách: 2 phút.

Mở /consumer và chọn Khách sạn Sương Mai với check-in 28/11/2026. Trước khi bấm, em nói rõ nút Kiểm tra giá chạy một quy trình mô phỏng năm bước: mở Booking.com, đọc bảng phòng, lưu snapshot, dựng đặc trưng và dự báo. Các tên bước mô tả luồng dự kiến, nhưng ở prototype chúng không mở website hay tạo snapshot thật. Bấm một lần và đợi hoàn tất, không bấm liên tục khi đang chạy.
Sau khoảng vài giây, kết quả hiển thị history-enriched, reference approved và tier medium. Em chỉ ra 12 snapshot trải 11 ngày, từ 7 đến 18 tháng 11. Giá mẫu tăng từ 1.050.000 lên 1.120.000 đồng. Đây là lịch sử của cùng kỳ lưu trú, khác với trục check-in của biểu đồ Hotelier. Nét liền là lịch sử, nét đứt là điểm dự báo minh họa.
Ở h3, mức thay đổi 2,7% tương ứng 30.240 đồng so với giá hiện tại. Quy tắc tạo gợi ý Nên đặt sớm và hiển thị số liệu lý do. Em không diễn giải tier medium là xác suất 50% hay 80%, cũng không gọi 30.240 đồng là khoản tiết kiệm sẽ đạt được. Kết quả này chứng minh giao diện nối các trường dữ liệu và quy tắc minh họa nhất quán, chưa chứng minh giá thật sẽ tăng như dự báo. Nếu trình bày chậm hơn dự kiến, em bỏ phần đọc từng horizon và chỉ giữ h3 cùng cảnh báo cuối panel.

### Slide 15. Demo cold start và giới hạn ngày

Mốc: 25:00-26:30. Ngân sách: 1.5 phút.

Giữ khách sạn Sương Mai và đổi check-in sang ngày 29 tháng 11, rồi bấm Kiểm tra giá. Mọi lựa chọn khác với kịch bản A trong khoảng hỗ trợ minh họa cold-start. Lần này chỉ có một snapshot, span lịch sử bằng không, reference proposed và tier low. Em chỉ vào cảnh báo Chưa có lịch sử của chuỗi này và dòng đánh giá sơ bộ.
Đa số horizon mẫu gần như ổn định, nên gợi ý là chưa có cơ sở để chờ, đi kèm định lượng các mức thay đổi. Em không nói model cold-start đã đạt hiệu quả vì đây vẫn là fixture. Điều cần chứng minh trên màn hình là người dùng nhận được mức cảnh báo khác khi thiếu lịch sử.
Để minh họa phạm vi ngày, chuyển lịch sang tháng 12: ngày 18 còn chọn được, ngày 19 đã khóa. Đây là khoảng một đến ba mươi ngày tính từ ngày mẫu 18 tháng 11, không tính từ ngày diễn ra buổi thuyết trình. Không cần nhập ngày sai bằng bàn phím trong buổi demo chính, tránh phụ thuộc cách date input của trình duyệt nhận định dạng ngày. Nếu có thêm thời gian, có thể minh họa thông báo ngoài phạm vi bằng thao tác đã tập trước. Sau đó quay lại slide tiến độ.

### Slide 16. Tiến độ và bằng chứng cần bổ sung

Mốc: 26:30-28:00. Ngân sách: 1.5 phút.

Phần đã có trong dashboard là bộ khung sản phẩm và hợp đồng dữ liệu mock. Hai route đã chạy được, và luồng enriched, cold-start, giới hạn ngày, sắp xếp và các cảnh báo đã được kiểm ở mức phần mềm. Trong quá trình xây dựng, các lệnh lint và build qua, hai route trả HTTP 200. Những bằng chứng đó giúp xác nhận frontend sẵn sàng cho bước tích hợp, không thay thế đánh giá model.
Phần tiếp theo là kiểm artifact dataset sau sửa, xác minh bộ package Colab, chạy huấn luyện và so sánh với baseline. Sau khi chốt model và chính sách serving, API mới nối vào dashboard. Các horizon phải phản ánh khả năng hỗ trợ thật chứ không dùng mặc định bốn giá trị như fixture.
Em chưa có bảng kết quả cuối cùng để điền accuracy, MAE hay confidence interval của run mới. Khi cập nhật slide này, em cần dùng report và predictions đúng version, kèm số mẫu và phân biệt validation với test. Nếu kết quả không thắng persistence rõ ràng, báo cáo phải nói điều đó và xác định cách sử dụng phù hợp, thay vì chỉ chọn một con số accuracy cao để trình bày.

### Slide 17. Các bước nghiệm thu tiếp theo

Mốc: 28:00-29:00. Ngân sách: 1 phút.

Em tổ chức các bước nghiệm thu theo phụ thuộc thực tế. Trước hết là dataset hợp lệ với version và provenance rõ ràng. Sau đó mới có căn cứ chạy huấn luyện và đọc kết quả model. Phần tích hợp API cần xác định horizon hợp lệ với ngày lưu trú, giữ thông tin phòng và rate, đồng thời thể hiện rõ các trường hợp không hỗ trợ.
Cuối cùng là đánh giá end-to-end và các mô phỏng quyết định đã đề xuất. Mô phỏng Hotelier có thể xem tín hiệu lệch vị thế giá, nhưng không thể quy đổi trực tiếp ra doanh thu vì dữ liệu không có booking. Mô phỏng Consumer đo chênh lệch giá niêm yết của chính sách, kèm trường hợp chờ bị bất lợi, không đo khoản tiết kiệm đã thực hiện. Các bước này không có ngày hoàn thành được bảo đảm trong bài trình bày, vì chúng phụ thuộc kết quả kiểm chứng.

### Slide 18. Kết luận

Mốc: 29:00-30:00. Ngân sách: 1 phút.

Em xin kết lại bằng ba nội dung chính. Thứ nhất, bài toán dự báo giá phòng đòi hỏi dữ liệu theo thời gian có khả năng so sánh, trong đó ngày quan sát và ngày lưu trú là hai tọa độ độc lập. Thứ hai, mục tiêu Accuracy@20% ít nhất 80% phải đi kèm baseline, lift ngoài mẫu và kiểm tra rò rỉ, không chỉ nhìn accuracy tổng hợp. Thứ ba, giao diện cần truyền đạt giới hạn dữ liệu, đặc biệt ở cold-start và các horizon chưa được hỗ trợ.
Demo hiện tại cho thấy cách hai nhóm người dùng đọc cùng cấu trúc dự báo qua hai góc nhìn khác nhau. Nó vẫn là prototype mock, chưa nối model thật và chưa tạo giao dịch. Phần còn lại của đề tài là kiểm dataset và kết quả huấn luyện, tích hợp API rồi đánh giá end-to-end theo các tiêu chí đã chốt. Em cảm ơn thầy cô và xin nhận câu hỏi.

### Slide 19. Hỏi đáp về dự báo

Phụ lục hỏi đáp, ngoài 30 phút.

Nếu hỏi về accuracy, giữ mục tiêu 80% nhưng giải thích vì sao persistence có thể cao khi giá ít đổi. Không nêu số thực nghiệm mới nếu chưa có report đã kiểm. Nếu hỏi cold-start, nói số snapshot và tier ở demo là fixture, chính sách thật cần bằng chứng. Nếu hỏi h14 cho check-in 28/11, thừa nhận nó rơi sau ngày lưu trú trong ví dụ và cần cổng kiểm horizon ở serving. Không suy ra mọi thẻ UI đều là dự báo thật hợp lệ.

### Slide 20. Hỏi đáp về giá và điều kiện phòng

Phụ lục hỏi đáp, ngoài 30 phút.

Chênh lệch 17,8% là so sánh fixture chưa điều chỉnh chất lượng. Tình trạng hết phòng không đo được booking, tồn kho thực hay nguyên nhân không còn bán. Gợi ý đặt sớm xuất phát từ một quy tắc số học xác định của prototype, không phải chứng minh hiệu quả kinh tế. Nếu hỏi sản phẩm có tự đổi giá hay đặt phòng không, trả lời không, phạm vi là thông tin tham khảo.

### Slide 21. Nguồn nội dung và bản trình bày

Phụ lục hỏi đáp, ngoài 30 phút.

Nguồn nội bộ: documents/proposal/proposal.tex và README.md còn có hai cách ghi tên đề tài tiếng Anh khác nhau. Bản trình bày dùng tên mô tả tiếng Việt, không tự sửa tên nộp chính thức. Nội dung phạm vi lấy từ các sections hiện hành, không lấy detailed đã lưu trữ. Dữ liệu minh họa lấy từ dashboard-frontend/lib/mock-data.ts và README.md. Tiến độ dùng hồ sơ discuss/model-results-improvement-20261008/121 và 122, không coi việc pre-review là thực thi đã hoàn tất.

## Ghi chú phiên bản và nguồn

Thiết kế lấy từ documents/proposal/sections hiện hành. Dữ liệu demo lấy từ hotel-price-intelligence/dashboard-frontend/lib/mock-data.ts, types.ts và lib/api.ts. Tiến độ lấy từ hồ sơ discuss 121/122 tại thời điểm chuẩn bị. Kiểm lại trạng thái trước buổi trình bày. Không dùng ảnh UI làm bằng chứng giá thật hoặc hiệu quả model.

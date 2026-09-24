# Phân tích HTML mini-crawl — nhóm trùng canonical key khác nhau ở đâu?

**Ngày:** 2026-09-24 · **Dữ liệu:** run #1 (DB `hotel_price_intel_minicrawl_20260924`), 54 item = 13 hotel DUP + 5 hotel CONTROL × 3 ngày check-in (01/10, 11/10, 18/11/2026),
cào 17:25–17:43 giờ VN một lần duy nhất; 53 item success + 1 sold-out. Bằng chứng chính là **HTML.gz** của từng trang (ảnh chụp chỉ có phần đầu trang, không chứa bảng phòng).
Số liệu tái lập bằng `analyze_minicrawl_html.py` (cùng thư mục); hai bảng CSV đi kèm: `options_dom_matched.csv` (1.948 option + đặc trưng DOM), `dup_groups.csv` (559 nhóm).

> Đây là **bằng chứng** cho cuộc tranh luận canonical key giữa Claude và GPT ở mốc warehouse ba nguồn (~01/10), không phải quyết định. Mẫu chọn theo hotel có nhóm trùng bền nên **không đại diện** cho 354 hotel.

## 1. Parser có đọc đúng bảng không? — Có

- 53 trang thành công có **1.984 dòng** `tr.js-rt-block-row` thuộc bảng phòng = **1.948 option đã lưu + 36 dòng trùng tuyệt đối mà scraper bỏ** (`duplicate_options_count` = 36, cả 36 đều của Vinhomes/Bunnys Homes).
- **1.948/1.948 (100%)** option trong DB ghép 1-1 được với một dòng DOM (1.915 theo giá `data-hotel-rounded-price`, 33 dòng `bbasic` — không có attribute đó — theo số tiền trong ô giá).
- Nghĩa là nhóm trùng canonical key **không** do parser đếm đôi hay bỏ sót: chúng là các dòng thật, phân biệt được, trên trang.

## 2. Cấu trúc trang và `data-block-id`

Mỗi phòng là một khối `th.hprt-table-cell-roomtype` (rowspan = số dòng giá); mỗi dòng giá có `data-block-id` duy nhất, dạng
`<roomId>_<rateId>_<số khách>_<mã bữa ăn>_<cờ>[_<mã gói>]`, ví dụ `665384701_409602035_2_0_0` (giá cơ bản 2 khách) và `665384701_409602035_0_0_0_1410366` (gói "đưa đón sân bay").
Giải mã theo **tương quan với nội dung hiển thị** (không có tài liệu chính thức): thành phần 3 = số khách của dòng giá (khớp "Số người tối đa: N"), thành phần 4 ≈ loại bữa ăn (mã theo từng khách sạn),
hậu tố = gói tiện ích/khuyến mãi. Trang còn có dòng `bbasic_0` (giá cơ bản), dòng banner Genius không phải phòng, và `data-fltrs` (JSON cờ hủy/giường).

## 3. Vì sao có nhóm trùng — chiều nào khác nhau

559 nhóm trùng (item × `room_identity_key` × `rate_plan_key`), 1.301 option = **66,8%** option đã lưu (mẫu này chọn theo hotel có trùng). Kích thước nhóm: 2 (449), 3 (43), 4 (64), 6 (3).

| Chiều khác nhau **duy nhất** trong nhóm | số nhóm | tỷ lệ | giá cao/thấp (trung vị) |
|---|---|---|---|
| dòng **"Chỉ dành cho 1 khách"** vs dòng 2 khách | 324 | 58,0% | 1,06× |
| **tiện ích/gói** (nhận phòng sớm, đậu xe, tín dụng ăn uống, đưa đón sân bay…) | 102 | 18,2% | 1,01× |
| **loại bữa ăn** (sáng / sáng+tối / cả ngày) | 46 | 8,2% | **1,74×** |
| 1 khách + bữa ăn | 34 | 6,1% | 1,40× |
| 1 khách + thanh toán + tiện ích | 21 | 3,8% | 1,14× |
| 1 khách + tiện ích | 18 | 3,2% | 1,10× |
| hủy + thanh toán + tiện ích | 11 | 2,0% | 1,15× |
| khác (3 tổ hợp) | 3 | 0,5% | — |

Theo định danh: **room id khác trong 3 nhóm (0,5%)**, rate id khác 45 nhóm (8,1%), số khách của dòng giá khác 435 nhóm (77,8%), gói khác 152 nhóm (27,2%); `data-block-id` khác nhau ở **559/559** nhóm.
Tức là nhóm trùng chủ yếu là **cùng một phòng, nhiều phương án bán khác nhau**, không phải nhiều căn cùng tên (trừ Vinhomes, nơi 12 dòng của các căn khác id giống hệt nhau đã bị bỏ ở bước dedupe).

## 4. Dòng "Chỉ dành cho 1 khách" — phát hiện quan trọng nhất

- **463/1.948 option đã lưu (23,8%)** là dòng có cảnh báo `single-occupancy-alert` "Chỉ dành cho 1 khách" (giá cho **1** khách, trong khi ngữ cảnh cào cố định là **2 người lớn**). Theo hotel: 50% ở Mövenpick/Mercure/ibis Styles,
  37–40% ở Hilton/The Myst/Dusit/Starview, 0% ở 22Land, Garden Plaza, Imperial, InterContinental, Sol, Roma… (các hotel này không bán giá 1 khách).
- DB lưu `max_occupancy` = **2** cho 457/463 dòng này (giá trị lấy từ dòng đầu của khối phòng, `_get_num_guests(room_type_header, row)`), tức **gắn nhãn sai** số khách.
- 399/559 nhóm trùng (71,4%) chứa cả dòng 1 khách và dòng khác; trong các nhóm đó **option rẻ nhất là dòng 1 khách ở 98,7%** trường hợp. Chênh giá 2 khách − 1 khách thường là một khoản **cố định theo hotel**
  (Mercure 210.000 VND và Mövenpick 300.000 VND ở 100% cặp; Hilton 680.400 và Dusit 238.140 ở khoảng 2/3 cặp; ibis 240.000 ở 60%), không phải tỷ lệ % cố định (ratio dao động 1,03–1,25).
- Hệ quả: một nhóm gồm dòng 1 khách và dòng 2 khách **không phải cùng một sản phẩm**; chọn "rẻ nhất" gần như luôn chọn dòng 1 khách.

## 5. Khóa mở rộng bằng nội dung hiển thị (không cần block-id)

Số nhóm trùng còn lại khi thêm dần vào khóa hiện tại: khóa hiện tại **559** → + cờ 1 khách **299** → + loại bữa ăn **183** → + chính sách hủy **172** → + điều kiện thanh toán **129** → + tiện ích/gói **6** (15 option; đều của Vinhomes/nhiều căn nội dung giống hệt).
Như vậy 5 thuộc tính đọc được từ chính ô điều kiện/ô số khách giải quyết **98,9%** nhóm; `data-block-id` đầy đủ giải quyết 100%.

## 6. Ổn định giữa các ngày check-in (cùng thời điểm cào)

Tỷ lệ `data-block-id` xuất hiện ở cả 3 ngày check-in: trung bình theo hotel **0,70** (min 0,10, max 1,00; 100% ở Mercure, Vinhomes, Pearl Wealth, Diamond Sea). Phần lệch có vẻ do phương án bán thay đổi theo ngày
(ví dụ ưu đãi chỉ áp cho một số đợt lưu trú; thấp nhất là B&K Homestay 0,10 và InterContinental 0,22) — đây là suy đoán, chưa kiểm. Trong mọi trang, `data-block-id` là duy nhất giữa các option đã lưu (0 trường hợp lặp).
**Ổn định theo thời gian (nhiều ngày cào) chưa kiểm chứng** — cần chạy lại cùng workbook.

## 7. Ý nghĩa cho A / B / C (bằng chứng, chưa phải quyết định)

1. **Phương án B "chọn offer đại diện = rẻ nhất"** sẽ trộn sản phẩm: nó chọn dòng 1 khách ở ~99% nhóm có dòng 1 khách (thấp hơn giá 2 khách ~6,6% trung vị, khoản cố định theo hotel), và nhảy khi dòng đó hết/hiện.
   Nếu dùng B cho lịch sử thì cần quy tắc khác "rẻ nhất" và phải kiểm độ ổn định theo ngày (chưa đo).
2. **Phương án C có cơ sở rõ hơn dự đoán:** thuộc tính phân biệt tồn tại, đọc được, và ngắn gọn (số khách của dòng giá, loại bữa ăn, tiện ích/gói, thanh toán). Riêng cờ "1 khách" là **vấn đề hợp lệ của giá** chứ không chỉ chuyện trùng key:
   nó ảnh hưởng mọi thống kê giá ở hotel có dòng 1 khách, kể cả phòng không nằm trong nhóm trùng.
3. **Lịch sử không sửa được bằng dữ liệu đã lưu:** cờ 1 khách, loại bữa ăn chi tiết, tiện ích/gói không được lưu; `max_occupancy` đã lưu sai cho dòng 1 khách. Mọi sửa cho lịch sử chỉ là suy luận theo cấu trúc nhóm.
4. **Đổi cách cào giữa chừng có chi phí thật:** cổng schema-fingerprint của warehouse cần đồng bộ ba collector nếu thêm cột; phương án không đổi schema là **bỏ (và đếm) dòng "chỉ 1 khách" ngay khi cào** (chúng nằm ngoài ngữ cảnh 2 người lớn),
   nhưng làm quần thể đổi theo thời gian (trước/sau ngày sửa). Cần tranh luận: sửa sớm (dữ liệu sạch cho ~2 tháng còn lại) hay giữ định nghĩa nhất quán.

*Lập trường tạm của Claude (sẵn sàng đổi theo phản biện):* không đổi canonical key/version trước cuộc review; nhưng nên xem xét **thu thập thêm thuộc tính phân biệt càng sớm càng tốt** (ít nhất cờ 1 khách + loại bữa ăn + gói + `data-block-id`, dạng raw, không vào khóa),
vì mỗi ngày trễ là mất một ngày dữ liệu có thuộc tính này và không backfill được.

## 8. Giới hạn / chưa kiểm chứng

- Một thời điểm cào, 18 hotel chọn theo có/không có nhóm trùng; **không đại diện** cho toàn cohort hay theo thời gian. Kích thước nhóm hiện nhỏ hơn lịch sử (Mövenpick: nhóm lớn nhất 12 → 2, phòng đó hiện có 4 option chia 2 rate plan; Hilton 6 → 4) — chưa biết do tồn kho thay đổi hay phương án bán thay đổi.
- Phân loại "chiều nội dung" dựa trên regex các dòng điều kiện tiếng Việt (kiểm tay trên nhiều ví dụ nhưng không phải bộ nhãn chuẩn); mã bữa ăn/`cờ` trong block-id chưa giải mã chính thức.
- HTML được lưu **sau** khi parse (cùng phiên); 100% dòng khớp nên không thấy khác biệt, nhưng không loại trừ hoàn toàn thay đổi động ở trang khác.
- Chưa kiểm: cùng hotel/ngày cào ở thời điểm khác; hotel ngoài mẫu; liệu dòng "1 khách" có tồn tại trong warehouse với tỷ lệ tương tự (không có cờ để đo).

## 9. Đính chính sau kiểm chứng độc lập (2026-09-24)

GPT đã tái lập toàn bộ số ở mục 1–8 (thread `discuss/canonical-key-duplicates/`, file 02) và Claude kiểm lại các điểm GPT nêu thêm (file 03; mục 9 của `analyze_minicrawl_html.py`). Các điều chỉnh sau **thay thế** câu tương ứng ở trên:

- **Mục 3, bảng theo định danh: "số khách của dòng giá khác trong 435 nhóm (77,8%)" bị thổi phồng.** Thành phần 3 của `data-block-id` bằng 0 ở 185/1.948 option và chỉ 31% trong đó có hậu tố gói, nên nó không luôn là số khách. Occupancy từng dòng thật sự khác: 399 nhóm (có dòng 1 khách) + 9 nhóm (chữ "Số người tối đa" khác nhau) = **408 nhóm (73,0%)**; thêm 6 nhóm Imperial không đối chiếu được (dòng giá không có ô occupancy) thì 414.
- **Mục 1: "36 dòng trùng tuyệt đối" là trùng theo khóa của scraper.** 33/36 giống hệt về mọi thứ nhìn thấy; 3/36 khác occupancy hiển thị (3 so với 2, Vinhomes) mà khóa dedupe (dùng `max_occupancy` cấp khối, `transform.py:88-99`) không thấy được.
- **Mục 1: "ghép 1-1 được 100%" không đồng nghĩa ứng viên duy nhất.** 164 option (14 item, 66 nhóm) có hơn một dòng DOM cùng (tên phòng, giá). Ghép theo thứ tự vẫn đúng vì thứ tự DB = thứ tự DOM; chỉ có rủi ro ở item có dòng bị bỏ xen kẽ (Vinhomes).
- **Mục 3: taxonomy 5 chiều thiếu chiều "occupancy khác 1".** 9 nhóm (+6 chưa kiểm được) đang xếp vào "tiện ích/gói" thực ra khác cả occupancy. Con số 98,9% nhóm được giải quyết ở mục 5 là cận dưới.
- **Mục 4: `max_occupancy` đã lưu** = max(sức chứa ghi trong tên phòng, occupancy dòng đầu của khối) (`parser.py:123-138`), tức lai giữa sức chứa vật lý và số hiển thị. Ở 8/18 hotel (41,4% option, gồm cả 5 CONTROL) dòng giá **không có ô occupancy**, nên không có chữ "Số người tối đa" và không có cảnh báo 1 khách.
- **"0/5 CONTROL có nhóm trùng"** bị nhiễu bởi mẫu trang (cả 5 CONTROL đều không có ô occupancy); không dùng làm bằng chứng về cấu trúc giá.
- **Phát hiện mới, ngoài phạm vi báo cáo này:** parser đọc "Không bao gồm bữa sáng" thành `breakfast_included=True` (46 option của Starview; `parser.py:35-37` và `:65-66`). Chi tiết ở file 03, mục 3.1.

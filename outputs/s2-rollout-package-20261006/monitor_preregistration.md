# G4 - monitor pre-registered cho regime `n1-parser-fix-1`

Dang ky TRUOC khi co du lieu sau rollout (khong chinh nguong sau khi thay ket qua).

**Muc tieu:** phat hien hotel NGOAI danh sach policy (exposure chua biet) bi doi `breakfast_included`/key sau ranh gioi, de quyet dinh them vao danh sach loai tru co version.

**So sanh** theo tung nguon, tung hotel: cua so truoc = 7 ngay crawl cuoi tren code cu; cua so sau = cac ngay crawl tren code moi (toi thieu 3 ngay, de co >= 3 run).
1. `share_breakfast_true` (tren option con gia, khong sold-out) truoc vs sau. Gan co neu |delta| >= 0.20 va moi cua so co >= 30 option.
2. `unavailable_rate` cua item match (moi `(hotel, checkin)` da co reference dong bang) truoc vs sau. Gan co neu tang >= 0.15 va moi cua so co >= 20 item.
3. Hotel nam trong policy: chi bao cao, khong tinh la phat hien moi.

**Xu ly:** hotel bi gan co -> bat buoc kiem chung bang HTML (artifact cua chinh hotel/check-in) hoac parse lai truoc khi them vao policy; association (breakfast hay unavailable doi) KHONG tu chung minh nguyen nhan. Moi thay doi danh sach/evidence -> policy_version moi + dataset_version moi. Khong bridge key, khong re-approve reference da dong bang tu dong.

**Khong lam:** khong sua RAW da nhap; khong recanonicalize tu raw bool cu; khong rollout tren may chua duoc nguoi van hanh xac nhan.

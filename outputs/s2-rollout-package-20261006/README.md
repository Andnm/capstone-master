# Goi rollout parser N1 (breakfast negation) - CHUA TRIEN KHAI

Trang thai: **chuan bi de review (GPT file 56 muc 5: G1-G4)**. Khong co gi duoc deploy, restart hay ket noi toi VPS/local phu. Quyet dinh thoi diem trien khai thuoc ve nguoi dung tren may cua ho.

## Noi dung
| File | Muc dich |
|---|---|
| `parser_n1.patch` | patch `backend/app/scraper/parser.py` (cau phu dinh khong con bi tinh la co bua sang); ap dung sach bang `patch -p3` tren ban sao, ket qua **byte-identical** voi `outputs/s2-patch-20261006/parser_patched.py` (file da kiem parity) |
| `scraper_version_bump.patch` | `SCRAPER_VERSION` 2.3.0 -> 2.3.1 de `crawl_runs.scraper_version` danh dau regime |
| `rollout_manifest.template.json` | manifest theo tung nguon (ranh gioi = run dau tien chay code moi) |
| `monitor_preregistration.md` | G4: monitor + nguong dang ky truoc |

## Bang chung G1 (patch regression) - tren BAN SAO cach ly cua backend
- Parity tren HTML con lai: 789 trang, 8.694 dong option, **59 dong doi o 10 trang**, chi `breakfast_included`, chi o dong co cau phu dinh, 0 vi pham (`outputs/s2-patch-20261006/parity_on_artifacts.out`).
- Backend test (`pytest tests`) tren ban sao goc: **425 passed, 24 skipped**; tren ban sao da vá + bump version: **425 passed, 24 skipped** (khong test nao doi ket qua).
- Unit test parser N1 (`outputs/s2-patch-20261006/test_parser_breakfast.py`): 19 passed.
- Anh huong len match (matcher that, snapshot quet 24-25/09, 1 ngay check-in): xem `ml/policies/n1/n1_matcher_replay.json` - 2/3 series co assignment rehearsal chuyen alias -> unavailable, 1 giu nguyen, 1 hotel khong co assignment, 1 hotel khong can chinh duoc HTML.
- Con thieu G1 day du: hoi quy scraper end-to-end (Selenium) tren trang that va regression option/key tren moi trang con lai ngoai nhom du kien (parity chi bao phu dieu kien parser). Can chay khi ops idle va cho GPT review truoc rollout.

## G2/G3/G4
- G2: `SCRAPER_VERSION` bump (co the bi ghi de bang bien moi truong `SCRAPER_VERSION` trong `.env` tren may thu thap - kiem tra truoc).
- G3: chi trien khai sau run terminal, 0 queued/running; dung worker cu va khoi dong worker moi (khong de mot run ghi hon hop code); dien manifest theo tung nguon.
- G4: `monitor_preregistration.md`.

## Rui ro da biet
- Sau rollout, series co option bi anh huong **co the** thanh `unavailable` hoac **chuyen sang option khac duoc chon** (alias): ket qua do TOAN BO offer + matcher quyet dinh, khong chi do rate key cu bi doi (snapshot quet: 2 series alias -> unavailable, 1 series giu nguyen selected alias). Thiet ke co chu y khong bridge. Hotel trong policy bi loai khoi dataset v1 qua moi regime.
- Hotel ngoai policy co exposure CHUA BIET; monitor G4 chi phat hien lien quan, khong chung minh nguyen nhan.

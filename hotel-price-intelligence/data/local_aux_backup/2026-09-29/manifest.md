# Local auxiliary backup crawl — 2026-09-29

- Source identity: `local_aux_backup`
- Host: `DESKTOP-TH607AP`
- Database: `hotel_price_intel`
- Export scope: core tables only (`hotels`, `crawl_runs`, `crawl_run_items`, `price_observations`)
- Full operational dump: not created, per user instruction
- Excel export: not requested
- Git branch/commit: unavailable in this working copy; crawl record also has `git_commit = NULL`

## Crawl run

- Run ID: `2`
- Status: `completed`
- Started (UTC): `2026-09-28T17:29:54Z`
- Finished (UTC): `2026-09-29T10:48:26Z`
- Started (Asia/Ho_Chi_Minh): `2026-09-29 00:29:54 +07:00`
- Finished (Asia/Ho_Chi_Minh): `2026-09-29 17:48:26 +07:00`
- Total items: `3540`
- Success: `3109`
- Partial: `0`
- Sold out: `321`
- Not bookable: `80`
- Error: `30`
- Stored price observations: `29923`
- Stored hotels: `351`

## Check-in dates

1. `2026-10-02`
2. `2026-10-05`
3. `2026-10-06`
4. `2026-10-13`
5. `2026-10-18`
6. `2026-10-19`
7. `2026-10-23`
8. `2026-10-26`
9. `2026-11-24`
10. `2027-02-04`

## Input files

- `link_hotel_data_expanded.xlsx`: `4A0AA5A39AD722BEC361BAE453C4DA90596077F4C7336ED5E2F8691C04CB9FE4`
- `aux_local_crawl_sampling_master.xlsx`: `A122BF2970471AE454AD448EE9EF222A13F1A05DD751C0BCB1D40BB7E3CE84DD`

## Output

- File: `local_aux_backup_core_2026-09-29.sql`
- Size: `21924640` bytes
- SHA-256: `00B37413F05C8DF26A8ED5C6B139FF9AA0674A54D6D37D0540AE6D62F1E02C82`
- Restore validation: passed against a temporary database; row counts matched the source

## Versions

- MySQL: `8.0.45`
- Python: `3.12.14`
- Node.js: `24.19.0`
- Microsoft Edge: `154.0.4258.37`
- Scraper: `2.3.0`
- Selector set: `booking-2026-08-17`

## Merge note

This dump is from an independent physical source. Do not restore it over the existing `local_aux` operational database. Import it through the project's merge/warehouse workflow so run, item, and observation identifiers can be reconciled safely.

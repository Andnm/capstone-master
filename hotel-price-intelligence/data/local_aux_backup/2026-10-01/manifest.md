# Local auxiliary backup crawl — 2026-10-01

- Source identity: `local_aux_backup`
- Host: `DESKTOP-TH607AP`
- Database: `hotel_price_intel`
- Export scope: core tables only (`hotels`, `crawl_runs`, `crawl_run_items`, `price_observations`)
- Snapshot contents: both completed runs `#2` and `#3`, preserving their relational IDs
- Full operational dump: not created
- Excel export: not requested
- Git branch: `sub-crawl-backup`
- Git author for the handoff snapshot: `Andnm <dangnguyenminhan123@gmail.com>`

## Crawl run for 2026-10-01

- Run ID: `3`
- Status: `completed`
- Started (UTC): `2026-09-30T18:30:28Z`
- Finished (UTC): `2026-10-01T11:38:47Z`
- Started (Asia/Ho_Chi_Minh): `2026-10-01 01:30:28 +07:00`
- Finished (Asia/Ho_Chi_Minh): `2026-10-01 18:38:47 +07:00`
- Total/processed items: `3540/3540`
- Success: `3093`
- Partial: `0`
- Sold out: `317`
- Not bookable: `90`
- Error: `40`
- Stored price observations for run `#3`: `29357`
- Hotels observed with stored prices in run `#3`: `341`

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

## Database snapshot counts

- `hotels`: `351`
- `crawl_runs`: `2`
- `crawl_run_items`: `7080`
- `price_observations`: `59280`

## Input files

- `link_hotel_data_expanded.xlsx`: `4A0AA5A39AD722BEC361BAE453C4DA90596077F4C7336ED5E2F8691C04CB9FE4`
- `aux_local_crawl_sampling_master.xlsx`: `A122BF2970471AE454AD448EE9EF222A13F1A05DD751C0BCB1D40BB7E3CE84DD`

## Output

- File: `local_aux_backup_core_2026-10-01.sql`
- Size: `43304778` bytes
- SHA-256: `79143A25269F924BBB9262A742DE7744FC079078BF72061DC9B2611A22FB9273`
- Restore validation: passed against temporary database `hotel_price_intel_verify_20261001`
- Restored counts: `351,2,7080,59280`, exactly matching the source
- Temporary validation database: deleted after validation

## Versions

- MySQL: `8.0.45`
- Python: `3.12.14`
- Node.js: `24.19.0`
- Microsoft Edge: `154.0.4258.37`
- Scraper: `2.3.0`
- Selector set: `booking-2026-08-17`

## Merge note

This dump is from an independent physical source. Do not restore it over an existing operational
`local_aux` database. Import it through the project's staging/merge or warehouse workflow so run,
item, and observation identifiers can be reconciled safely. Because this is a relational snapshot,
it contains both run `#2` and run `#3`; the receiving workflow must deduplicate the earlier run if it
was already imported from the 2026-09-29 handoff.

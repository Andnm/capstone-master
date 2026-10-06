"""Dataset builder (Phase 3B, WAREHOUSE_EDA_ML_SPEC.md muc 3b/11/12/14/15/17/18).

Dat NGOAI `GUARDED_PATHS` cua `app.warehouse.provenance` co chu dich: rebuild warehouse chinh thuc doi cac
duong do sach so voi HEAD, nen builder khong bao gio duoc sua/them file trong `backend/app/warehouse` va
cac file guard khac. Builder chi DOC code do (import) va chi GHI cac bang `dataset_build_manifests`/`ml_*`
cua mot `dataset_version` trong mot warehouse da PASS.
"""

BUILDER_VERSION = "dataset-builder-1.4.0"   # 1.1.0: split theo coverage that, primary horizon-specific, hop dong cot mot nguon su that
#                                             1.2.0: danh tinh ma ghim vao config (builder_code), verify truoc moi step + truoc mark_pass, gioi report tu chua
#                                             1.3.0: input lich (`vn_holidays.csv`) ghim hash vao config, snapshot bytes trong artifact + checksum
#                                             1.4.0: evaluation_horizons + purge >= max (build rieng theo horizon K), dataset_contract.json, official can primary_eligible

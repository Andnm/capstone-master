"""Dataset builder (Phase 3B, WAREHOUSE_EDA_ML_SPEC.md muc 3b/11/12/14/15/17/18).

Dat NGOAI `GUARDED_PATHS` cua `app.warehouse.provenance` co chu dich: rebuild warehouse chinh thuc doi cac
duong do sach so voi HEAD, nen builder khong bao gio duoc sua/them file trong `backend/app/warehouse` va
cac file guard khac. Builder chi DOC code do (import) va chi GHI cac bang `dataset_build_manifests`/`ml_*`
cua mot `dataset_version` trong mot warehouse da PASS.
"""

BUILDER_VERSION = "dataset-builder-1.0.0"

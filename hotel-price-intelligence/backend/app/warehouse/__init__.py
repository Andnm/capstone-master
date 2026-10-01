"""Warehouse build lifecycle (WAREHOUSE_EDA_ML_SPEC.md muc 3a).

Package nay CHI phuc vu warehouse build (1 lan/batch): snapshot -> import/remap 4 bang core ->
curated key -> full-history reference -> PASS -> promote. Dataset build lifecycle (muc 3b:
ml_reference_assignments/ml_item_reference_matches/ml_samples) la lifecycle RIENG, khong nam o day.

Nguyen tac xuyen suot:
- Moi DB nguon la READ-ONLY voi ETL. Package nay khong bao gio mo connection toi DB van hanh de ghi.
- FAIL CLOSED: moi bat thuong -> raise WarehouseError, khong tu doan/tu sua.
- Moi quyet dinh nam trong pure function test duoc khong can MySQL; script chi la CLI wrapper.
"""

import json
from app.core.database import get_db_connection

rid = 36
q = "SELECT id,status,total,processed,success_count,partial_count,sold_out_count,not_bookable_count,error_count,scraper_version,started_at,finished_at FROM crawl_runs WHERE id=%s"
qi = "SELECT COUNT(*) AS item_count, COALESCE(SUM(saved_options_count),0) AS valid_records FROM crawl_run_items WHERE crawl_run_id=%s"
with get_db_connection() as c:
    cur = c.cursor(dictionary=True)
    cur.execute(q, (rid,))
    run = cur.fetchone()
    cur.execute(qi, (rid,))
    items = cur.fetchone()
print(json.dumps({"run": run, "items": items}, default=str))

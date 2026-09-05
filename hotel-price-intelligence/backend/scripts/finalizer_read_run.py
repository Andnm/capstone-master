import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.database import get_db_connection
run_id=int(sys.argv[1])
with get_db_connection() as conn:
    cur=conn.cursor(dictionary=True)
    cur.execute('''SELECT id,status,total,processed,success_count,partial_count,sold_out_count,not_bookable_count,error_count,scraper_version,started_at,finished_at FROM crawl_runs WHERE id=%s''',(run_id,))
    run=cur.fetchone()
    cur.execute('SELECT COALESCE(SUM(saved_options_count),0) AS valid_records, COUNT(*) AS item_count FROM crawl_run_items WHERE crawl_run_id=%s',(run_id,))
    agg=cur.fetchone()
    print(json.dumps({'run':run,'agg':agg},default=str,ensure_ascii=False))
    conn.rollback()

from contextlib import contextmanager


def test_recompute_commits_terminal_aggregate_before_reference_refresh(monkeypatch):
    import app.database.durable as durable

    events = []

    class Cursor:
        def __init__(self):
            self.result = None

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split())
            events.append(("execute", normalized, params))
            if normalized.startswith("SELECT COUNT(*) total"):
                self.result = {
                    "total": 2,
                    "success_count": 2,
                    "partial_count": 0,
                    "sold_out_count": 0,
                    "not_bookable_count": 0,
                    "error_count": 0,
                    "processed": 2,
                }
            elif normalized.startswith("SELECT DISTINCT hotel_id,checkin_date"):
                self.result = [{"hotel_id": "hotel-1", "checkin_date": "2026-10-10"}]

        def fetchone(self):
            return self.result

        def fetchall(self):
            return self.result

        def close(self):
            events.append(("close",))

    class Connection:
        def __init__(self):
            self.commit_count = 0

        def cursor(self, dictionary=False):
            return Cursor()

        def commit(self):
            self.commit_count += 1
            events.append(("commit", self.commit_count))

        def rollback(self):
            events.append(("rollback",))

    connection = Connection()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(durable, "get_db_connection", fake_connection)

    class Repository(durable.DurableQueueRepository):
        def _refresh_reference(self, cursor, hotel_id, checkin_date, now):
            events.append(("refresh", connection.commit_count, hotel_id, checkin_date))

    Repository().recompute_run(34)

    assert ("refresh", 1, "hotel-1", "2026-10-10") in events
    assert connection.commit_count == 2
    assert not any(event[0] == "rollback" for event in events)

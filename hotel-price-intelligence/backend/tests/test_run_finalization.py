from app.database.durable import DurableQueueRepository


TERMINAL_COUNTS = {
    "total": 4248,
    "processed": 4248,
    "success_count": 3682,
    "partial_count": 0,
    "sold_out_count": 398,
    "not_bookable_count": 120,
    "error_count": 48,
}


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.pending = None

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.conn.executed.append((normalized, params))
        if "SELECT COUNT(*) total" in normalized:
            self.pending = dict(self.conn.counts)
        elif "SELECT DISTINCT hotel_id,checkin_date" in normalized:
            self.pending = list(self.conn.series)
        else:
            self.pending = None

    def fetchone(self):
        return self.pending

    def fetchall(self):
        return self.pending or []

    def close(self):
        pass


class _Conn:
    def __init__(self, *, counts=None, series=None, events=None):
        self.counts = counts or TERMINAL_COUNTS
        self.series = series or []
        self.events = events if events is not None else []
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, dictionary=False):
        return _Cursor(self)

    def commit(self):
        self.commits += 1
        self.events.append("aggregate_commit")

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_terminal_aggregate_commits_before_reference_refresh(monkeypatch):
    events = []
    conn = _Conn(events=events)
    monkeypatch.setattr("app.database.durable.get_db_connection", lambda: conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(
        repo,
        "_refresh_references_for_run",
        lambda run_id, now: events.append("reference_refresh"),
    )

    repo.recompute_run(61)

    assert events == ["aggregate_commit", "reference_refresh"]
    update_params = next(params for sql, params in conn.executed if sql.startswith("UPDATE crawl_runs"))
    assert update_params[1] == 4248
    assert update_params[7] == "completed"


def test_reference_refresh_failure_cannot_roll_back_completed_run(monkeypatch, capsys):
    conn = _Conn()
    monkeypatch.setattr("app.database.durable.get_db_connection", lambda: conn)
    repo = DurableQueueRepository()

    def fail_refresh(run_id, now):
        raise RuntimeError("maintenance failed")

    monkeypatch.setattr(repo, "_refresh_references_for_run", fail_refresh)

    repo.recompute_run(61)

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert "reference refresh deferred for run 61" in capsys.readouterr().out


def test_reconcile_repairs_only_when_every_item_is_terminal(monkeypatch):
    terminal_conn = _Conn()
    monkeypatch.setattr("app.database.durable.get_db_connection", lambda: terminal_conn)
    repo = DurableQueueRepository()
    calls = []
    monkeypatch.setattr(
        repo,
        "recompute_run",
        lambda run_id, refresh_references=True: calls.append((run_id, refresh_references)),
    )

    assert repo.reconcile_run_if_items_terminal(61) is True
    assert calls == [(61, False)]

    terminal_conn.counts = dict(TERMINAL_COUNTS, processed=4247)
    calls.clear()
    assert repo.reconcile_run_if_items_terminal(61) is False
    assert calls == []


def test_reference_refresh_pulses_watchdog_and_commits_in_batches(monkeypatch):
    series = [
        {"hotel_id": f"hotel-{index}", "checkin_date": "2026-10-04"}
        for index in range(26)
    ]
    connections = []

    def connection_factory():
        conn = _Conn(series=series if not connections else [])
        connections.append(conn)
        return conn

    monkeypatch.setattr("app.database.durable.get_db_connection", connection_factory)
    pulses = []
    refreshed = []
    repo = DurableQueueRepository(maintenance_heartbeat=lambda: pulses.append(True))
    monkeypatch.setattr(
        repo,
        "_refresh_reference",
        lambda cursor, hotel_id, checkin_date, now: refreshed.append((hotel_id, checkin_date)),
    )

    repo._refresh_references_for_run(61, "now")

    assert len(refreshed) == 26
    assert len(pulses) == 26
    assert [conn.commits for conn in connections] == [0, 1, 1]

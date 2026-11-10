"""scan_results.created_at is when the scan was stored, not when its transaction started (#827)."""
from datetime import datetime, timezone

from src.core.db import ScanResult
from src.repositories import scan_results_repo


def test_insert_stamps_created_at_at_insert_time_not_transaction_start(db):
    db.execute(ScanResult.__table__.select().limit(1))  # the transaction is already open before the insert
    before = datetime.now(timezone.utc)

    scan_results_repo.insert(db, "acme-created-at", 80, 1, 0, [])

    row = db.query(ScanResult).filter(ScanResult.owner == "acme-created-at").one()
    # Postgres now() (the old default) is the transaction start, which is earlier than `before`.
    assert row.created_at >= before

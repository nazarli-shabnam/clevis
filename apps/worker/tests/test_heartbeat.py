import time

from worker import _touch_heartbeat


def test_touch_heartbeat_writes_current_time(tmp_path, monkeypatch):
    heartbeat_file = tmp_path / "worker_heartbeat"
    monkeypatch.setattr("worker.HEARTBEAT_FILE", heartbeat_file)

    before = time.time()
    _touch_heartbeat()
    after = time.time()

    written = float(heartbeat_file.read_text())
    assert before <= written <= after


def test_touch_heartbeat_does_not_raise_if_the_path_is_unwritable(monkeypatch, tmp_path):
    # e.g. a read-only filesystem or a permissions issue -- the heartbeat is only a
    # liveness signal for the healthcheck, must never take down job processing itself.
    unwritable_dir = tmp_path / "does-not-exist" / "worker_heartbeat"
    monkeypatch.setattr("worker.HEARTBEAT_FILE", unwritable_dir)

    _touch_heartbeat()  # must not raise

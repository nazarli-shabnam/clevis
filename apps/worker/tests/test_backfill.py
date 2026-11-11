"""Tests for the install-time activity backfill."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import psycopg
import pytest

import backfill
import worker
from _crypto import encrypt_job_token
from config import settings

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


def test_summarize_all_five_tracked_types():
    assert backfill._summarize("push", {"size": 2, "ref": "refs/heads/main"}) == "pushed 2 commits to main"
    assert (
        backfill._summarize("pull_request", {"action": "opened", "number": 7, "pull_request": {"title": "Add widgets"}})
        == "opened PR #7: Add widgets"
    )
    assert (
        backfill._summarize(
            "pull_request", {"action": "closed", "number": 7, "pull_request": {"title": "Add widgets", "merged": True}}
        )
        == "merged PR #7: Add widgets"
    )
    assert backfill._summarize("issues", {"action": "opened", "issue": {"number": 3, "title": "Bug"}}) == "opened issue #3: Bug"
    assert backfill._summarize("release", {"release": {"tag_name": "v1.2.3"}}) == "created release v1.2.3"
    assert backfill._summarize("create", {"ref_type": "branch", "ref": "feature-x"}) == "created branch feature-x"


def test_summarize_unknown_type_returns_the_type_itself():
    assert backfill._summarize("deployment", {}) == "deployment"


def _raw_event(event_type="PushEvent", **overrides):
    event = {
        "id": "111",
        "type": event_type,
        "actor": {"login": "octocat", "avatar_url": "https://example.com/a.png"},
        "repo": {"name": "acme/widgets"},
        "payload": {"size": 1, "ref": "refs/heads/main"},
        "created_at": "2026-08-21T00:30:00Z",
    }
    event.update(overrides)
    return event


def test_normalize_maps_a_known_type():
    normalized = backfill.normalize(_raw_event())
    assert normalized["delivery_id"] == "backfill:111"
    assert normalized["event_type"] == "push"
    assert normalized["actor"] == "octocat"
    assert normalized["actor_avatar"] == "https://example.com/a.png"
    assert normalized["repo"] == "acme/widgets"
    assert normalized["summary"] == "pushed 1 commit to main"
    assert normalized["occurred_at"].isoformat() == "2026-08-21T00:30:00+00:00"


def test_normalize_skips_an_untracked_event_type():
    assert backfill.normalize(_raw_event(event_type="WatchEvent")) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},
        {"repo": {}},
        {"created_at": None},
        {"created_at": "not-a-timestamp"},
    ],
)
def test_normalize_returns_none_for_missing_or_malformed_fields(overrides):
    assert backfill.normalize(_raw_event(**overrides)) is None


class _FakeResponse:
    def __init__(self, json_data, links=None, status_code=200, headers=None, text=""):
        self._json_data = json_data
        self.links = links or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=self)

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


def test_fetch_events_follows_the_link_header():
    page1 = _FakeResponse([_raw_event(id="1")], links={"next": {"url": "https://api.github.com/orgs/acme/events?page=2"}})
    page2 = _FakeResponse([_raw_event(id="2")])
    client = _FakeClient([page1, page2])

    events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1", "2"]
    assert client.calls[0][0] == "https://api.github.com/orgs/acme/events"
    assert client.calls[1][0] == "https://api.github.com/orgs/acme/events?page=2"


def test_fetch_events_stops_at_the_page_cap():
    pages = [
        _FakeResponse([_raw_event(id=str(i))], links={"next": {"url": f"https://api.github.com/orgs/acme/events?page={i + 1}"}})
        for i in range(5)
    ]
    client = _FakeClient(pages)

    events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert len(events) == backfill._MAX_PAGES
    assert len(client.calls) == backfill._MAX_PAGES


def test_fetch_events_uses_the_user_events_path_for_personal_installs():
    client = _FakeClient([_FakeResponse([])])
    backfill.fetch_events(client, "https://api.github.com", {}, "octocat", "User")
    assert client.calls[0][0] == "https://api.github.com/users/octocat/events"


def test_fetch_events_retries_a_429_and_succeeds():
    rate_limited = _FakeResponse({}, status_code=429)
    success = _FakeResponse([_raw_event(id="1")])
    client = _FakeClient([rate_limited, success])

    with patch("backfill.time.sleep") as mock_sleep:
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]
    assert len(client.calls) == 2
    mock_sleep.assert_called_once()


def test_fetch_events_retries_a_secondary_rate_limit_403_with_retry_after():
    rate_limited = _FakeResponse({}, status_code=403, headers={"Retry-After": "5"})
    success = _FakeResponse([_raw_event(id="1")])
    client = _FakeClient([rate_limited, success])

    with patch("backfill.time.sleep") as mock_sleep:
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]
    mock_sleep.assert_called_once_with(5.0)


def test_fetch_events_retries_a_secondary_rate_limit_403_via_remaining_header():
    rate_limited = _FakeResponse({}, status_code=403, headers={"X-RateLimit-Remaining": "0"})
    success = _FakeResponse([_raw_event(id="1")])
    client = _FakeClient([rate_limited, success])

    with patch("backfill.time.sleep"):
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]


def test_fetch_events_retries_a_secondary_rate_limit_403_with_a_malformed_retry_after():
    # A non-numeric Retry-After is still a secondary rate limit (header presence counts),
    # so this waits the conservative cap, not a fast retry.
    rate_limited = _FakeResponse({}, status_code=403, headers={"Retry-After": "not-a-number"})
    success = _FakeResponse([_raw_event(id="1")])
    client = _FakeClient([rate_limited, success])

    with patch("backfill.time.sleep") as mock_sleep:
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]
    mock_sleep.assert_called_once_with(backfill._MAX_RETRY_AFTER_SECONDS)


def test_retry_delay_seconds_caps_a_large_retry_after():
    resp = _FakeResponse({}, status_code=429, headers={"Retry-After": "600"})
    assert backfill._retry_delay_seconds(resp, 0) == backfill._MAX_RETRY_AFTER_SECONDS


def test_retry_delay_seconds_uses_x_rate_limit_reset_when_retry_after_is_absent():
    resp = _FakeResponse({}, status_code=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000030"})
    with patch("backfill.time.time", return_value=1000000.0):
        assert backfill._retry_delay_seconds(resp, 0) == 30.0


def test_retry_delay_seconds_caps_a_far_future_x_rate_limit_reset():
    resp = _FakeResponse({}, status_code=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010000"})
    with patch("backfill.time.time", return_value=1000000.0):
        assert backfill._retry_delay_seconds(resp, 0) == backfill._MAX_RETRY_AFTER_SECONDS


def test_retry_delay_seconds_ignores_a_past_x_rate_limit_reset():
    # A reset time already in the past must not produce a negative sleep.
    resp = _FakeResponse({}, status_code=429, headers={"X-RateLimit-Reset": "999900"})
    with patch("backfill.time.time", return_value=1000000.0):
        assert backfill._retry_delay_seconds(resp, 0) == backfill._MAX_RETRY_AFTER_SECONDS


def test_retry_delay_seconds_falls_back_to_exponential_for_a_plain_5xx():
    resp = _FakeResponse({}, status_code=502)
    assert backfill._retry_delay_seconds(resp, 1) == 2


def test_fetch_events_does_not_retry_a_genuine_permission_403():
    forbidden = _FakeResponse({}, status_code=403)  # no Retry-After, no X-RateLimit-Remaining
    client = _FakeClient([forbidden])

    with patch("backfill.time.sleep") as mock_sleep:
        with pytest.raises(httpx.HTTPStatusError):
            backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert len(client.calls) == 1
    mock_sleep.assert_not_called()


def test_fetch_events_raises_after_exhausting_all_retries():
    responses = [_FakeResponse({}, status_code=429) for _ in range(3)]
    client = _FakeClient(responses)

    with patch("backfill.time.sleep"):
        with pytest.raises(httpx.HTTPStatusError):
            backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert len(client.calls) == 3


def test_fetch_events_retries_a_5xx():
    server_error = _FakeResponse({}, status_code=502)
    success = _FakeResponse([_raw_event(id="1")])
    client = _FakeClient([server_error, success])

    with patch("backfill.time.sleep"):
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]


def test_fetch_events_retries_a_connection_error_then_succeeds():
    success = _FakeResponse([_raw_event(id="1")])

    class _FlakyClient(_FakeClient):
        def get(self, url, headers=None, params=None):
            self.calls.append((url, params))
            if len(self.calls) == 1:
                raise httpx.RequestError("connection reset")
            return self._responses.pop(0)

    client = _FlakyClient([success])

    with patch("backfill.time.sleep"):
        events = backfill.fetch_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["1"]
    assert len(client.calls) == 2


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeConn:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self._cursor = _FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _payload(**kwargs):
    enc = encrypt_job_token("secret", settings.job_secret_key.get_secret_value())
    return json.dumps({"tenant_id": 1, "account_login": "acme", "account_type": "Organization", "token": enc, **kwargs})


def test_handler_marks_failed_on_invalid_payload():
    conn = _FakeConn()
    worker._handle_backfill_repo_events(conn, 1, "{}", 0)
    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql


def test_handler_marks_failed_on_undecryptable_token():
    conn = _FakeConn()
    bad_payload = json.dumps({"tenant_id": 1, "account_login": "acme", "account_type": "Organization", "token": "not-encrypted"})
    worker._handle_backfill_repo_events(conn, 1, bad_payload, 0)
    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql


def test_handler_marks_failed_on_4xx_from_github():
    conn = _FakeConn()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.side_effect = ValueError("not json")

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=mock_response)
        mock_client_cls.return_value = mock_client

        worker._handle_backfill_repo_events(conn, 2, _payload(), 0)

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert "GitHub API error" in params[0]


def test_handler_requeues_on_5xx_from_github():
    conn = _FakeConn()
    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_response.json.side_effect = ValueError("not json")

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=mock_response)
        mock_client_cls.return_value = mock_client

        worker._handle_backfill_repo_events(conn, 3, _payload(), 0)

    sql, params = conn._cursor.calls[0]
    assert "status='queued'" in sql  # requeued, not failed -- retry_count 0 -> 1, well under MAX_RETRIES


# status, headers, body, whether GitHub's answer is worth retrying later
_GITHUB_ERROR_CASES = [
    pytest.param(500, {}, "", True, id="5xx"),
    pytest.param(429, {}, "", True, id="429"),
    pytest.param(403, {"Retry-After": "30"}, "", True, id="403-retry-after"),
    pytest.param(403, {"X-RateLimit-Remaining": "0"}, "", True, id="403-primary-limit-exhausted"),
    pytest.param(
        403, {}, '{"message": "You have exceeded a secondary rate limit. Please wait."}', True,
        id="403-secondary-limit-message-only",
    ),
    pytest.param(
        403, {}, '{"message": "Resource not accessible by integration"}', False, id="403-permission-denied"
    ),
    pytest.param(401, {}, "", False, id="401"),
    pytest.param(404, {}, "", False, id="404"),
]


def _github_status_error(status_code: int, headers: dict, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/orgs/acme/events")
    response = httpx.Response(status_code, headers=headers, text=body, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize("status_code, headers, body, transient", _GITHUB_ERROR_CASES)
def test_transient_github_error_classifier(status_code, headers, body, transient):
    error = _github_status_error(status_code, headers, body)
    assert worker._is_transient_github_error(error.response) is transient


@pytest.mark.parametrize("status_code, headers, body, transient", _GITHUB_ERROR_CASES)
def test_handler_requeues_only_transient_github_errors(status_code, headers, body, transient):
    """A rate limit that outlasts fetch_events' own retries must be retried later, not failed for good."""
    conn = _FakeConn()
    error = _github_status_error(status_code, headers, body)

    with patch("worker.backfill.fetch_all_events", side_effect=error):
        worker._handle_backfill_repo_events(conn, 6, _payload(), 0)

    sql, _params = conn._cursor.calls[0]
    assert ("status='queued'" in sql) is transient
    assert ("status='failed'" in sql) is (not transient)


def test_handler_fails_a_rate_limited_job_for_good_once_its_retries_are_spent():
    conn = _FakeConn()
    error = _github_status_error(429, {}, "")

    with patch("worker.backfill.fetch_all_events", side_effect=error):
        worker._handle_backfill_repo_events(conn, 7, _payload(), worker.MAX_RETRIES)

    sql, _params = conn._cursor.calls[0]
    assert "status='failed'" in sql


def test_handler_requeues_on_network_error():
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=httpx.RequestError("connection reset"))
        mock_client_cls.return_value = mock_client

        worker._handle_backfill_repo_events(conn, 4, _payload(), 0)

    sql, params = conn._cursor.calls[0]
    assert "status='queued'" in sql


class _FailingCursor(_FakeCursor):
    """Raises on the first execute() (SET app.tenant_id) to simulate a DB error mid-loop;
    later calls succeed, as they would after rollback()."""

    has_failed = False

    def execute(self, sql, params=None):
        if not self.has_failed:
            self.has_failed = True
            raise psycopg.OperationalError("simulated database failure")
        super().execute(sql, params)


class _FailingConn(_FakeConn):
    def __init__(self):
        super().__init__()
        self._cursor = _FailingCursor()


def test_handler_rolls_back_and_requeues_on_a_db_error_during_the_insert_loop():
    # Without a rollback, the follow-up UPDATE would hit InFailedSqlTransaction.
    conn = _FailingConn()
    events = [_raw_event(id="b-db-error-1")]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(conn, 5, _payload(), 0)

    assert conn.rolled_back is True
    sql, _params = conn._cursor.calls[0]
    assert "status='queued'" in sql  # requeued, not stuck in 'processing'
    # The failure is on the first execute(), so the cursor upsert never runs.
    assert not any("activity_sync_cursors" in sql for sql, _params in conn._cursor.calls)


@pytest.fixture()
def pg_conn():
    conn = psycopg.connect(_DB_URL, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def tenant_id(pg_conn):
    email = "s5-backfill-tests@example.com"
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))
        row = cur.fetchone()
        user_id = row[0] if row else None
        if user_id is None:
            cur.execute("INSERT INTO users (email) VALUES (%s) RETURNING id", (email,))
            user_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM tenants WHERE personal_user_id = %s", (user_id,))
        row = cur.fetchone()
        result = row[0] if row else None
        if result is None:
            cur.execute("INSERT INTO tenants (kind, personal_user_id) VALUES ('personal', %s) RETURNING id", (user_id,))
            result = cur.fetchone()[0]
    pg_conn.commit()
    return result


def _mock_github_client(events: list[dict]):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.links = {}
    mock_response.json = MagicMock(return_value=events)
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_response)
    return mock_client


def _repo_events_count(conn, tenant_id, repo):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute("SELECT count(*) FROM repo_events WHERE repo = %s AND tenant_id = %s", (repo, tenant_id))
        return cur.fetchone()[0]


def _daily_count(conn, tenant_id, repo, event_type, day):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute(
            "SELECT count FROM repo_event_daily_counts WHERE tenant_id = %s AND repo = %s AND event_type = %s AND day = %s",
            (tenant_id, repo, event_type, day),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _payload_for(tenant_id: int, account_login: str = "acme", account_type: str = "Organization") -> str:
    enc = encrypt_job_token("secret", settings.job_secret_key.get_secret_value())
    return json.dumps({"tenant_id": tenant_id, "account_login": account_login, "account_type": account_type, "token": enc})


def test_handler_inserts_events_and_daily_counts(pg_conn, tenant_id):
    repo = "acme/backfill-fresh"
    events = [
        _raw_event(id="b-fresh-1", event_type="PushEvent", repo={"name": repo}),
        _raw_event(id="b-fresh-2", event_type="IssuesEvent", payload={"action": "opened", "issue": {"number": 1, "title": "Bug"}}, repo={"name": repo}),
    ]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 100, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 2
    assert _daily_count(pg_conn, tenant_id, repo, "push", date(2026, 8, 21)) == 1
    assert _daily_count(pg_conn, tenant_id, repo, "issues", date(2026, 8, 21)) == 1


def _insert_webhook_event(conn, tenant_id, repo, event_type, actor, occurred_at):
    """A row as event_consumer stores it: GitHub's delivery GUID, stamped with our receive time."""
    from repo_events_store import insert_event_and_upsert_daily_count

    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        insert_event_and_upsert_daily_count(
            cur, tenant_id=tenant_id, delivery_id=str(uuid.uuid4()), event_type=event_type, actor=actor,
            actor_avatar="", repo=repo, summary="from webhook", occurred_at=occurred_at,
        )
    conn.commit()


@pytest.fixture()
def other_tenant_id(pg_conn):
    email = "s5-backfill-tests-other@example.com"
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO users (email) VALUES (%s) RETURNING id", (email,))
            user_id = cur.fetchone()[0]
        else:
            user_id = row[0]
        cur.execute("SELECT id FROM tenants WHERE personal_user_id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO tenants (kind, personal_user_id) VALUES ('personal', %s) RETURNING id", (user_id,))
            row = cur.fetchone()
    pg_conn.commit()
    return row[0]


def test_the_same_github_event_is_stored_for_each_tenant_that_backfills_it(pg_conn, tenant_id, other_tenant_id):
    # GitHub event ids are global: a member's push to an org repo is in both their personal feed and the
    # org's feed. The second tenant to backfill must still get its own row and daily count (#764).
    repo = f"acme/backfill-xtenant-{uuid.uuid4().hex[:8]}"
    events = [_raw_event(id=f"b-xtenant-{repo[-8:]}", event_type="PushEvent", repo={"name": repo})]

    for job_id, tenant in ((130, tenant_id), (131, other_tenant_id)):
        with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
            worker._handle_backfill_repo_events(pg_conn, job_id, _payload_for(tenant), 0)

    for tenant in (tenant_id, other_tenant_id):
        assert _repo_events_count(pg_conn, tenant, repo) == 1
        assert _daily_count(pg_conn, tenant, repo, "push", date(2026, 8, 21)) == 1


def test_rerunning_a_backfill_for_the_same_tenant_still_dedupes(pg_conn, tenant_id):
    repo = f"acme/backfill-rerun-{uuid.uuid4().hex[:8]}"
    events = [_raw_event(id=f"b-rerun-{repo[-8:]}", event_type="PushEvent", repo={"name": repo})]

    for job_id in (132, 133):
        with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
            worker._handle_backfill_repo_events(pg_conn, job_id, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 1
    assert _daily_count(pg_conn, tenant_id, repo, "push", date(2026, 8, 21)) == 1


def test_handler_skips_an_event_the_webhook_path_already_stored(pg_conn, tenant_id):
    # The same push arrives by webhook (stamped when we received it) and in the Events API; it must count once (#542).
    repo = f"acme/backfill-twin-{uuid.uuid4().hex[:8]}"  # unique per run: rows persist
    github_created = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    _insert_webhook_event(pg_conn, tenant_id, repo, "push", "octocat", github_created + timedelta(seconds=20))
    events = [_raw_event(id=f"b-twin-1-{repo[-8:]}", event_type="PushEvent", repo={"name": repo})]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 120, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 1
    assert _daily_count(pg_conn, tenant_id, repo, "push", date(2026, 8, 21)) == 1


def test_handler_still_recovers_an_event_the_webhook_path_missed(pg_conn, tenant_id):
    # Gap-heal's purpose: a webhook outage leaves holes that the Events API fills. Different actor and
    # a far-away timestamp are not twins.
    repo = f"acme/backfill-gap-{uuid.uuid4().hex[:8]}"  # unique per run: rows persist
    _insert_webhook_event(pg_conn, tenant_id, repo, "push", "someone-else", datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc))
    _insert_webhook_event(pg_conn, tenant_id, repo, "push", "octocat", datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc))
    events = [_raw_event(id=f"b-gap-1-{repo[-8:]}", event_type="PushEvent", repo={"name": repo})]  # octocat at 00:30

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 121, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 3
    assert _daily_count(pg_conn, tenant_id, repo, "push", date(2026, 8, 21)) == 3


def test_one_webhook_row_hides_only_one_of_two_close_backfilled_events(pg_conn, tenant_id):
    repo = f"acme/backfill-pair-{uuid.uuid4().hex[:8]}"  # unique per run: rows persist
    _insert_webhook_event(pg_conn, tenant_id, repo, "push", "octocat", datetime(2026, 8, 21, 0, 30, 10, tzinfo=timezone.utc))
    events = [
        _raw_event(id=f"b-pair-1-{repo[-8:]}", event_type="PushEvent", repo={"name": repo}),
        _raw_event(id=f"b-pair-2-{repo[-8:]}", event_type="PushEvent", repo={"name": repo}, created_at="2026-08-21T00:30:40Z"),
    ]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 122, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 2  # the webhook row + one backfilled
    assert _daily_count(pg_conn, tenant_id, repo, "push", date(2026, 8, 21)) == 2


def test_handler_skips_untracked_event_types(pg_conn, tenant_id):
    repo = "acme/backfill-skip"
    events = [_raw_event(id="b-skip-1", event_type="WatchEvent", repo={"name": repo})]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 101, _payload_for(tenant_id), 0)

    assert _repo_events_count(pg_conn, tenant_id, repo) == 0


def test_handler_rerun_is_idempotent(pg_conn, tenant_id):
    repo = "acme/backfill-dedupe"
    events = [_raw_event(id="b-dedupe-1", event_type="CreateEvent", payload={"ref_type": "tag", "ref": "v1"}, repo={"name": repo})]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 102, _payload_for(tenant_id), 0)
        worker._handle_backfill_repo_events(pg_conn, 103, _payload_for(tenant_id), 0)  # simulates a retried/re-triggered job

    assert _repo_events_count(pg_conn, tenant_id, repo) == 1
    assert _daily_count(pg_conn, tenant_id, repo, "create", date(2026, 8, 21)) == 1


def test_handler_uses_the_user_events_path_for_personal_installs(pg_conn, tenant_id):
    repo = "octocat/personal-repo"
    events = [_raw_event(id="b-personal-1", event_type="PushEvent", repo={"name": repo})]
    mock_client = _mock_github_client(events)

    with patch("worker.httpx.Client", return_value=mock_client):
        worker._handle_backfill_repo_events(pg_conn, 104, _payload_for(tenant_id, account_login="octocat", account_type="User"), 0)

    called_url = mock_client.get.call_args_list[0][0][0]
    assert "/users/octocat/events" in called_url
    assert _repo_events_count(pg_conn, tenant_id, repo) == 1


def _sync_cursor(conn, tenant_id):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute(
            "SELECT account_login, account_type, last_synced_at FROM activity_sync_cursors WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cur.fetchone()


def test_handler_upserts_the_sync_cursor_on_success(pg_conn, tenant_id):
    repo = "acme/cursor-fresh"
    events = [_raw_event(id="b-cursor-1", event_type="PushEvent", repo={"name": repo})]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 105, _payload_for(tenant_id), 0)

    row = _sync_cursor(pg_conn, tenant_id)
    assert row is not None
    account_login, account_type, last_synced_at = row
    assert account_login == "acme"
    assert account_type == "Organization"
    assert last_synced_at is not None


def test_handler_cursor_upsert_advances_last_synced_at_on_rerun(pg_conn, tenant_id):
    repo = "acme/cursor-rerun"
    events = [_raw_event(id="b-cursor-2", event_type="PushEvent", repo={"name": repo})]

    with patch("worker.httpx.Client", return_value=_mock_github_client(events)):
        worker._handle_backfill_repo_events(pg_conn, 106, _payload_for(tenant_id), 0)
        first_synced_at = _sync_cursor(pg_conn, tenant_id)[2]

        worker._handle_backfill_repo_events(pg_conn, 107, _payload_for(tenant_id), 0)
        second_synced_at = _sync_cursor(pg_conn, tenant_id)[2]

    assert second_synced_at >= first_synced_at


# --- private repositories (#806): the org/user feed is public-only ---

def _repo(full_name, private=True, pushed_at="2026-08-20T00:00:00Z"):
    return {"full_name": full_name, "private": private, "pushed_at": pushed_at}


def test_fetch_private_repo_events_reads_each_private_repo_newest_first():
    client = _FakeClient([
        _FakeResponse([_repo("acme/old", pushed_at="2026-01-01T00:00:00Z"), _repo("acme/pub", private=False), _repo("acme/new")]),
        _FakeResponse([_raw_event(id="n1")]),  # acme/new (pushed most recently)
        _FakeResponse([_raw_event(id="o1")]),  # acme/old
    ])

    events = backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["n1", "o1"]
    assert client.calls[0][0] == "https://api.github.com/orgs/acme/repos"
    assert client.calls[0][1]["type"] == "private"
    assert [c[0] for c in client.calls[1:]] == [
        "https://api.github.com/repos/acme/new/events",
        "https://api.github.com/repos/acme/old/events",
    ]


def test_fetch_private_repo_events_uses_installation_repositories_for_personal_installs():
    client = _FakeClient([_FakeResponse({"repositories": [_repo("octocat/secret")]}), _FakeResponse([_raw_event(id="p1")])])

    events = backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "octocat", "User")

    assert [e["id"] for e in events] == ["p1"]
    assert client.calls[0][0] == "https://api.github.com/installation/repositories"


def test_a_secondary_rate_limit_signalled_only_in_the_body_is_not_skipped_as_a_forbidden_repo():
    """GitHub also sends the secondary limit as a 403 with no Retry-After / X-RateLimit-Remaining header; only
    its message tells it from a permission refusal. Skipping it would drop that repo's events silently and
    still advance the sync cursor, so it must propagate (the job is then requeued)."""
    limited = _FakeResponse({}, status_code=403, text="You have exceeded a secondary rate limit. Please wait.")
    client = _FakeClient([_FakeResponse([_repo("acme/a")]), limited])

    with pytest.raises(httpx.HTTPStatusError):
        backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization")


def test_a_genuine_permission_403_is_still_skipped_when_its_message_is_unrelated():
    denied = _FakeResponse({}, status_code=403, text="Resource not accessible by integration")
    client = _FakeClient([_FakeResponse([_repo("acme/a")]), denied])

    assert backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization") == []


def test_a_personal_install_reads_every_repo_page_before_picking_the_newest_private_ones():
    page1 = _FakeResponse(
        {"repositories": [_repo("octocat/old", pushed_at="2026-01-01T00:00:00Z")]},
        links={"next": {"url": "https://api.github.com/installation/repositories?page=2"}},
    )
    page2 = _FakeResponse({"repositories": [_repo("octocat/newest", pushed_at="2026-08-30T00:00:00Z")]})
    client = _FakeClient([page1, page2, _FakeResponse([_raw_event(id="n1")]), _FakeResponse([_raw_event(id="o1")])])

    events = backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "octocat", "User")

    assert [e["id"] for e in events] == ["n1", "o1"]  # the repo on page 2 was seen, and sorts first
    assert [c[0] for c in client.calls[2:]] == [
        "https://api.github.com/repos/octocat/newest/events",
        "https://api.github.com/repos/octocat/old/events",
    ]


def test_fetch_private_repo_events_caps_the_number_of_repos():
    repos = [_repo(f"acme/r{i}", pushed_at=f"2026-08-{i % 28 + 1:02d}T00:00:00Z") for i in range(backfill._MAX_PRIVATE_REPOS + 10)]
    client = _FakeClient([_FakeResponse(repos)] + [_FakeResponse([]) for _ in repos])

    backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert len(client.calls) == 1 + backfill._MAX_PRIVATE_REPOS


def test_fetch_private_repo_events_skips_a_repo_the_token_cannot_read():
    client = _FakeClient([
        _FakeResponse([_repo("acme/a"), _repo("acme/b", pushed_at="2026-07-01T00:00:00Z")]),
        _FakeResponse({}, status_code=404),
        _FakeResponse([_raw_event(id="b1")]),
    ])

    events = backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert [e["id"] for e in events] == ["b1"]


def test_fetch_private_repo_events_returns_nothing_when_the_repo_list_is_forbidden():
    client = _FakeClient([_FakeResponse({}, status_code=403)])
    assert backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization") == []


def test_fetch_private_repo_events_propagates_a_server_error_so_the_job_is_retried():
    client = _FakeClient([_FakeResponse({}, status_code=502)] * 3)
    with patch("backfill.time.sleep"), pytest.raises(httpx.HTTPStatusError):
        backfill.fetch_private_repo_events(client, "https://api.github.com", {}, "acme", "Organization")


def test_fetch_all_events_merges_public_and_private_without_duplicates():
    client = _FakeClient([
        _FakeResponse([_raw_event(id="pub1"), _raw_event(id="dup")]),  # public feed
        _FakeResponse([_repo("acme/secret")]),
        _FakeResponse([_raw_event(id="dup"), _raw_event(id="priv1")]),
    ])

    events = backfill.fetch_all_events(client, "https://api.github.com", {}, "acme", "Organization")

    assert sorted(e["id"] for e in events) == ["dup", "priv1", "pub1"]

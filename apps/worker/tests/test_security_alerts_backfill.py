"""Backfill of already-open Dependabot / code scanning alerts, and the per-(repo, kind) completeness record (#814)."""

import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
import psycopg
import pytest

import security_alerts_backfill as sab
import worker
from _crypto import encrypt_job_token
from config import settings

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")
BASE = "https://api.github.com"


class _Resp:
    def __init__(self, body, status_code=200, links=None, headers=None):
        self._body, self.status_code, self.links, self.headers, self.text = body, status_code, links or {}, headers or {}, ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=self)


class _Client:
    """Routes GETs by URL substring to a response or a list of responses (consumed in order)."""

    def __init__(self, routes):
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append(url)
        for fragment, queue in self.routes.items():
            if fragment in url:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"no route for {url}")


def _alert(number, severity="critical", **extra):
    return {
        "number": number,
        "state": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "security_advisory": {"severity": severity},
        "rule": {"severity": severity},
        **extra,
    }


# --- fetching ---

def test_fetch_open_alerts_follows_pages_and_treats_404_as_no_alerts():
    client = _Client({
        "acme/api/dependabot/alerts": [
            _Resp([_alert(1)], links={"next": {"url": f"{BASE}/repos/acme/api/dependabot/alerts?page=2"}}),
            _Resp([_alert(2)]),
        ],
        "acme/off/dependabot/alerts": _Resp({}, status_code=404),
    })

    assert [a["number"] for a in sab.fetch_open_alerts(client, BASE, {}, "acme/api", "dependabot")] == [1, 2]
    assert sab.fetch_open_alerts(client, BASE, {}, "acme/off", "dependabot") == []


def test_fetch_open_alerts_raises_for_other_failures_and_past_the_page_cap():
    forbidden = _Client({"dependabot/alerts": _Resp({}, status_code=403)})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        sab.fetch_open_alerts(forbidden, BASE, {}, "acme/api", "dependabot")
    assert sab.is_unreadable(exc.value)

    server_error = _Client({"dependabot/alerts": _Resp({}, status_code=502)})
    with patch("backfill.time.sleep"), pytest.raises(httpx.HTTPStatusError) as exc:
        sab.fetch_open_alerts(server_error, BASE, {}, "acme/api", "dependabot")
    assert not sab.is_unreadable(exc.value)

    endless = _Client({"dependabot/alerts": _Resp([_alert(1)], links={"next": {"url": f"{BASE}/repos/acme/api/dependabot/alerts?page=2"}})})
    with pytest.raises(sab.IncompleteListing):
        sab.fetch_open_alerts(endless, BASE, {}, "acme/api", "dependabot")


def test_a_rate_limit_is_not_treated_as_unreadable():
    limited = httpx.HTTPStatusError("x", request=MagicMock(), response=_Resp({}, status_code=429))
    secondary = httpx.HTTPStatusError("x", request=MagicMock(), response=_Resp({}, status_code=403, headers={"Retry-After": "5"}))
    assert not sab.is_unreadable(limited) and not sab.is_unreadable(secondary)


def test_list_repos_for_an_org_and_a_personal_install():
    org_client = _Client({"/orgs/acme/repos": _Resp([{"full_name": "acme/api"}, {"full_name": "acme/web"}])})
    assert sab.list_repos(org_client, BASE, {}, "acme", "Organization") == ["acme/api", "acme/web"]

    user_client = _Client({"/installation/repositories": _Resp({"repositories": [{"full_name": "octo/dots"}]})})
    assert sab.list_repos(user_client, BASE, {}, "octo", "User") == ["octo/dots"]


# --- the job ---

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
    """A fresh tenant per test, so no cleanup is needed (the restricted role these tests run as in CI cannot
    DELETE from security_alerts or security_alert_syncs)."""
    email = f"s814-alert-backfill-{uuid.uuid4().hex}@example.com"
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO users (email) VALUES (%s) RETURNING id", (email,))
        user_id = cur.fetchone()[0]
        cur.execute("INSERT INTO tenants (kind, personal_user_id) VALUES ('personal', %s) RETURNING id", (user_id,))
        result = cur.fetchone()[0]
    pg_conn.commit()
    return result


def _payload(tenant_id, account_type="Organization", token="ghs_secret"):
    enc = encrypt_job_token(token, settings.job_secret_key.get_secret_value())
    return json.dumps({"tenant_id": tenant_id, "account_login": "acme", "account_type": account_type, "token": enc})


def _run(pg_conn, client, tenant_id, retry_count=0):
    with patch("worker.httpx.Client") as cls, patch("backfill.time.sleep"):
        cls.return_value.__enter__ = MagicMock(return_value=client)
        cls.return_value.__exit__ = MagicMock(return_value=False)
        worker._handle_backfill_security_alerts(pg_conn, 4242, _payload(tenant_id), retry_count)


def _rows(conn, tenant_id):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute("SELECT repo, kind, number, severity, alert_notified_at FROM security_alerts WHERE tenant_id = %s ORDER BY repo, kind, number", (tenant_id,))
        alerts = cur.fetchall()
        cur.execute("SELECT repo, kind FROM security_alert_syncs WHERE tenant_id = %s ORDER BY repo, kind", (tenant_id,))
        return alerts, cur.fetchall()


def _routes(**over):
    routes = {
        "/orgs/acme/repos": _Resp([{"full_name": "acme/api"}, {"full_name": "acme/locked"}]),
        "acme/api/dependabot/alerts": _Resp([_alert(1), _alert(2, "low")]),
        "acme/api/code-scanning/alerts": _Resp({}, status_code=404),  # code scanning is off for this repo
        "acme/locked/dependabot/alerts": _Resp({}, status_code=403),  # the token cannot read these
        "acme/locked/code-scanning/alerts": _Resp([_alert(7, "error")]),
    }
    routes.update(over)
    return routes


def test_job_stores_open_alerts_and_records_only_the_lists_it_read_in_full(pg_conn, tenant_id):
    _run(pg_conn, _Client(_routes()), tenant_id)

    alerts, synced = _rows(pg_conn, tenant_id)
    assert [(r, k, n) for r, k, n, _s, _a in alerts] == [
        ("acme/api", "dependabot", 1), ("acme/api", "dependabot", 2), ("acme/locked", "code_scanning", 7),
    ]
    # api: dependabot read, code scanning 404 (off => complete with zero alerts); locked: dependabot 403 => NOT recorded
    assert synced == [("acme/api", "code_scanning"), ("acme/api", "dependabot"), ("acme/locked", "code_scanning")]


def test_backfilled_alerts_are_marked_announced_so_the_alert_sweep_does_not_flood_destinations(pg_conn, tenant_id):
    _run(pg_conn, _Client(_routes()), tenant_id)

    alerts, _ = _rows(pg_conn, tenant_id)
    assert all(announced is not None for *_rest, announced in alerts)


def test_an_alert_created_within_the_last_hour_is_left_for_the_alert_sweep_to_announce(pg_conn, tenant_id):
    from datetime import datetime, timedelta, timezone

    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    routes = _routes(**{"acme/api/dependabot/alerts": _Resp([_alert(1, created_at=fresh), _alert(2)])})

    _run(pg_conn, _Client(routes), tenant_id)

    announced = {n: a for repo, kind, n, _s, a in _rows(pg_conn, tenant_id)[0] if (repo, kind) == ("acme/api", "dependabot")}
    assert announced[1] is None  # created minutes ago: its webhook may not have been processed yet
    assert announced[2] is not None  # long-standing: not "new"


def _insert_open_alert(conn, tenant_id, number, ingested_offset_sql):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute(
            "INSERT INTO security_alerts (tenant_id, repo, kind, number, state, severity, created_at, updated_at, ingested_at) "
            f"VALUES (%s, 'acme/api', 'dependabot', %s, 'open', 'critical', now(), now(), {ingested_offset_sql})",
            (tenant_id, number),
        )
    conn.commit()


def _state(conn, tenant_id, number):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute("SELECT state FROM security_alerts WHERE tenant_id = %s AND repo = 'acme/api' AND kind = 'dependabot' AND number = %s", (tenant_id, number))
        return cur.fetchone()[0]


def test_a_complete_listing_closes_stored_open_alerts_it_no_longer_contains(pg_conn, tenant_id):
    _insert_open_alert(pg_conn, tenant_id, 99, "now() - interval '1 day'")  # stored long ago; its close webhook was missed
    _insert_open_alert(pg_conn, tenant_id, 98, "now() + interval '1 hour'")  # stored after the listing began

    _run(pg_conn, _Client(_routes()), tenant_id)  # api/dependabot lists only #1 and #2

    assert _state(pg_conn, tenant_id, 99) == "fixed"
    assert _state(pg_conn, tenant_id, 98) == "open"  # newer than what was read: left alone
    assert _state(pg_conn, tenant_id, 1) == "open"  # still listed


def _insert_alert_in(conn, tenant_id, repo, number):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute(
            "INSERT INTO security_alerts (tenant_id, repo, kind, number, state, severity, created_at, updated_at, ingested_at) "
            "VALUES (%s, %s, 'dependabot', %s, 'open', 'critical', now(), now(), now() - interval '1 day')",
            (tenant_id, repo, number),
        )
    conn.commit()


def _state_in(conn, tenant_id, repo, number):
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        cur.execute("SELECT state FROM security_alerts WHERE tenant_id = %s AND repo = %s AND number = %s", (tenant_id, repo, number))
        return cur.fetchone()[0]


def test_open_alerts_of_a_repo_that_left_the_listing_are_closed_but_other_accounts_are_not(pg_conn, tenant_id):
    _insert_alert_in(pg_conn, tenant_id, "acme/old-name", 5)  # renamed away: no longer listed
    _insert_alert_in(pg_conn, tenant_id, "other/elsewhere", 6)  # another account's repo on the same tenant

    _run(pg_conn, _Client(_routes()), tenant_id)

    assert _state_in(pg_conn, tenant_id, "acme/old-name", 5) == "fixed"
    assert _state_in(pg_conn, tenant_id, "other/elsewhere", 6) == "open"
    assert _state_in(pg_conn, tenant_id, "acme/api", 1) == "open"  # still listed


def test_a_listing_read_with_a_pasted_token_closes_nothing(pg_conn, tenant_id):
    _insert_alert_in(pg_conn, tenant_id, "acme/old-name", 5)  # a PAT may simply not see this repo
    with patch("worker.httpx.Client") as cls, patch("backfill.time.sleep"):
        cls.return_value.__enter__ = MagicMock(return_value=_Client(_routes()))
        cls.return_value.__exit__ = MagicMock(return_value=False)
        worker._handle_backfill_security_alerts(pg_conn, 4243, _payload(tenant_id, token="ghp_pat"), 0)

    assert _state_in(pg_conn, tenant_id, "acme/old-name", 5) == "open"


def test_a_repo_listing_cut_off_at_the_cap_closes_nothing(pg_conn, tenant_id, monkeypatch):
    _insert_alert_in(pg_conn, tenant_id, "acme/old-name", 5)
    monkeypatch.setattr(sab, "_MAX_REPOS", 2)  # the two listed repos fill the cap, so more may exist

    _run(pg_conn, _Client(_routes()), tenant_id)

    assert _state_in(pg_conn, tenant_id, "acme/old-name", 5) == "open"


def test_a_list_that_could_not_be_read_closes_nothing(pg_conn, tenant_id):
    _insert_open_alert(pg_conn, tenant_id, 99, "now() - interval '1 day'")
    routes = _routes(**{"acme/api/dependabot/alerts": _Resp({}, status_code=403)})  # unreadable: not a complete listing

    _run(pg_conn, _Client(routes), tenant_id)

    assert _state(pg_conn, tenant_id, 99) == "open"


def test_rerunning_is_idempotent_and_leaves_an_existing_alerts_announced_state_alone(pg_conn, tenant_id):
    _run(pg_conn, _Client(_routes()), tenant_id)
    first, _ = _rows(pg_conn, tenant_id)
    with pg_conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        # a webhook-ingested alert that was never announced must stay un-announced when the backfill sees it
        cur.execute("UPDATE security_alerts SET alert_notified_at = NULL WHERE tenant_id = %s AND number = 2", (tenant_id,))
    pg_conn.commit()

    _run(pg_conn, _Client(_routes()), tenant_id)

    second, synced = _rows(pg_conn, tenant_id)
    assert len(second) == len(first) == 3 and len(synced) == 3
    assert [a for *_r, a in second if a is None] != []  # number 2 kept its NULL: only newly inserted rows are stamped


def test_nothing_is_recorded_when_a_rate_limit_interrupts_the_run(pg_conn, tenant_id):
    routes = _routes(**{"acme/api/dependabot/alerts": _Resp({}, status_code=429)})

    _run(pg_conn, _Client(routes), tenant_id)

    alerts, synced = _rows(pg_conn, tenant_id)
    assert alerts == [] and synced == []  # the job is requeued whole; no partial completeness claim

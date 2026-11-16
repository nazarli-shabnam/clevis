"""worker._job_token: a fresh installation token per attempt, falling back to the payload's token."""

import json
import time
from unittest.mock import MagicMock, patch

import httpx
from pydantic import SecretStr

import worker


def _settings(token):
    return patch.object(
        worker,
        "settings",
        MagicMock(job_secret_key=SecretStr("k"), worker_api_token=token, api_internal_url="http://api:8080/"),
    )


def _resp(status, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", "http://api"))


def test_without_a_worker_credential_the_payload_token_is_used_and_nothing_is_called():
    for token in (None, SecretStr("")):  # compose passes "" when the variable is not configured
        with _settings(token), patch.object(worker, "decrypt_job_token", return_value="payload"), patch.object(
            worker.httpx, "post"
        ) as post:
            assert worker._job_token(7, "enc", "{}") == "payload"
        post.assert_not_called()


def test_a_fresh_token_from_the_api_wins():
    with _settings(SecretStr("s")), patch.object(worker, "decrypt_job_token", return_value="payload"), patch.object(
        worker.httpx, "post", return_value=_resp(200, {"token": "fresh"})
    ) as post:
        assert worker._job_token(7, "enc", "{}") == "fresh"
    assert post.call_args.args[0] == "http://api:8080/internal/jobs/7/installation-token"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer s"}


def test_a_404_a_server_error_or_a_network_failure_falls_back_to_the_payload_token():
    for outcome in (_resp(404), _resp(502), _resp(200, {}), httpx.ConnectError("down")):
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with _settings(SecretStr("s")), patch.object(worker, "decrypt_job_token", return_value="payload"), patch.object(
            worker.httpx, "post", **kwargs
        ):
            assert worker._job_token(7, "enc", "{}") == "payload"


def test_a_corrupt_payload_token_still_raises_before_any_call():
    with _settings(SecretStr("s")), patch.object(worker, "decrypt_job_token", side_effect=ValueError("bad")), patch.object(
        worker.httpx, "post"
    ) as post:
        try:
            worker._job_token(7, "enc", "{}")
        except ValueError:
            pass
        else:
            raise AssertionError("expected the decrypt error")
    post.assert_not_called()


def test_an_expired_payload_token_is_replaced_by_the_refresh():
    expired = json.dumps({"token_expires_at": time.time() - 5})
    with _settings(SecretStr("s")), patch.object(worker, "decrypt_job_token", return_value="payload"), patch.object(
        worker.httpx, "post", return_value=_resp(200, {"token": "fresh"})
    ):
        assert worker._job_token(7, "enc", expired) == "fresh"


def test_an_expired_payload_token_with_no_refresh_available_fails_with_the_expiry_message():
    expired = json.dumps({"token_expires_at": time.time() - 5})
    with _settings(SecretStr("s")), patch.object(worker, "decrypt_job_token", return_value="payload"), patch.object(
        worker.httpx, "post", return_value=_resp(404)
    ):
        try:
            worker._job_token(7, "enc", expired)
        except RuntimeError as error:
            assert str(error) == worker.TOKEN_EXPIRED_MESSAGE
        else:
            raise AssertionError("expected the stale payload token to be refused")

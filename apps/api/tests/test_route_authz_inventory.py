"""Every route must carry an authorization dependency, or be listed here with the reason it is public (#1344).

Walks the real app's routes and their dependency trees. A new route without `require_auth`,
`require_workspace_admin`, `require_org_role(...)`, `require_scope(...)` or the like fails this test until it
either gets one or is added to _PUBLIC with a reason a reviewer can challenge."""

from fastapi.routing import APIRoute

from src.main import app

# (method, path) -> why it needs no session/role dependency.
_PUBLIC = {
    ("POST", "/auth/setup"): "first-run admin creation; refuses once any user exists",
    ("GET", "/auth/setup-required"): "tells the UI whether first-run setup is still open",
    ("POST", "/auth/register"): "self sign-up (gated by registration_enabled / an invitation token)",
    ("POST", "/auth/login"): "credential check",
    ("POST", "/auth/logout"): "reads the bearer token itself so an already-expired token can still be revoked",
    ("POST", "/auth/forgot-password"): "emailed reset link, enumeration-safe",
    ("POST", "/auth/reset-password"): "authorised by the signed reset token",
    ("POST", "/auth/verify-email"): "authorised by the emailed verification token",
    ("POST", "/auth/confirm-email-change"): "authorised by the emailed confirmation token",
    ("GET", "/auth/github/login"): "starts the GitHub OAuth redirect",
    ("GET", "/auth/github/callback"): "OAuth callback, authorised by the signed state and the GitHub code",
    ("GET", "/badges/{org_login}/score.svg"): "public score badge, only served when the org enabled it",
    ("GET", "/healthz"): "liveness probe",
    ("GET", "/invitations/{token}"): "the invitation token is the credential",
    ("POST", "/internal/jobs/{job_id}/installation-token"): "checks WORKER_API_TOKEN inline",
    ("POST", "/webhooks/github"): "authorised by the GitHub HMAC signature",
}


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def _has_authz(route: APIRoute) -> bool:
    for call in _dependency_calls(route.dependant):
        name = getattr(call, "__name__", "")
        # require_auth / require_workspace_admin / require_scope / require_personal_tenant, and the inner
        # function require_org_role() returns.
        if name.startswith("require_") or (call.__module__ == "src.core.rbac" and name == "dependency"):
            return True
    return False


def _routes() -> dict[tuple[str, str], APIRoute]:
    return {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }


def test_every_route_has_an_authorization_dependency_or_is_listed_as_public():
    unprotected = sorted(key for key, route in _routes().items() if not _has_authz(route) and key not in _PUBLIC)
    assert not unprotected, (
        "routes with no require_auth / require_workspace_admin / require_org_role / require_scope dependency "
        f"and not in _PUBLIC: {unprotected}"
    )


def test_the_public_list_has_no_stale_or_now_protected_entries():
    routes = _routes()
    assert not [key for key in _PUBLIC if key not in routes], "a _PUBLIC entry matches no route; remove it"
    assert not [key for key in _PUBLIC if _has_authz(routes[key])], "a _PUBLIC entry now has an authz dependency; remove it"

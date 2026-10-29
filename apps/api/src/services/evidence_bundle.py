"""Compliance evidence bundle: one dated zip tying together what an auditor asks for.

Contents (all scoped to one org's tenant, over an optional date range):
- ``scan_history.csv`` and ``failed_checks.csv``: every scan, and per scan each failing/erroring check
  (with any waiver that excluded it from the score);
- ``members.csv`` and ``outside_collaborators.csv``: the org roster with role and 2FA status, and the
  repository access held by outside collaborators;
- ``audit_log.csv``: the org's audit slice, read server-side with the same tenant filter as
  ``GET /orgs/{org}/audit`` (not whatever rows a browser happened to load);
- ``manifest.json``: when, for whom, what range, which Clevis version, row counts and truncation flags.

Every list is capped; a capped list is flagged ``truncated`` in the manifest, never silently partial.
The roster comes from Clevis's ingested copy (webhooks plus the membership reconciliation poll), not a
live GitHub call, so an export needs no GitHub token; the manifest says how fresh that copy is.
CSV cells get the same spreadsheet formula-injection defence as ``apps/ui/lib/csv.ts``.
"""

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.core.version import VERSION
from src.core.db import OrgMember, OrgMembershipSyncCursor, RepoCollaborator
from src.repositories import audit_repo, scan_results_repo

SCAN_LIMIT = 5000
AUDIT_LIMIT = 10000
ROSTER_LIMIT = 10000

_FORMULA_PREFIX = re.compile(r"^[=+\-@\t\r]")
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def csv_cell(value: object) -> str:
    """One CSV field value, defanged against spreadsheet formula injection: a leading =, +, -, @, tab or
    CR (other than a plain number) gets a single quote prepended. Quoting itself is left to csv.writer."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    if _FORMULA_PREFIX.match(text) and not _PLAIN_NUMBER.match(text):
        text = "'" + text
    return text


def to_csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([csv_cell(h) for h in header])
    for row in rows:
        writer.writerow([csv_cell(v) for v in row])
    return out.getvalue()


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _scan_files(scans: list[dict]) -> tuple[str, str, int]:
    history, failing = [], []
    for s in scans:
        history.append([s["id"], s["created_at"], s["owner"], s["score"], s["total_checks"], s["failed_checks"]])
        for c in s["checks"]:
            if c.get("status") not in ("fail", "error"):
                continue
            value = c.get("value")
            repos = value.get("repos") if isinstance(value, dict) else None
            waived = c.get("waived") or {}
            failing.append([
                s["id"], s["created_at"], c.get("id"), c.get("title"), c.get("severity"), c.get("status"),
                c.get("scored"), "; ".join(str(r) for r in repos) if isinstance(repos, list) else "",
                waived.get("until"), waived.get("owner"), waived.get("reason"),
            ])
    return (
        to_csv(["scan_id", "scanned_at", "owner", "score", "total_checks", "failed_checks"], history),
        to_csv(
            ["scan_id", "scanned_at", "check_id", "title", "severity", "status", "scored", "repos",
             "waived_until", "waived_owner", "waived_reason"],
            failing,
        ),
        len(failing),
    )


def build_bundle(
    db: Session,
    *,
    org_login: str,
    tenant_id: int,
    generated_by: str,
    since: datetime | None,
    until: datetime | None,
) -> tuple[bytes, dict]:
    """The zip bytes and the manifest dict. `until` is an inclusive end-of-day instant; the caller must
    have set the tenant session context (the roster tables are row-level secured)."""
    now = datetime.now(timezone.utc)
    files: dict[str, dict] = {}
    contents: dict[str, str] = {}

    scans = scan_results_repo.list_for_export(
        db, owner=org_login, since=since, until=until, limit=SCAN_LIMIT, org_tenant_id=tenant_id
    )
    scans_truncated = len(scans) > SCAN_LIMIT
    scans = scans[:SCAN_LIMIT]
    contents["scan_history.csv"], contents["failed_checks.csv"], failing_rows = _scan_files(scans)
    files["scan_history.csv"] = {"rows": len(scans), "truncated": scans_truncated}
    files["failed_checks.csv"] = {"rows": failing_rows, "truncated": scans_truncated}

    members = (
        db.query(OrgMember).filter(OrgMember.tenant_id == tenant_id).order_by(OrgMember.login).limit(ROSTER_LIMIT + 1).all()
    )
    members_truncated = len(members) > ROSTER_LIMIT
    members = members[:ROSTER_LIMIT]
    contents["members.csv"] = to_csv(
        ["login", "role", "two_factor_enabled", "added_at"],
        [[m.login, m.role, m.two_factor_enabled, _iso(m.added_at)] for m in members],
    )
    files["members.csv"] = {"rows": len(members), "truncated": members_truncated}

    outside_q = db.query(RepoCollaborator).filter(RepoCollaborator.tenant_id == tenant_id)
    outside = (
        outside_q.filter(RepoCollaborator.is_outside_collaborator.is_(True))
        .order_by(RepoCollaborator.repo, RepoCollaborator.login)
        .limit(ROSTER_LIMIT + 1)
        .all()
    )
    outside_truncated = len(outside) > ROSTER_LIMIT
    outside = outside[:ROSTER_LIMIT]
    unclassified = outside_q.filter(RepoCollaborator.is_outside_collaborator.is_(None)).count()
    contents["outside_collaborators.csv"] = to_csv(
        ["login", "repo", "permission", "granted_at"],
        [[c.login, c.repo, c.permission, _iso(c.granted_at)] for c in outside],
    )
    files["outside_collaborators.csv"] = {"rows": len(outside), "truncated": outside_truncated}

    # list_for_tenant treats `until` as exclusive, so step past the inclusive end-of-day instant.
    try:
        audit_until = until + timedelta(microseconds=1) if until else None
    except OverflowError:  # until is the largest representable instant: effectively no upper bound
        audit_until = None
    audit = audit_repo.list_for_tenant(db, tenant_id, since=since, until=audit_until, limit=AUDIT_LIMIT + 1)
    audit_truncated = len(audit) > AUDIT_LIMIT
    audit = audit[:AUDIT_LIMIT]
    contents["audit_log.csv"] = to_csv(
        ["id", "created_at", "actor", "action", "target", "payload"],
        [[a.id, _iso(a.created_at), a.actor, a.action, a.target, a.payload] for a in audit],
    )
    files["audit_log.csv"] = {"rows": len(audit), "truncated": audit_truncated}

    cursor = db.get(OrgMembershipSyncCursor, tenant_id)
    notes = [
        "members.csv and outside_collaborators.csv are Clevis's ingested copy (GitHub webhooks plus the "
        "membership reconciliation poll), not a live GitHub read; see roster_last_synced_at.",
        "two_factor_enabled is empty when the reconciliation poll has not read it yet (unknown, not off).",
        "audit_log.csv lists the rows that existed when this export was generated; the export itself is "
        "recorded as an evidence.export row afterwards.",
    ]
    if unclassified:
        notes.append(
            f"{unclassified} collaborator grant(s) are not yet classified as outside or member and are not listed."
        )
    manifest = {
        "generated_at": now.isoformat(),
        "generated_by": generated_by,
        "org": org_login,
        "range": {"since": _iso(since) or None, "until": _iso(until) or None},
        "clevis_version": VERSION,
        "roster_last_synced_at": _iso(cursor.last_synced_at) if cursor and cursor.last_synced_at else None,
        "truncated": any(f["truncated"] for f in files.values()),
        "files": files,
        "notes": notes,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, text in contents.items():
            z.writestr(name, text)
    return buf.getvalue(), manifest

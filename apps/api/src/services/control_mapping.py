"""Which security-framework controls each Clevis check gives evidence for (#1277).

A static, reviewable table: SOC 2 (2017 Trust Services Criteria), CIS Critical Security Controls v8 and
ISO/IEC 27001:2022 Annex A. It is an aid for the auditor's own mapping, not an attestation: a passing check is
evidence toward a control, never proof the control is met, and a control usually needs more than one source.
A check with no clear counterpart (license, stale branches) is deliberately left unmapped rather than stretched.

Served by ``GET /compliance/controls`` (the compliance matrix) and written into the evidence bundle
(``control_mapping.csv`` and the ``controls`` column of ``failed_checks.csv``). Adding a check means adding a row
here too; ``test_control_mapping`` fails if the registered checks and this table drift apart.
"""

FRAMEWORKS: list[dict[str, str]] = [
    {"id": "soc2", "name": "SOC 2", "version": "2017 Trust Services Criteria"},
    {"id": "cis", "name": "CIS Controls", "version": "v8"},
    {"id": "iso27001", "name": "ISO/IEC 27001", "version": "2022 Annex A"},
]

# framework id -> control id -> short name
CONTROL_NAMES: dict[str, dict[str, str]] = {
    "soc2": {
        "CC2.3": "Communication with external parties",
        "CC6.1": "Logical access security",
        "CC6.3": "Role-based access and least privilege",
        "CC7.1": "Detection and monitoring of vulnerabilities",
        "CC8.1": "Change management",
    },
    "cis": {
        "6.3": "Require MFA for externally-exposed applications",
        "6.5": "Require MFA for administrative access",
        "6.8": "Define and maintain role-based access control",
        "7.7": "Remediate detected vulnerabilities",
        "16.1": "Establish a secure application development process",
        "16.2": "Process to accept and address software vulnerabilities",
        "16.5": "Use up-to-date and trusted third-party software components",
        "16.12": "Implement code-level security checks",
    },
    "iso27001": {
        "A.5.15": "Access control",
        "A.5.17": "Authentication information",
        "A.5.21": "Managing information security in the ICT supply chain",
        "A.8.2": "Privileged access rights",
        "A.8.4": "Access to source code",
        "A.8.5": "Secure authentication",
        "A.8.8": "Management of technical vulnerabilities",
        "A.8.28": "Secure coding",
        "A.8.32": "Change management",
    },
}

# check id -> framework id -> control ids
CHECK_CONTROLS: dict[str, dict[str, list[str]]] = {
    "organization_members_mfa_required": {"soc2": ["CC6.1"], "cis": ["6.3", "6.5"], "iso27001": ["A.8.5"]},
    "organization_members_2fa_enabled": {"soc2": ["CC6.1"], "cis": ["6.3", "6.5"], "iso27001": ["A.8.5"]},
    "repository_default_branch_protection_enabled": {
        "soc2": ["CC8.1"], "cis": ["16.1"], "iso27001": ["A.8.32", "A.8.4"],
    },
    "repository_default_branch_no_force_push": {
        "soc2": ["CC8.1"], "cis": ["16.1"], "iso27001": ["A.8.32", "A.8.4"],
    },
    "repository_secret_scanning_enabled": {
        "soc2": ["CC6.1", "CC7.1"], "cis": ["16.12"], "iso27001": ["A.5.17"],
    },
    "repository_dependabot_alerts_clear": {
        "soc2": ["CC7.1"], "cis": ["16.5", "7.7"], "iso27001": ["A.8.8"],
    },
    "repository_code_scanning_alerts_clear": {
        "soc2": ["CC7.1"], "cis": ["16.12", "7.7"], "iso27001": ["A.8.8", "A.8.28"],
    },
    "repository_no_outside_collaborator_admins": {
        "soc2": ["CC6.3"], "cis": ["6.8"], "iso27001": ["A.5.15", "A.8.2"],
    },
    "repository_codeowners_present": {"soc2": ["CC8.1"], "iso27001": ["A.8.32"]},
    "repository_security_policy_present": {"soc2": ["CC2.3"], "cis": ["16.2"]},
    "repository_actions_pinned_to_sha": {"soc2": ["CC8.1"], "cis": ["16.5"], "iso27001": ["A.5.21"]},
    # No clear counterpart in these frameworks: listed so the table covers every check explicitly.
    "repository_license_present": {},
    "repository_no_stale_branches": {},
}


def controls_for(check_id: str) -> list[dict[str, str]]:
    """The mapped controls of one check as ``{framework, id, name}`` rows, frameworks in table order; empty for
    an unmapped or unknown check."""
    mapped = CHECK_CONTROLS.get(check_id, {})
    return [
        {"framework": f["id"], "id": cid, "name": CONTROL_NAMES[f["id"]][cid]}
        for f in FRAMEWORKS
        for cid in mapped.get(f["id"], [])
    ]


def framework_name(framework_id: str) -> str:
    return next(f["name"] for f in FRAMEWORKS if f["id"] == framework_id)


def control_label(row: dict[str, str]) -> str:
    """``SOC 2 CC8.1`` style text for a CSV cell."""
    return f"{framework_name(row['framework'])} {row['id']}"

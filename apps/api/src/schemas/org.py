from pydantic import BaseModel


class MyOrgMembershipOut(BaseModel):
    org_login: str
    role: str
    # How the membership was granted: "invite" (can be left) or "github" (mirrors the GitHub org).
    source: str = "github"

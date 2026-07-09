from pydantic import BaseModel


class MyOrgMembershipOut(BaseModel):
    org_login: str
    role: str

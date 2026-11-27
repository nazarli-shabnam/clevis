"""The static check-to-control mapping (#1277), for the compliance matrix. Not tenant data: any signed-in user."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.core.auth import UserOut, require_auth
from src.services import control_mapping

router = APIRouter()


class Framework(BaseModel):
    id: str
    name: str
    version: str


class Control(BaseModel):
    framework: str
    id: str
    name: str


class ControlMappingOut(BaseModel):
    frameworks: list[Framework]
    # check id -> the controls it gives evidence for (empty when it has no clear counterpart)
    checks: dict[str, list[Control]]


@router.get("/compliance/controls", response_model=ControlMappingOut)
def get_control_mapping(_user: UserOut = Depends(require_auth)):
    return ControlMappingOut(
        frameworks=control_mapping.FRAMEWORKS,
        checks={cid: control_mapping.controls_for(cid) for cid in control_mapping.CHECK_CONTROLS},
    )

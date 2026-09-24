from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyCookie
from minmodkg.config import JWT_ALGORITHM, SECRET_KEY
from minmodkg.models.kg.base import MINMOD_KG, MINMOD_NS
from minmodkg.models.kgrel.base import get_rel_session
from minmodkg.models.kgrel.entities.commodity import Commodity
from minmodkg.models.kgrel.entities.country import Country
from minmodkg.models.kgrel.entities.deposit_type import DepositType
from minmodkg.models.kgrel.entities.state_or_province import StateOrProvince
from minmodkg.models.kgrel.user import User
from minmodkg.services.mineral_site import MineralSiteService
from minmodkg.services.paper import PaperService
from minmodkg.services.sample import SampleService
from minmodkg.typing import InternalID
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# for login/security
token_from_cookie = APIKeyCookie(name="session")
TokenDep = Annotated[str, Depends(token_from_cookie)]
RelSessionDep = Annotated[Session, Depends(get_rel_session.__wrapped__)]


async def get_current_user(session: RelSessionDep, token: TokenDep):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("username")
        if username is None:
            raise credentials_exception
        expired_at = datetime.fromtimestamp(payload.get("exp"), timezone.utc)
        if expired_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credentials expired",
            )
    except jwt.InvalidTokenError:
        raise credentials_exception

    user = session.get(User, username)
    if user is None:
        raise credentials_exception
    return user


def get_mineral_site_service():
    return MineralSiteService()


def get_sample_service():
    return SampleService()


def get_paper_service():
    return PaperService()


CurrentUserDep = Annotated[User, Depends(get_current_user)]
MineralSiteServiceDep = Annotated[MineralSiteService, Depends(get_mineral_site_service)]
SampleServiceDep = Annotated[SampleService, Depends(get_sample_service)]
PaperServiceDep = Annotated[PaperService, Depends(get_paper_service)]


def get_snapshot_id():
    query = f"SELECT ?snapshot_id WHERE {{ {MINMOD_NS.mr.alias}:kg {MINMOD_NS.dcterms.alias}:hasVersion ?snapshot_id }}"
    qres = MINMOD_KG.query(query)
    return qres[0]["snapshot_id"]


def norm_commodity(commodity: str) -> InternalID:
    if commodity.startswith("http"):
        raise HTTPException(
            status_code=404,
            detail=f"Expect commodity to be either just an id (QXXX) or name. Get `{commodity}` instead",
        )
    if not is_minmod_id(commodity):
        norm_commodity = get_commodity_by_name(commodity)
        if norm_commodity is None:
            raise HTTPException(
                status_code=404, detail=f"Commodity `{commodity}` not found"
            )
    else:
        norm_commodity = commodity
    return norm_commodity


def norm_country(country: str) -> InternalID:
    if country.startswith("http"):
        raise HTTPException(
            status_code=404,
            detail=f"Expect country to be either just an id (QXXX) or name. Get `{country}` instead",
        )
    if not is_minmod_id(country):
        norm_country = get_country_by_name(country)
        if norm_country is None:
            raise HTTPException(
                status_code=404, detail=f"country `{country}` not found"
            )
    else:
        norm_country = country
    return norm_country


def norm_state_or_province(state_or_province: str) -> InternalID:
    if state_or_province.startswith("http"):
        raise HTTPException(
            status_code=404,
            detail=f"Expect state_or_province to be either just an id (QXXX) or name. Get `{state_or_province}` instead",
        )
    if not is_minmod_id(state_or_province):
        norm_state_or_province = get_state_or_province_by_name(state_or_province)
        if norm_state_or_province is None:
            raise HTTPException(
                status_code=404,
                detail=f"state_or_province `{state_or_province}` not found",
            )
    else:
        norm_state_or_province = state_or_province
    return norm_state_or_province


def norm_deposit_type(deposit_type: str) -> InternalID:
    if deposit_type.startswith("http"):
        raise HTTPException(
            status_code=404,
            detail=f"Expect deposit_type to be either just an id (QXXX) or name. Get `{deposit_type}` instead",
        )
    if not is_minmod_id(deposit_type):
        norm_deposit_type = get_deposit_type_by_name(deposit_type)
        if norm_deposit_type is None:
            raise HTTPException(
                status_code=404,
                detail=f"deposit_type `{deposit_type}` not found",
            )
    else:
        norm_deposit_type = deposit_type
    return norm_deposit_type


def is_minmod_id(text: str) -> bool:
    return text.startswith("Q") and text[1:].isdigit()


def get_commodity_by_name(
    name: str,
) -> Optional[InternalID]:
    with get_rel_session() as session:
        commodity_id = session.scalar(
            select(Commodity.id).where(Commodity.lower_name == name.lower())
        )
        return commodity_id


def get_country_by_name(
    name: str,
) -> Optional[InternalID]:
    with get_rel_session() as session:
        country_id = session.scalar(
            select(Country.id).where(func.lower(Country.name) == name.lower())
        )
        return country_id


def get_state_or_province_by_name(
    name: str,
) -> Optional[InternalID]:
    with get_rel_session() as session:
        state_or_province_id = session.scalar(
            select(StateOrProvince.id).where(
                func.lower(StateOrProvince.name) == name.lower()
            )
        )
        return state_or_province_id


def get_deposit_type_by_name(
    name: str,
) -> Optional[InternalID]:
    with get_rel_session() as session:
        deposit_type_id = session.scalar(
            select(DepositType.id).where(func.lower(DepositType.name) == name.lower())
        )
        return deposit_type_id

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository
from app.domain.members.service import ensure_member_usable
from app.storage import get_storage
from app.storage.base import FileStorage

DbSession = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[FileStorage, Depends(get_storage)]


async def get_current_member(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Member:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("인증 토큰이 필요합니다.")
    token = authorization.split(" ", 1)[1]
    subject = decode_access_token(token)
    try:
        member_pk = int(subject)
    except ValueError as exc:
        raise UnauthorizedError("유효하지 않은 사용자입니다.") from exc
    member = await MemberRepository(db).get_by_pk(member_pk)
    if member is None:
        raise UnauthorizedError("유효하지 않은 사용자입니다.")
    # 이미 발급된 토큰이라도 그 사이 상태가 바뀌었다면(관리자가 정지, 본인 탈퇴 등) 즉시 막는다.
    ensure_member_usable(member)
    return member


CurrentMember = Annotated[Member, Depends(get_current_member)]


async def get_current_admin(member: CurrentMember) -> Member:
    if not member.has_any_role("0202"):
        raise ForbiddenError("관리자 권한이 필요합니다.")
    return member


CurrentAdmin = Annotated[Member, Depends(get_current_admin)]

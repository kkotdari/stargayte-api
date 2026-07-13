from fastapi import APIRouter

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.core.exceptions import ForbiddenError
from app.domain.members.schemas import (
    MemberCreateByAdmin,
    MemberOut,
    MemberPasswordUpdate,
    MemberReplayAliasAdd,
    MemberReplayAliasesReplace,
    MemberRolesUpdate,
    MemberStatusUpdate,
    MemberUpdate,
)
from app.domain.members.service import MemberService

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
async def list_members(db: DbSession, storage: StorageDep, _current: CurrentMember) -> list[MemberOut]:
    members = await MemberService(db, storage).list_members()
    return [MemberOut.model_validate(m) for m in members]


@router.post("", response_model=MemberOut)
async def create_member_by_admin(
    payload: MemberCreateByAdmin,
    db: DbSession,
    storage: StorageDep,
    admin: CurrentAdmin,
) -> MemberOut:
    member = await MemberService(db, storage).create_member_by_admin(
        member_id=payload.id,
        password=payload.password,
        battletag=payload.battletag,
        replay_aliases=payload.replay_aliases,
        insta=payload.insta,
        avatar=payload.avatar,
        actor=admin,
    )
    await db.commit()
    return MemberOut.model_validate(member)


@router.patch("/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: str,
    patch: MemberUpdate,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MemberOut:
    if current.id != member_id and not current.has_any_role("0202"):
        raise ForbiddenError("본인 프로필만 수정할 수 있습니다.")
    member = await MemberService(db, storage).update_profile(member_id, patch, actor_pk=current.pk)
    await db.commit()
    return MemberOut.model_validate(member)


@router.put("/{member_id}/replay-aliases", response_model=MemberOut)
async def replace_member_replay_aliases(
    member_id: str,
    patch: MemberReplayAliasesReplace,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MemberOut:
    member = await MemberService(db, storage).replace_replay_aliases(
        member_id, patch.aliases, actor_pk=current.pk
    )
    await db.commit()
    return MemberOut.model_validate(member)


@router.post("/{member_id}/replay-aliases", response_model=MemberOut)
async def add_member_replay_alias(
    member_id: str,
    patch: MemberReplayAliasAdd,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MemberOut:
    member = await MemberService(db, storage).add_replay_alias(
        member_id, patch.alias, actor_pk=current.pk
    )
    await db.commit()
    return MemberOut.model_validate(member)


@router.patch("/{member_id}/password", response_model=MemberOut)
async def update_member_password(
    member_id: str,
    patch: MemberPasswordUpdate,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MemberOut:
    member = await MemberService(db, storage).update_password(
        member_id, patch.current_password, patch.new_password, actor=current
    )
    await db.commit()
    return MemberOut.model_validate(member)


@router.patch("/{member_id}/status", response_model=MemberOut)
async def update_member_status(
    member_id: str,
    patch: MemberStatusUpdate,
    db: DbSession,
    storage: StorageDep,
    admin: CurrentAdmin,
) -> MemberOut:
    member = await MemberService(db, storage).update_status(member_id, patch.status, actor=admin)
    await db.commit()
    return MemberOut.model_validate(member)


@router.patch("/{member_id}/roles", response_model=MemberOut)
async def update_member_roles(
    member_id: str,
    patch: MemberRolesUpdate,
    db: DbSession,
    storage: StorageDep,
    admin: CurrentAdmin,
) -> MemberOut:
    member = await MemberService(db, storage).update_roles(member_id, patch.roles, actor=admin)
    await db.commit()
    return MemberOut.model_validate(member)


@router.post("/{member_id}/avatar/reprocess", response_model=MemberOut)
async def reprocess_member_avatar(
    member_id: str, db: DbSession, storage: StorageDep, admin: CurrentAdmin
) -> MemberOut:
    member = await MemberService(db, storage).reprocess_avatar(member_id, actor_pk=admin.pk)
    await db.commit()
    return MemberOut.model_validate(member)


@router.post("/{member_id}/withdraw", response_model=MemberOut)
async def withdraw_member(
    member_id: str, db: DbSession, storage: StorageDep, current: CurrentMember
) -> MemberOut:
    member = await MemberService(db, storage).withdraw(member_id, actor=current)
    await db.commit()
    return MemberOut.model_validate(member)

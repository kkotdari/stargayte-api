from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.leagues.schemas import (
    LeagueBracketSeedIn,
    LeagueCreateIn,
    LeagueListOut,
    LeagueMatchSide,
    LeagueOut,
    LeagueTeamCompositionIn,
)
from app.domain.leagues.service import LeagueService

# 화면 전체가 운영자 전용(요청: "일단 운영자만 볼수있게 처리")이라 조회(GET) 포함
# 전 엔드포인트를 CurrentAdmin으로 게이팅한다.
router = APIRouter(prefix="/leagues", tags=["leagues"])


@router.get("", response_model=LeagueListOut)
async def list_leagues(db: DbSession, current: CurrentAdmin) -> LeagueListOut:
    return await LeagueService(db).list_leagues()


@router.post("", response_model=LeagueOut)
async def create_league(payload: LeagueCreateIn, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).create_league(payload, actor=current)


@router.get("/{league_id}", response_model=LeagueOut)
async def get_league(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).get_league(league_id)


@router.delete("/{league_id}", status_code=204)
async def delete_league(league_id: int, db: DbSession, current: CurrentAdmin) -> None:
    await LeagueService(db).delete_league(league_id)


@router.put("/{league_id}/teams", response_model=LeagueOut)
async def set_team_composition(
    league_id: int, payload: LeagueTeamCompositionIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_team_composition(league_id, payload, actor=current)


# 대진표는 우승 자리 하나에서 시작해 왼쪽으로 가지를 쳐 나간다(요청) — 크기를 미리 받는
# 엔드포인트(bracket/generate)는 사라졌다.
@router.post("/{league_id}/bracket", response_model=LeagueOut)
async def start_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).start_bracket(league_id, actor=current)


@router.delete("/{league_id}/bracket", response_model=LeagueOut)
async def delete_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).delete_bracket(league_id, actor=current)


@router.post("/{league_id}/bracket/matches/{match_id}/{side}/branch", response_model=LeagueOut)
async def branch_slot(
    league_id: int, match_id: int, side: LeagueMatchSide, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).branch_slot(league_id, match_id, side, actor=current)


@router.delete("/{league_id}/bracket/matches/{match_id}/{side}/branch", response_model=LeagueOut)
async def unbranch_slot(
    league_id: int, match_id: int, side: LeagueMatchSide, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).unbranch_slot(league_id, match_id, side, actor=current)


@router.post("/{league_id}/bracket/confirm", response_model=LeagueOut)
async def confirm_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).confirm_bracket(league_id, actor=current)


@router.put("/{league_id}/bracket/seeding", response_model=LeagueOut)
async def set_bracket_seeding(
    league_id: int, payload: LeagueBracketSeedIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket_seeding(league_id, payload, actor=current)

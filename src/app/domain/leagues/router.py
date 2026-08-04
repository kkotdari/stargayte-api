from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.leagues.schemas import (
    LeagueBracketByesIn,
    LeagueBracketGenerateIn,
    LeagueBracketSeedIn,
    LeagueCreateIn,
    LeagueListOut,
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


@router.post("/{league_id}/bracket/generate", response_model=LeagueOut)
async def generate_bracket(
    league_id: int, payload: LeagueBracketGenerateIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).generate_bracket(league_id, payload, actor=current)


@router.post("/{league_id}/bracket/confirm", response_model=LeagueOut)
async def confirm_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).confirm_bracket(league_id, actor=current)


# 부전승 자리 — 관리자가 직접 고른다(요청: "한쪽은 토너먼트, 그 승자가 다른 두 명의
# 승자와 결승"). 같은 대진 규모라도 부전승을 어느 칸에 두느냐로 그 모양이 갈린다.
@router.put("/{league_id}/bracket/byes", response_model=LeagueOut)
async def set_bracket_byes(
    league_id: int, payload: LeagueBracketByesIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket_byes(league_id, payload, actor=current)


@router.put("/{league_id}/bracket/seeding", response_model=LeagueOut)
async def set_bracket_seeding(
    league_id: int, payload: LeagueBracketSeedIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket_seeding(league_id, payload, actor=current)

from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.leagues.schemas import (
    LeagueBracketGenerateIn,
    LeagueBracketSeedIn,
    LeagueCreateIn,
    LeagueListOut,
    LeagueMatchOut,
    LeagueMatchResultIn,
    LeagueMatchScheduleIn,
    LeagueMatchSlotIn,
    LeagueOut,
    LeagueTeamCompositionIn,
    LeagueTeamOut,
    LeagueTeamRosterIn,
    LeagueUpdateIn,
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


@router.patch("/{league_id}", response_model=LeagueOut)
async def update_league(
    league_id: int, payload: LeagueUpdateIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).update_league(league_id, payload, actor=current)


@router.delete("/{league_id}", status_code=204)
async def delete_league(league_id: int, db: DbSession, current: CurrentAdmin) -> None:
    await LeagueService(db).delete_league(league_id)


@router.post("/{league_id}/teams", response_model=LeagueTeamOut)
async def add_team(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueTeamOut:
    return await LeagueService(db).add_team(league_id, actor=current)


@router.put("/{league_id}/teams", response_model=LeagueOut)
async def set_team_composition(
    league_id: int, payload: LeagueTeamCompositionIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_team_composition(league_id, payload, actor=current)


@router.put("/{league_id}/teams/{team_id}/roster", response_model=LeagueTeamOut)
async def set_roster(
    league_id: int, team_id: int, payload: LeagueTeamRosterIn, db: DbSession, current: CurrentAdmin,
) -> LeagueTeamOut:
    return await LeagueService(db).set_roster(league_id, team_id, payload, actor=current)


@router.delete("/{league_id}/teams/{team_id}", response_model=LeagueOut)
async def delete_team(
    league_id: int, team_id: int, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).delete_team(league_id, team_id, actor=current)


@router.post("/{league_id}/bracket/generate", response_model=LeagueOut)
async def generate_bracket(
    league_id: int, payload: LeagueBracketGenerateIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).generate_bracket(league_id, payload, actor=current)


@router.post("/{league_id}/bracket/confirm", response_model=LeagueOut)
async def confirm_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).confirm_bracket(league_id, actor=current)


@router.patch("/{league_id}/matches/{match_id}/slot", response_model=LeagueOut)
async def set_match_slot(
    league_id: int, match_id: int, payload: LeagueMatchSlotIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_match_slot(league_id, match_id, payload, actor=current)


@router.put("/{league_id}/bracket/seeding", response_model=LeagueOut)
async def set_bracket_seeding(
    league_id: int, payload: LeagueBracketSeedIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket_seeding(league_id, payload, actor=current)


@router.patch("/{league_id}/matches/{match_id}/schedule", response_model=LeagueMatchOut)
async def set_match_schedule(
    league_id: int, match_id: int, payload: LeagueMatchScheduleIn, db: DbSession, current: CurrentAdmin,
) -> LeagueMatchOut:
    return await LeagueService(db).set_match_schedule(league_id, match_id, payload, actor=current)


@router.post("/{league_id}/matches/{match_id}/result", response_model=LeagueOut)
async def enter_match_result(
    league_id: int, match_id: int, payload: LeagueMatchResultIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).enter_match_result(league_id, match_id, payload, actor=current)


@router.delete("/{league_id}/matches/{match_id}/result", response_model=LeagueOut)
async def clear_match_result(
    league_id: int, match_id: int, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).clear_match_result(league_id, match_id, actor=current)

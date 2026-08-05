from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.leagues.schemas import (
    LeagueBracketIn,
    LeagueBracketSeedIn,
    LeagueCreateIn,
    LeagueListOut,
    LeagueMatchResultIn,
    LeagueMatchScheduleIn,
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


# 대진표의 모양과 배정은 한 번에 저장한다(요청: 바로바로가 아니라 마지막 저장 버튼) —
# 가지를 치고 지우는 조작마다 부르던 엔드포인트 넷(대진표 생성/삭제, 가지 추가/삭제)을
# 이 하나가 대신한다.
@router.put("/{league_id}/bracket", response_model=LeagueOut)
async def set_bracket(
    league_id: int, payload: LeagueBracketIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket(league_id, payload, actor=current)


@router.put("/{league_id}/matches/{match_id}/schedule", response_model=LeagueOut)
async def set_match_schedule(
    league_id: int, match_id: int, payload: LeagueMatchScheduleIn,
    db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_match_schedule(league_id, match_id, payload, actor=current)


@router.put("/{league_id}/matches/{match_id}/result", response_model=LeagueOut)
async def set_match_result(
    league_id: int, match_id: int, payload: LeagueMatchResultIn,
    db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_match_result(league_id, match_id, payload, actor=current)


@router.post("/{league_id}/bracket/confirm", response_model=LeagueOut)
async def confirm_bracket(league_id: int, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).confirm_bracket(league_id, actor=current)


@router.put("/{league_id}/bracket/seeding", response_model=LeagueOut)
async def set_bracket_seeding(
    league_id: int, payload: LeagueBracketSeedIn, db: DbSession, current: CurrentAdmin,
) -> LeagueOut:
    return await LeagueService(db).set_bracket_seeding(league_id, payload, actor=current)

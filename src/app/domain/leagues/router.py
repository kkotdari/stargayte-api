from fastapi import APIRouter

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
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

# 경계는 '판을 짜는 일'과 '판에 적어 넣는 일'로 갈린다(요청: "일정등록과 결과입력은 아무나
# 가능하게 열어주고 수정은 불가").
#   · 회원 누구나 — 조회(GET), 경기 일시, 경기 결과. 일시와 결과는 그 자리에 있던 사람이
#     적는 것이 가장 빠르고 정확하다. 적을 수 있는 자리 자체는 이미 운영자가 정해 뒀고,
#     결과는 확정된 대진에만 들어가므로 판의 모양을 바꾸지는 못한다.
#   · 운영자만 — 리그 생성·삭제, 팀 구성, 대진표 모양과 배정, 확정. 여기가 '수정'이다.
# 화면의 '수정' 토글은 겉모습일 뿐이라, 실제 경계는 여기 이 줄들이다.
router = APIRouter(prefix="/leagues", tags=["leagues"])


@router.get("", response_model=LeagueListOut)
async def list_leagues(db: DbSession, current: CurrentMember) -> LeagueListOut:
    return await LeagueService(db).list_leagues()


@router.post("", response_model=LeagueOut)
async def create_league(payload: LeagueCreateIn, db: DbSession, current: CurrentAdmin) -> LeagueOut:
    return await LeagueService(db).create_league(payload, actor=current)


@router.get("/{league_id}", response_model=LeagueOut)
async def get_league(league_id: int, db: DbSession, current: CurrentMember) -> LeagueOut:
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
    db: DbSession, current: CurrentMember,
) -> LeagueOut:
    return await LeagueService(db).set_match_schedule(league_id, match_id, payload, actor=current)


@router.put("/{league_id}/matches/{match_id}/result", response_model=LeagueOut)
async def set_match_result(
    league_id: int, match_id: int, payload: LeagueMatchResultIn,
    db: DbSession, current: CurrentMember,
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

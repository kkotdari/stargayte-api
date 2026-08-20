from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 큰 글 칸 — MySQL의 TEXT는 **64KB**라 트랙이 안 들어간다(자취 한 판이 3.7MB까지 간다).
# sqlite는 Text에 길이 제한이 없어 그대로 쓴다.
BigText = Text().with_variant(LONGTEXT, "mysql")

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class GameResult(AuditMixin, TimestampMixin, Base):
    __tablename__ = "game_results"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 사람이 보고 지목하기 위한 고유번호 — 등록 순서(id)가 아니라 "그 경기가 실제로 언제
    # 열렸는지"를 기준으로 한다(리플레이는 한참 지나서야 등록되는 경우가 흔해서, id 순서가
    # 실제 경기 순서와 어긋난다). 형식: YYMMDDHHMMSS(리플레이가 있으면 실제 시작 시각(KST),
    # 없으면 경기 날짜 + 000000) + 2자리 일련번호(00부터, 같은 초/같은 날짜가 겹치면 01, 02...
    # 로 늘어난다 — 하루/한 초에 100건이 몰릴 일은 없다고 가정). service.py의 생성 로직
    # 참고. 한 번 배정되면 이후 수정에서도 절대 바뀌지 않는다.
    match_no: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 게임 상세(페이지) 조회수(요청) — 페이지가 열릴 때마다 1씩 는다. 기존 DB에는
    # main.py의 멱등 ALTER가 넣는다.
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 경기유형 코드 (0101=1:1, 0102=팀전). team1/team2 인원수와 별개로
    # 어떤 성격의 경기인지 분류하기 위한 값이라 컬럼으로 따로 관리한다.
    match_type: Mapped[str] = mapped_column(String(4), nullable=False, default="0101")

    participants: Mapped[list["GameResultParticipant"]] = relationship(
        back_populates="game_result",
        cascade="all, delete-orphan",
        order_by="GameResultParticipant.position",
    )
    # 결과(승패/맵/시작시각/경기시간) — 얇은 사이드 테이블로 분리해 관리한다(모든 경기가
    # 등록과 동시에 결과를 함께 저장하므로 실질적으로 항상 1:1로 존재한다). 리플레이(.rep)도
    # "실제로 어떻게 끝났는가"에 속하는 정보라 여기(GameOutcome.replay)에 매달린다 —
    # GameResult 자신은 리플레이를 직접 참조하지 않는다.
    result_row: Mapped["GameOutcome | None"] = relationship(
        back_populates="game_result",
        cascade="all, delete-orphan",
        uselist=False,
    )
    # created_by는 AuditMixin이 제공하는 컬럼이라 이 클래스 본문에서 바로 이름을 못 쓰므로
    # 문자열로 지연 참조한다. 작성자 표시/삭제 권한 판단에 쓰고, 여기서 쓰지는 않는다(viewonly).
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="GameResult.created_by", viewonly=True
    )


class GameResultParticipant(AuditMixin, Base):
    __tablename__ = "game_result_participants"
    __table_args__ = (UniqueConstraint("match_id", "team", "position"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("game_results.id", ondelete="CASCADE"), nullable=False
    )
    team: Mapped[str] = mapped_column(String(5), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 실제 게임에서 쓰인 플레이어 이름(리플레이 파싱 원본 게임 아이디, 또는 수기등록 시
    # 고른 이름) — 절대 NULL이 될 수 없다(수기등록도 드롭다운에서 기존 이름을 고르거나,
    # 새 이름이면 회원/비회원/컴퓨터 중 하나로 즉시 분류해야만 등록이 끝난다). 회원
    # 여부/식별은 이 행에 저장하지 않고 매번 replay_aliases(raw_name → kind/member_pk)로
    # 조회해서 판단한다 — member_pk 컬럼을 따로 두면 회원이 여러 게임 아이디를 쓸 수 있는
    # 것과 이중 관리가 되어 어긋날 여지가 생긴다. 회원의 members.battletag는
    # 나중에 바뀔 수 있는 값이라, 그것만 믿으면 이 경기 시점에 실제로 어떤 게임 아이디로
    # 참가했는지 알 수 없게 된다 — 이 컬럼이 그 시점의 진짜 값을 영구 보존한다.
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    race: Mapped[str] = mapped_column(String(20), nullable=False)
    # 아래 4개는 리플레이 파싱으로만 채워진다 (수동 등록 참가자는 항상 NULL).
    apm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eapm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cmd_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_cmd_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 리플레이 커맨드 스트림에서 센 '생산' 지표 — 유닛 훈련/건물 건설/변태(저그) 커맨드
    # 총합이다(build order 규모). apm 4형제와 마찬가지로 리플레이 파싱으로만 채워지고
    # 수동 등록/과거 데이터는 NULL이다. 프론트 replayParser가 세서 슬롯에 실어 보낸다.
    build_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 그 '생산'을 갈래별로 나눈 값(요청: 통계 생산 칸에 도넛 셋 + 초반 일꾼 수) — 건물
    # 생산/방어, 병력 기본/고급/마법, 지상/공중, 5분까지의 일꾼 수. 총량 하나로는 "많이
    # 했다"까지밖에 못 말해서 구성을 따로 싣는다. 갈래가 늘거나 이름이 바뀔 수 있어 컬럼을
    # 쪼개지 않고 JSON 한 칸에 담는다(집계는 파이썬에서 더한다 — 통계 한 번에 도는 행이
    # 수천 건 규모라 DB에서 굳이 풀 이유가 없다). 프론트 replayBuildMix가 세서 보낸다.
    build_mix: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    game_result: Mapped[GameResult] = relationship(back_populates="participants")


class Replay(AuditMixin, TimestampMixin, Base):
    """업로드된 리플레이(.rep) 파일 한 건. 경기 결과(match_results.replay_id)가 이 행을
    가리키며 실제 파일과 매핑된다. 원본 파일명과 알아보기 쉬운 생성 파일명, 시작시각/맵 등
    풀 메타데이터를 보존한다(요청)."""

    __tablename__ = "replays"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 업로드된 원본 파일명 / 알아보기 쉬운 생성 파일명(둘 다 보존).
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 리플레이 시작 시각 / 맵 이름 — 파싱해서 함께 저장하는 풀 메타데이터.
    game_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    map_name: Mapped[str | None] = mapped_column(String(150), nullable=True)


class ReplayMap(TimestampMixin, Base):
    """미니맵을 그리는 데 필요한 맵의 지형 격자 — 리플레이 안에 들어 있는 타일 격자다.

    경기가 아니라 '맵'에 매달아 둔다(지적: 같은 맵을 반복해서 쓰니 동일하면 하나를 함께
    쓰면 된다). 클럽은 빠른무한 몇 종류를 계속 돌리므로, 경기마다 넣으면 같은 격자
    수백 벌이 쌓인다. 그래서 격자 내용 자체의 해시(map_hash)를 키로 한 번만 저장하고
    경기(GameOutcome.map_hash)가 그걸 가리킨다 — 맵 이름이 아니라 내용이 기준인 이유는,
    같은 이름으로 조금씩 다른 판본이 돌아다니고(센포금지/빠른무한 …) 반대로 이름만 바꾼
    같은 맵도 있어서다.

    지형 그래픽(tileset의 cv5/vx4/vr4·팔레트)은 게임 설치본에 있는 저작물이라 여기 없다 —
    그래서 이 격자로 그리는 건 게임과 같은 색의 미니맵이 아니라 '타일 종류를 색으로 구분한
    개략도'다. 실제 확인: 타일 그룹 번호만으로도 본진·램프·중앙 광장이 또렷하게 나온다.
    """

    __tablename__ = "replay_maps"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 격자 내용의 해시(프론트가 계산해 보낸다) — 같은 맵을 두 번 저장하지 않게 하는 열쇠다.
    map_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # 그 맵을 처음 올린 리플레이에 적혀 있던 이름. 표시용이 아니라 사람이 DB를 볼 때의
    # 단서다(화면에 쓰는 맵 이름은 경기마다 저장된 GameOutcome.map_name이다).
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # 타일 단위 맵 크기(보통 128×128).
    width: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    height: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 이 맵에 나오는 타일 그룹 번호 목록 — tiles의 각 바이트가 이 배열의 첨자다. 원본
    # 번호를 그대로 쓰지 않고 한 겹 접는 이유는 한 맵에 서른 몇 종류뿐이라 1바이트에 담기고,
    # 그래야 격자가 크기의 4분의 1로 줄어든다(실측: JSON 63KB → base64 22KB).
    palette: Mapped[list] = mapped_column(JSON, nullable=False)
    # width*height개의 팔레트 첨자를 바이트로 늘어놓고 base64로 옮긴 것.
    tiles: Mapped[str] = mapped_column(Text, nullable=False)
    # 자원 지대([타일x, 타일y, 가스여부]) — 앞마당·멀티 자리. 옛 데이터엔 없어 nullable.
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 사람이 올려 둔 실제 미니맵 그림(MinimapImage)을 가리킨다 — 있으면 격자 대신 그걸 그린다.
    # 여러 맵 행이 같은 그림을 가리킬 수 있다(요청: 버전·이름만 다른 거의 같은 맵을 한데 묶기).
    image_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("minimap_images.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 맵연결 기록(요청: 게임 상세에서 아무나 미니맵 그림을 골라 연결 — 누가 언제 마지막으로
    # 연결했는지 회원 pk와 함께 남긴다). 운영자 제어판의 일괄 매핑은 이 기록을 안 건드린다.
    # FK는 members.pk다(수리) — members.id는 로그인 문자열(String)이고 pk가 숫자 식별자.
    linked_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="SET NULL"), nullable=True
    )
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MinimapImage(TimestampMixin, Base):
    """사람이 올려 둔 실제 미니맵 그림 한 장.

    리플레이의 타일 격자로는 게임과 같은 색의 미니맵을 만들 수 없다 — 타일 번호를 픽셀로
    바꾸는 표와 그림이 게임 설치본에 있고 리플레이엔 없다(ReplayMap 주석). 번호만으로 물·풀·
    땅·벽을 갈라 보려는 시도를 네 번 했고 다 실패했다(빈도·응집도·순위·그룹 덩어리). 그래서
    운영자가 맵마다 실제 미니맵 그림을 한 번 올려 두고, 그 위에 아바타·화살표를 얹는다(요청).

    그림을 replay_maps에 직접 넣지 않고 따로 두는 이유: 이름이나 판본만 다른 거의 같은 맵이
    여러 벌 있어서(빠른무한 계열) 그것들이 한 그림을 함께 가리켜야 한다(요청: "거의 비슷한데
    버전이나 이름이 다른 경우도 한데 묶을 수 있어야").
    """

    __tablename__ = "minimap_images"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 운영자가 붙이는 이름 — 제어판 목록에서 이 그림이 어느 맵인지 알아보는 이름이다.
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # data URL 그대로("data:image/png;base64,..."). 파일 저장소를 쓰지 않는 이유는 장 수가
    # 맵 종류 수(십여 장)뿐이고, 한 벌을 받아 두면 계속 쓰기 때문이다.
    image: Mapped[str] = mapped_column(Text, nullable=False)
    # 같은 그림의 작은 판(512px, data URL) — 목록에 실어 보내는 것은 이쪽이다(지적:
    # "미니맵 배경이 화질이 너무 안좋아"를 고치려고 image를 2048px로 키웠는데, 그대로
    # 두면 활동 목록 한 화면이 맵 종류 수만큼 2048px을 나른다). 재생 화면이 실제로
    # 크게 그릴 때만 ?full=1로 원본을 따로 받는다. 옛 행은 NULL이라 image로 되돌아간다.
    thumb: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 지형(이동 가능/불가) 격자 - 프론트가 그림을 분석해 만들고 운영자가 검수/수정한 값
    # (요청). JSON 문자열이고, 없으면 프론트가 그때그때 색으로 어림한다.
    walk: Mapped[str | None] = mapped_column(Text, nullable=True)


class GameOutcome(Base):
    """경기 결과 — status가 completed로 확정된 경기에만 이 행이 존재한다(예약/취소 상태는
    애초에 결과가 없으므로 행 자체가 없다). 리플레이 메타데이터(맵/시작시각/경기시간)도
    "실제로 어떻게 끝났는가"에 속하는 정보라 matches가 아니라 여기로 옮겼다."""

    __tablename__ = "game_outcomes"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("game_results.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    result: Mapped[str] = mapped_column(String(10), nullable=False)
    # 아래 3개는 리플레이 파싱으로만 채워진다 (수동 등록 경기는 항상 NULL).
    map_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    game_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 이 경기가 치러진 맵의 지형 격자(replay_maps.map_hash) — 미니맵을 그리는 데 쓴다.
    # 외래키를 걸지 않는 이유는 격자가 순전히 파생 데이터라서다: 맵 행이 없어도 경기는
    # 온전하고(미니맵만 안 나온다), 반대로 어떤 경기도 안 가리키는 맵 행이 남아도 무해하다.
    # 리플레이가 없는 수기 등록 경기와 옛 데이터는 NULL이다.
    map_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 리플레이(.rep) — 별도 replays 테이블에 풀 메타데이터로 저장하고, 결과는 replay_id로
    # 그 파일에 매핑한다. single_parent+delete-orphan이라 결과 행을 지우면 리플레이 행도
    # 함께 지워진다(파일 삭제는 서비스에서 처리). 수기 경기는 리플레이가 없어 nullable.
    replay_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("replays.id"), unique=True, nullable=True
    )
    replay: Mapped["Replay | None"] = relationship(
        foreign_keys=[replay_id], single_parent=True, cascade="all, delete-orphan",
    )

    game_result: Mapped[GameResult] = relationship(back_populates="result_row")

class GameResultUnitTracks(TimestampMixin, Base):
    """개체 트랙(v2) — 유닛 태그 단위 분석 결과를 경기와 별도 테이블에 담는다.

    요청: 기존 부대 어림(옛 요약 데이터의 motion)과 나란히 두고 비교해 보게 별도
    테이블로. 리플레이 명령의 100%가 선택 태그에 귀속됨을 실측으로 확인한 뒤의
    새 파이프라인이다 — 프론트가 등록 때 계산해 올리고, 재생 화면의 '개체' 토글이
    읽는다. 내용은 프론트 소유의 JSON 문자열이라 서버는 열어 보지 않는다."""

    __tablename__ = "game_result_unit_tracks"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    game_result_id: Mapped[int] = mapped_column(
        ForeignKey("game_results.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    # v2 트랙 JSON 문자열 — 4:4 한 판 실측 원시 173KB, 접으면 100KB 안쪽.
    data: Mapped[str] = mapped_column(BigText, nullable=False)


class GameResultMotionTracks(TimestampMixin, Base):
    """참값 자취 — 서버가 리플레이를 **실제로 시뮬레이션해** 뽑은 유닛의 자리·방향·상태.

    개체 트랙(game_result_unit_tracks)과 자리를 나눈 이유: 그쪽은 사건(명령·연구·마법)을
    담고 프론트가 만들지만, 이쪽은 자리를 담고 **서버만** 만든다. 한 칸에 같이 두면 한쪽을
    쓸 때 다른 쪽이 덮인다.

    여태 자리는 브라우저가 개체 트랙 위에서 시뮬을 돌려 얻었다 — 경기를 열 때마다, 폰마다.
    이제 서버가 한 번 구워 두면 모두가 그것을 받는다(openbw/README.md).

    내용은 덤퍼가 낸 조밀 이진(zlib)을 base64로 담은 것이다. 서버는 열어 보지 않는다 —
    푸는 곳은 프론트 한 곳뿐이다(src/utils/openbwTracks.ts).
    """

    __tablename__ = "game_result_motion_tracks"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    game_result_id: Mapped[int] = mapped_column(
        ForeignKey("game_results.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
    )
    # 실측 0.8~3.8MB(판 길이·사람 수에 따라). 상한은 스키마와 같은 12MB로 본다.
    data: Mapped[str] = mapped_column(BigText, nullable=False)

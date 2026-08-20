"""리플레이를 실제로 시뮬레이션해 참값 트랙을 굽는다.

여태 유닛 트랙은 프론트가 **리플레이 커맨드에서 유추해** 화면에서 만들었다. 리플레이에는
유닛이 안 들어 있어서(사람이 누른 커맨드뿐) "이 번호가 무슨 유닛인가"를 증거로 좁히는
길이었고, 정답표가 없어 얼마나 틀렸는지 잴 수조차 없었다.

OpenBW는 그 경기를 **그대로 돌린다**. 프레임마다 유닛의 참 자리·방향·상태가 나오므로
유추할 것이 없다. 그걸 여기서 구워 두면 폰은 받아서 풀기만 하면 된다.

굽기가 안 되는 판도 있다 — 컴퓨터 플레이어가 낀 경기는 OpenBW에 컴퓨터 AI가 없어 원리상
못 돌린다. 그런 판은 덤퍼가 스스로 "믿을 구간 0"이라고 말하고, 프론트는 예전 길로 돌아간다.

바이너리(`bwdump`)나 게임 자료가 없으면 **조용히 건너뛴다.** 굽기는 곁다리 기능이라
없다고 앱이 뜨지 못하면 안 된다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 트랙 한 판이 차지할 수 있는 자리. 실측은 0.8~3.8MB인데(31분 4:4가 가장 컸다) 자리·체력
# ·업그레이드·마법·핑이 다 실린 뒤의 수라 여기서 더 크게 잡는다. 이상하게 커진 판을
# 저장 전에 걸러 내는 것이 목적이지, 정상인 판을 막는 자가 아니다.
MAX_TRACK_CHARS = 12_000_000

# 한 번에 몇 판까지 동시에 구울 것인가. 재분석은 수백 판을 여러 갈래로 몰아치는데, 굽기는
# 판마다 CPU 한 코어를 몇십 초 붙잡는다 — 안 막으면 서버가 그동안 응답을 못 한다.
# 두 판까지만 겹치게 두고 나머지는 줄을 세운다.
_bake_gate = asyncio.Semaphore(2)


def _bin_path() -> Path:
    return Path(settings.openbw_bin)


def _data_path() -> Path:
    return Path(settings.openbw_data_root)


def is_available() -> bool:
    """지금 이 서버에서 구울 수 있는가 — 바이너리와 게임 자료가 다 있어야 한다."""
    if not settings.openbw_enabled:
        return False
    if not _bin_path().is_file():
        return False
    # 자료 폴더에 실제로 쓰는 파일이 들어 있는지까지 본다(빈 폴더만 만들어 둔 경우를 거른다).
    return (_data_path() / "arr" / "units.dat").is_file()


def unavailable_reason() -> str:
    """왜 못 굽는지 — 수동 굽기 요청에 그대로 돌려준다."""
    if not settings.openbw_enabled:
        return "참값 굽기가 꺼져 있습니다(OPENBW_ENABLED)."
    if not _bin_path().is_file():
        return f"덤퍼가 없습니다: {settings.openbw_bin}"
    if not (_data_path() / "arr" / "units.dat").is_file():
        return f"게임 자료가 없습니다: {settings.openbw_data_root}/arr/units.dat"
    return ""


async def bake(replay_file: Path) -> str | None:
    """리플레이 하나를 굽는다. 트랙을 base64 문자열로 주고, 못 구우면 None.

    덤퍼는 조밀 이진(zlib)을 그대로 stdout에 낸다 — 서버는 옮겨 담기만 하고, 푸는 것은
    프론트 한 곳뿐이다(`src/utils/openbwTracks.ts`). 꼴은 그 파일 머리말에 적혀 있다.
    """
    if not is_available():
        logger.info("참값 굽기 건너뜀 — %s", unavailable_reason())
        return None
    if not replay_file.is_file():
        logger.warning("참값 굽기 건너뜀 — 리플레이 파일이 없다: %s", replay_file)
        return None

    args = [
        str(_bin_path()),
        str(_data_path()),
        str(replay_file),
        str(settings.openbw_step),
        "--tracks",
        "--bin",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:  # 실행 자체가 안 되는 경우(권한·아키텍처)
        logger.warning("참값 굽기 실패 — 덤퍼를 못 돌렸다: %s", exc)
        return None

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=settings.openbw_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("참값 굽기 실패 — %d초를 넘겼다: %s", settings.openbw_timeout_seconds, replay_file.name)
        return None

    if proc.returncode != 0 or not out:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        logger.warning("참값 굽기 실패(코드 %s) — %s", proc.returncode, tail[0][:200])
        return None

    encoded = base64.b64encode(out).decode("ascii")
    if len(encoded) > MAX_TRACK_CHARS:
        logger.warning(
            "참값 굽기 버림 — 트랙이 %.1fMB로 너무 크다: %s",
            len(encoded) / 1_048_576,
            replay_file.name,
        )
        return None
    logger.info("참값 구움 — %s · %.2fMB", replay_file.name, len(encoded) / 1_048_576)
    return encoded


async def bake_quietly(match_id: int) -> None:
    """등록·수정 뒤 **뒤에서** 굽는다 — 실패해도 조용히 넘긴다.

    굽기는 곁다리다. 경기 등록이 굽기 때문에 느려지거나 실패하면 안 되므로, 요청이 끝난
    뒤에 따로 세션을 열어 돌리고 무슨 일이 나든 로그만 남긴다. 아직 안 구워진 경기는
    프론트가 예전처럼 화면에서 유추한 트랙으로 그린다.
    """
    if not is_available():
        return
    # 순환 임포트를 피하려고 여기서 늦게 들여온다(service가 이 모듈을 쓴다).
    from app.db.session import AsyncSessionLocal
    from app.domain.game_results.service import GameResultService
    from app.storage import get_storage

    try:
        async with _bake_gate:
            async with AsyncSessionLocal() as session:
                await GameResultService(session, get_storage()).bake_unit_tracks(match_id)
    except Exception as exc:  # noqa: BLE001 — 굽기 실패가 등록을 흔들면 안 된다
        logger.info("자동 참값 굽기 건너뜀(경기 %s) — %s", match_id, exc)

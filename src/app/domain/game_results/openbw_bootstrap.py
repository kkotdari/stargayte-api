"""게임 자료를 볼륨에 처음 한 번 올리는 문.

덤퍼(`bwdump`)는 스타크래프트 자료 955개(16MB)가 있어야 돈다. 이 자료는 블리자드 저작물
이라 **저장소에도 이미지에도 안 담는다**(openbw/README.md '자료 파일과 법'). 그러면 운영
서버의 볼륨에 넣을 길이 필요한데, Railway 볼륨은 밖에서 파일을 밀어 넣을 방법이 없다.

그래서 문을 하나 낸다. 다만 **영구히 열어 둘 구멍이 아니다.** 지키는 선:

* `OPENBW_BOOTSTRAP_TOKEN`이 비어 있으면 문이 아예 **없다**(404). 올릴 때만 채워서
  배포하고, 다 올린 뒤 도로 비운다.
* 쓰기 전용이다. 어떤 API도 이 파일을 **돌려주지 않는다**(README 규칙 2).
* 받는 것은 tar.gz 하나뿐이고, 들어 있는 이름을 전부 검사한다 — 볼륨 밖으로 나가는
  경로(`..`·절대경로·심볼릭 링크)는 하나라도 있으면 통째로 거절한다.
* 갈래는 덤퍼가 실제로 읽는 다섯 곳만 받는다. 그 밖의 것은 조용히 버린다.
"""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.core.config import settings

logger = logging.getLogger(__name__)

# 덤퍼가 여는 갈래(재 봤다 — README '자료 파일과 법'):
#   arr 116K · scripts 40K · triggers 8K · Tileset 2.3M · unit 13M
# unit/**/*.grp가 짐의 대부분인데, 그림을 그리려는 게 아니라 개체 테두리 크기를 파일
# 머리말에서 읽으려고 연다. 그래서 뺄 수가 없다.
ALLOWED_TOPS = frozenset({"arr", "scripts", "triggers", "Tileset", "unit"})

# 풀었을 때 총 크기 상한. 실제 자료가 16MB라 넉넉하다 — zip bomb을 여기서 끊는다.
MAX_TOTAL_BYTES = 96 * 1024 * 1024
# 파일 하나 상한. 가장 큰 것이 380K다.
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 4000


class BootstrapRejected(Exception):
    """묶음이 규칙을 어겼다 — 한 파일도 안 쓰고 통째로 거절한다."""


def is_open() -> bool:
    """문이 열려 있나 — 토큰이 설정돼 있을 때만."""
    return bool(settings.openbw_bootstrap_token)


def _is_junk(leaf: str) -> bool:
    """자료가 아닌 곁다리 — 맥에서 묶으면 파일마다 딸려 온다.

    맥의 tar는 확장 속성을 `._이름`이라는 **따로 된 파일**로 함께 넣는다(AppleDouble).
    955개를 묶었는데 1925개가 깔리던 것이 이것이었다. 덤퍼는 이름을 콕 집어 열기 때문에
    있어도 안 읽지만, 볼륨에 쓰레기를 두 배로 쌓을 이유가 없다.
    """
    return leaf.startswith("._") or leaf == ".DS_Store"


def _safe_name(name: str) -> PurePosixPath | None:
    """묶음 속 이름을 검사해 볼륨 안 상대경로로 바꾼다. 수상하면 None."""
    if not name or name.startswith("/") or "\\" in name:
        return None
    parts = PurePosixPath(name).parts
    # tar는 흔히 앞에 "./"를 붙인다 — 그것만 걷어 낸다.
    parts = tuple(p for p in parts if p != ".")
    if not parts or any(p == ".." for p in parts):
        return None
    if parts[0] not in ALLOWED_TOPS:
        return None
    if _is_junk(parts[-1]):
        return None
    return PurePosixPath(*parts)


def install(stream: BinaryIO) -> dict[str, int]:
    """tar.gz를 풀어 자료 폴더에 깐다. 쓴 파일 수와 바이트를 돌려준다.

    한 파일이라도 규칙을 어기면 **아무것도 안 쓰고** 던진다 — 반쯤 깔린 자료는 덤퍼가
    엉뚱하게 죽는 자리라, 깔릴 거면 온전히 깔려야 한다.
    """
    root = Path(settings.openbw_data_root).resolve()

    try:
        tar = tarfile.open(fileobj=stream, mode="r:gz")
    except tarfile.TarError as exc:
        raise BootstrapRejected(f"tar.gz로 못 읽었습니다: {exc}") from exc

    # 1단계 — 전부 검사만 한다(아직 한 바이트도 안 쓴다).
    plan: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    total = 0
    with tar:
        for info in tar:
            if info.isdir():
                continue
            if not info.isfile():
                # 심볼릭·하드 링크·장치 파일 — 볼륨 밖을 가리킬 수 있다.
                raise BootstrapRejected(f"보통 파일이 아닌 것이 들어 있습니다: {info.name}")
            rel = _safe_name(info.name)
            if rel is None:
                # 갈래 밖(초상화·소리 따위)은 버리고, 경로가 수상하면 거절한다.
                if info.name.startswith("/") or ".." in PurePosixPath(info.name).parts:
                    raise BootstrapRejected(f"볼륨 밖을 가리키는 이름입니다: {info.name}")
                continue
            if info.size > MAX_FILE_BYTES:
                raise BootstrapRejected(f"파일 하나가 너무 큽니다: {info.name} ({info.size}바이트)")
            total += info.size
            if total > MAX_TOTAL_BYTES:
                raise BootstrapRejected("풀면 96MB를 넘습니다")
            if len(plan) >= MAX_FILES:
                raise BootstrapRejected(f"파일이 {MAX_FILES}개를 넘습니다")
            plan.append((info, rel))

        if not plan:
            raise BootstrapRejected(
                "쓸 파일이 하나도 없습니다 — 묶음 안이 arr/·scripts/·triggers/·Tileset/·unit/로 "
                "시작해야 합니다(tar czf bwdata.tgz -C <자료폴더> . 로 묶으세요)"
            )

        # 2단계 — 검사를 다 통과했으니 이제 쓴다.
        written = 0
        for info, rel in plan:
            src = tar.extractfile(info)
            if src is None:
                raise BootstrapRejected(f"내용을 못 읽었습니다: {info.name}")
            dst = root / Path(*rel.parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src, dst.open("wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
            written += 1

    logger.info("게임 자료를 깔았다 — %d개 · %.1fMB · %s", written, total / 1_048_576, root)
    return {"files": written, "bytes": total}

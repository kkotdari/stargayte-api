"""게임 자료 올리기 — 볼륨 부트스트랩(openbw_bootstrap.py 머리말).

`/game-results` 밑이 아니라 따로 선 것은 길 충돌 때문이다. 거기 밑에는 `/{match_id}`가
있어서 `/game-results/openbw-data`가 경기번호로 읽힌다.

묶음은 **본문에 그대로** 받는다(multipart가 아니다) — 파일 하나를 받자고 python-multipart를
들이지 않는다. 받는 동안 상한을 넘으면 그 자리에서 끊는다.
"""

from __future__ import annotations

import secrets
import tempfile

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings
from app.domain.game_results import openbw, openbw_bootstrap

router = APIRouter(prefix="/openbw", tags=["openbw"])

# 받는 동안의 상한(눌린 채로). 실제 묶음이 6.9MB다 — 넉넉히 준다.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


@router.post("/data", status_code=status.HTTP_200_OK)
async def install_data(
    request: Request,
    x_bootstrap_token: str = Header(default=""),
) -> dict[str, object]:
    """자료 묶음(tar.gz)을 볼륨에 깐다 — 서버를 세울 때 **한 번만** 쓴다.

        tar czf bwdata.tgz -C tools/openbw/data .
        curl -X POST https://<서버>/api/openbw/data \\
             -H "X-Bootstrap-Token: <OPENBW_BOOTSTRAP_TOKEN>" \\
             -H "Content-Type: application/gzip" \\
             --data-binary @bwdata.tgz

    다 올렸으면 `OPENBW_BOOTSTRAP_TOKEN`을 비워서 문을 닫는다. 토큰이 비어 있으면 이 길은
    **없는 길**이다(404) — 안 열려 있다는 것조차 안 알린다.
    """
    if not openbw_bootstrap.is_open():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # compare_digest — 토큰을 앞에서부터 맞춰 보며 알아내는 길을 막는다.
    if not secrets.compare_digest(x_bootstrap_token, settings.openbw_bootstrap_token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # 디스크로 흘려 받는다 — 6.9MB라 메모리에 들어도 되지만, 상한이 64MB라 그건 안 된다.
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as buf:
        got_bytes = 0
        async for chunk in request.stream():
            got_bytes += len(chunk)
            if got_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"묶음이 {MAX_UPLOAD_BYTES // 1024 // 1024}MB를 넘습니다",
                )
            buf.write(chunk)
        if got_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="본문이 비었습니다 — curl은 --data-binary @묶음.tgz 로 보내세요",
            )
        buf.seek(0)
        try:
            got = openbw_bootstrap.install(buf)
        except openbw_bootstrap.BootstrapRejected as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 깔고 나서 실제로 구울 수 있게 됐는지까지 말해 준다 — 올린 사람이 곧바로 알아야 한다.
    ready = openbw.is_available()
    return {
        "files": got["files"],
        "bytes": got["bytes"],
        "ready": ready,
        "reason": "" if ready else openbw.unavailable_reason(),
    }

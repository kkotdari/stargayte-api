"""숨겨진 제어판 잠금 비밀번호(env_vars.admin_panel_password) 검증 엔드포인트."""

from app.domain.env_vars.models import EnvVar
from app.domain.env_vars.service import ADMIN_PANEL_PASSWORD_KEY


async def _signup(client, member_id: str = "player01") -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id, "password": "pass1234", "battletag": "Tag#1001",
            "replayAliases": [member_id], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_verify_admin_panel_password_matches_db_value(client, db_session):
    db_session.add(EnvVar(key=ADMIN_PANEL_PASSWORD_KEY, value="0701"))
    await db_session.commit()

    signup = await _signup(client)
    headers = {"Authorization": f"Bearer {signup['accessToken']}"}

    res = await client.post(
        "/api/env-vars/admin-panel/verify", headers=headers, json={"password": "0701"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}


async def test_verify_admin_panel_password_rejects_wrong_value(client, db_session):
    db_session.add(EnvVar(key=ADMIN_PANEL_PASSWORD_KEY, value="0701"))
    await db_session.commit()

    signup = await _signup(client)
    headers = {"Authorization": f"Bearer {signup['accessToken']}"}

    res = await client.post(
        "/api/env-vars/admin-panel/verify", headers=headers, json={"password": "wrong"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": False}


async def test_verify_admin_panel_password_fails_closed_when_unseeded(client, db_session):
    """행 자체가 없으면(시드 누락 등) 무조건 실패한다 — 빈 문자열 등으로 통과되면 안 된다."""
    signup = await _signup(client)
    headers = {"Authorization": f"Bearer {signup['accessToken']}"}

    res = await client.post(
        "/api/env-vars/admin-panel/verify", headers=headers, json={"password": ""}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": False}


async def test_verify_admin_panel_password_requires_login(client):
    res = await client.post("/api/env-vars/admin-panel/verify", json={"password": "0701"})
    assert res.status_code == 401

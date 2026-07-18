"""버전 레지스트리(app_versions) — 등록된 버전 목록 조회 + 등록된 버전으로만 배포."""


async def _signup_admin(client) -> str:
    """첫 회원은 자동으로 운영자(admin)가 된다 — 그 accessToken을 돌려준다."""
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": "player01", "password": "pass1234", "battletag": "Tag#1001",
            "replayAliases": ["player01"], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


async def test_list_versions_returns_seeded_registry(client):
    token = await _signup_admin(client)
    res = await client.get("/api/app-versions", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert [v["number"] for v in res.json()] == ["1", "2", "3"]


async def test_set_version_to_registered_succeeds(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "3"})
    assert res.status_code == 200, res.text
    assert res.json() == {"activeVersion": "3"}
    # 다시 조회해도 반영돼 있어야 한다.
    got = await client.get("/api/app-version", headers=headers)
    assert got.json() == {"activeVersion": "3"}


async def test_set_version_to_unregistered_is_rejected(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    # 형식은 valid(숫자)지만 레지스트리에 없는 "9" — 400으로 막혀야 한다.
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "9"})
    assert res.status_code == 400, res.text
    # 활성 버전은 그대로(기본 "1")여야 한다.
    got = await client.get("/api/app-version", headers=headers)
    assert got.json() == {"activeVersion": "1"}


async def test_set_version_rejects_bad_format(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "v3"})
    assert res.status_code == 422, res.text

"""凭据目录脱敏与卡片字段的原子保存契约。"""

from core import provider_keys as pk


async def test_api_batch_save_and_safe_list(tmp_path, monkeypatch):
    monkeypatch.setattr(pk, "_cache", None)
    monkeypatch.setattr(pk, "_cache_mtime", -1)
    monkeypatch.setattr(pk, "_registry", {})
    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "provider_keys.json"))
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from web.routers.provider_keys import router

    pk.register_provider_key("sample", "sound", "Sample", fields=(pk.CredentialField("token"),))
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put("/provider-keys/sample", json={"values": {"token": "secret-test-value"}})).status_code == 200
        response = await client.get("/provider-keys")
        assert "secret-test-value" not in response.text
        assert response.json()["providers"][0]["configured"]
        assert (await client.put("/provider-keys/sample", json={"values": {"unknown": "bad"}})).status_code == 400
        assert (await client.put("/provider-keys/sample", json={"values": {"token": ""}})).status_code == 200

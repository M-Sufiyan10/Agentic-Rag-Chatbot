"""Tests for the /api/conversations endpoints."""

import httpx

from tests.conftest import register_user

URL = "/api/conversations"


async def _create(
    client: httpx.AsyncClient, headers: dict[str, str], **body: str
) -> dict[str, str]:
    r = await client.post(URL, json=body, headers=headers)
    assert r.status_code == 201, r.text
    data: dict[str, str] = r.json()
    return data


async def test_create_defaults_title_new_chat(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    r = await client.post(URL, json={}, headers=headers)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "New chat"
    assert {"id", "created_at", "updated_at"} <= body.keys()


async def test_list_only_returns_own_conversations(client: httpx.AsyncClient) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    a = await _create(client, alice, title="alice chat")
    b = await _create(client, bob, title="bob chat")

    alice_list = (await client.get(URL, headers=alice)).json()
    bob_list = (await client.get(URL, headers=bob)).json()

    assert [c["id"] for c in alice_list] == [a["id"]]
    assert [c["id"] for c in bob_list] == [b["id"]]


async def test_list_ordered_by_updated_at_desc(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    first = await _create(client, headers, title="first")
    second = await _create(client, headers, title="second")
    ids = [c["id"] for c in (await client.get(URL, headers=headers)).json()]
    assert ids == [second["id"], first["id"]]

    # Renaming the older one bumps updated_at, so it becomes the most recent.
    r = await client.patch(f"{URL}/{first['id']}", json={"title": "first renamed"}, headers=headers)
    assert r.status_code == 200
    ids = [c["id"] for c in (await client.get(URL, headers=headers)).json()]
    assert ids == [first["id"], second["id"]]


async def test_rename_conversation(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    conv = await _create(client, headers)
    r = await client.patch(f"{URL}/{conv['id']}", json={"title": "  Renamed  "}, headers=headers)
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"


async def test_rename_rejects_blank_title(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    conv = await _create(client, headers)
    r = await client.patch(f"{URL}/{conv['id']}", json={"title": "   "}, headers=headers)
    assert r.status_code == 422


async def test_other_users_conversation_is_404(client: httpx.AsyncClient) -> None:
    alice = await register_user(client, email="alice@example.com")
    bob = await register_user(client, email="bob@example.com")
    conv = await _create(client, alice)

    patch = await client.patch(f"{URL}/{conv['id']}", json={"title": "hijack"}, headers=bob)
    delete = await client.delete(f"{URL}/{conv['id']}", headers=bob)

    assert patch.status_code == 404
    assert delete.status_code == 404
    still = (await client.get(URL, headers=alice)).json()
    assert [c["title"] for c in still] == ["New chat"]


async def test_delete_conversation(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    conv = await _create(client, headers)
    r = await client.delete(f"{URL}/{conv['id']}", headers=headers)
    assert r.status_code == 204
    assert (await client.get(URL, headers=headers)).json() == []

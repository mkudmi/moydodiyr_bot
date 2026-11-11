import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from moydodyr.domain import MOSCOW
from moydodyr.miniapp.server import create_app, order_data
from tests.test_welcome import load_catalog

TOKEN = "123456:TEST_TOKEN"


def init_data(user_id=42, age=0):
    fields = {
        "auth_date": str(int((datetime.now(timezone.utc) - timedelta(seconds=age)).timestamp())),
        "user": json.dumps({"id": user_id, "first_name": "Тест"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def sample():
    return {
        "draft_id": str(uuid4()),
        "object": "flat",
        "service": "regular",
        "area": "50,5",
        "extras": ["windows"],
        "date": (datetime.now(MOSCOW).date() + timedelta(days=1)).isoformat(),
        "time": "",
        "address": "Москва, Тестовая 1",
        "name": "Ирина",
        "phone": "+7 999 123-45-67",
        "comment": "",
        "photos": [],
    }


@pytest.mark.asyncio
async def test_api_checks_telegram_auth_and_recalculates_on_server():
    db = SimpleNamespace(
        catalog=AsyncMock(return_value=load_catalog()),
        draft_photos_exist=AsyncMock(return_value=True),
        submit=AsyncMock(return_value={"id": 123}),
    )
    settings = SimpleNamespace(token=TOKEN, manager_chat_id=-100, webhook_enabled=False)
    async with TestClient(TestServer(create_app(db, settings))) as client:
        assert (await client.get("/api/catalog")).status == 401
        assert (
            await client.get("/api/catalog", headers={"X-Telegram-Init-Data": init_data(age=90000)})
        ).status == 401
        headers = {"X-Telegram-Init-Data": init_data()}
        payload = sample()
        payload["estimate"] = {"total": 1}
        response = await client.post("/api/submit", headers=headers, json=payload)
        assert response.status == 200
        assert (await response.json())["order_id"] == 123
        args = db.submit.await_args.args
        assert args[0] == 42
        assert args[1]["estimate"]["total"] == 4995
        assert args[1]["phone"] == "+79991234567"
        assert args[1]["time"] == "Любое время"
        assert args[1]["telegram_user"] == 42
        db.draft_photos_exist.return_value = False
        response = await client.post("/api/submit", headers=headers, json=payload)
        assert response.status == 400
        assert db.submit.await_count == 1


@pytest.mark.asyncio
async def test_photo_upload_is_owned_and_format_checked():
    db = SimpleNamespace(archive_photo=AsyncMock())
    settings = SimpleNamespace(token=TOKEN)
    async with TestClient(TestServer(create_app(db, settings))) as client:
        headers = {"X-Telegram-Init-Data": init_data(), "X-Draft-Id": str(uuid4())}
        response = await client.post("/api/photo", headers=headers, data=b"not an image")
        assert response.status == 400
        response = await client.post(
            "/api/photo", headers=headers, data=b"\x89PNG\r\n\x1a\nexample"
        )
        assert response.status == 200
        assert (await response.json())["file_id"].startswith("miniapp:")
        assert db.archive_photo.await_args.args[0] == 42
        assert db.archive_photo.await_args.args[1] == headers["X-Draft-Id"]


def test_invalid_or_disabled_service_and_duplicate_photos():
    catalog = load_catalog()
    data = sample()
    catalog["services"]["regular"]["enabled"] = False
    with pytest.raises(ValueError):
        order_data(data, catalog, 42)
    catalog["services"]["regular"]["enabled"] = True
    data["photos"] = ["miniapp:same", "miniapp:same"]
    with pytest.raises(ValueError):
        order_data(data, catalog, 42)

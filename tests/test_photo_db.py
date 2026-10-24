import hashlib
from unittest.mock import AsyncMock

import pytest

from moydodyr.bot.services.photo_archive import backfill_order_photos
from tests.test_customer_db import draft
from tests.test_customer_db import isolated_db as isolated_db


async def test_binary_photos_crm_metadata_and_owner_scope(isolated_db):
    db = isolated_db
    data = draft()
    content = b"\xff\xd8\xffstored-jpeg"
    archived = await db.archive_photo(
        42,
        data["draft_id"],
        "photo-file",
        content,
        "image/jpeg",
        unique_id="photo-unique",
        width=800,
        height=600,
    )
    again = await db.archive_photo(42, data["draft_id"], "photo-file", content, "image/jpeg")
    assert archived["id"] == again["id"]
    data["photos"] = ["photo-file"]
    order = await db.submit(42, data, -123, "Принята", webhook_payload={"name": "Тест"})
    photos = await db.customer_order_photos(42, order["id"])
    assert len(photos) == 1
    photo = await db.customer_order_photo(42, order["id"], str(archived["id"]))
    assert photo["content"] == content
    assert photo["sha256"] == hashlib.sha256(content).hexdigest()
    assert photo["file_size"] == len(content)
    assert await db.customer_order_photos(99, order["id"]) == []
    assert await db.customer_order_photo(99, order["id"], str(archived["id"])) is None
    foreign = await db.archive_photo(99, data["draft_id"], "photo-file", content, "image/jpeg")
    assert await db.link_order_photo(42, order["id"], 0, str(foreign["id"])) == "UPDATE 0"
    assert await db.customer_order_photo(42, order["id"], str(foreign["id"])) is None
    lead = await db.pool.fetchval(
        "SELECT payload->'lead' FROM outbox WHERE dedupe_key=$1", f"n8n:{order['id']}"
    )
    assert lead["photos"][0]["photo_id"] == str(archived["id"])
    assert lead["photos"][0]["content_type"] == "image/jpeg"
    assert lead["telegram_user_id"] == 42
    assert "content" not in lead["photos"][0]
    duplicate = await db.submit(42, data, -123, "Принята")
    assert duplicate["id"] == order["id"]
    assert len(await db.customer_order_photos(42, order["id"])) == 1


async def test_existing_order_backfill_is_repeatable(isolated_db, screen_env):
    db = isolated_db
    bot, _, _, _ = screen_env
    data = draft()
    data["photos"] = ["legacy-file"]
    order = await db.submit(42, data, -123, "Принята")
    assert (await db.customer_order_photos(42, order["id"]))[0]["photo_id"] is None
    assert await backfill_order_photos(bot, db) == (1, 0)
    assert await backfill_order_photos(bot, db) == (0, 0)
    metadata = (await db.customer_order_photos(42, order["id"]))[0]
    photo = await db.customer_order_photo(42, order["id"], str(metadata["photo_id"]))
    assert photo["content"] == b"\xff\xd8\xfftest-image"


async def test_linking_photos_rolls_back_with_failed_order(isolated_db, monkeypatch):
    db = isolated_db
    data = draft()
    data["photos"] = ["file"]
    archived = await db.archive_photo(
        42, data["draft_id"], "file", b"\xff\xd8\xfftest", "image/jpeg"
    )
    monkeypatch.setattr(db, "enqueue", AsyncMock(side_effect=RuntimeError("test failure")))
    with pytest.raises(RuntimeError, match="test failure"):
        await db.submit(42, data, -123, "Принята")
    assert await db.pool.fetchval("SELECT count(*) FROM orders") == 0
    assert await db.pool.fetchval("SELECT count(*) FROM order_photos") == 0
    assert (await db.draft_photo(42, data["draft_id"], "file"))["id"] == archived["id"]

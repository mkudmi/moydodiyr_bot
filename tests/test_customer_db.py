import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.domain import MOSCOW


@pytest_asyncio.fixture
async def isolated_db():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("RUN_DB_TESTS=1 enables tests in a disposable PostgreSQL database")
    url = Settings.load().database_url
    name = "test_moydodyr_" + uuid4().hex
    admin = await asyncpg.connect(url)
    db = None
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        parts = urlsplit(url)
        db = await Database.connect(urlunsplit(parts._replace(path="/" + name)))
        yield db
    finally:
        if db:
            await db.pool.close()
        # Only the randomly named database created by this fixture is removed.
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


def draft(days=3):
    start = datetime.now(MOSCOW) + timedelta(days=days)
    return {
        "draft_id": str(uuid4()),
        "date": start.date().isoformat(),
        "time": "13:00–15:00",
        "service": "regular",
        "object": "flat",
        "area": "65",
        "photos": [],
        "extras": [],
    }


async def test_owner_scoping_registration_pagination_and_atomic_cancellation(
    isolated_db, monkeypatch
):
    db = isolated_db
    # Apply the production schema again to verify that the migration is repeatable.
    await db.pool.execute(Path("moydodyr/schema.sql").read_text(encoding="utf-8"))
    await db.register_user(42, "campaign")
    await db.register_user(42)
    assert await db.pool.fetchval("SELECT source FROM users WHERE id=42") == "campaign"
    orders = [await db.submit(42, draft(), -123, "Принята") for _ in range(7)]
    foreign = await db.submit(99, draft(), -123, "Принята")
    assert await db.pool.fetchval("SELECT count(*) FROM users WHERE id=99") == 1
    assert await db.customer_order(42, foreign["id"]) is None
    assert await db.customer_order_history(42, foreign["id"]) == []
    first = await db.customer_orders(42, True, limit=5)
    second = await db.customer_orders(42, True, offset=5, limit=5)
    assert len(first) == 5 and len(second) == 2
    assert {row["id"] for row in first}.isdisjoint(row["id"] for row in second)
    assert all(row["user_id"] == 42 for row in first + second)

    target = orders[0]["id"]
    assert (await db.cancel_customer_order(99, target, -123))[0] is False
    results = await asyncio.gather(
        db.cancel_customer_order(42, target, -123),
        db.cancel_customer_order(42, target, -123),
    )
    assert sum(ok for ok, _ in results) == 1
    assert (await db.customer_order(42, target))["status"] == "cancelled"
    events = await db.customer_order_history(42, target)
    assert len(events) == 1
    assert events[0]["actor_user_id"] == 42
    assert events[0]["manager_id"] is None
    assert (
        await db.pool.fetchval(
            "SELECT count(*) FROM outbox WHERE dedupe_key=$1", f"customer-cancel:{target}"
        )
        == 1
    )
    assert target in {row["id"] for row in await db.customer_orders(42, False)}
    assert target not in {row["id"] for row in await db.customer_orders(42, True)}
    assert not (await db.transition(target, 123, "working"))[0]

    late = draft()
    start = datetime.now(MOSCOW) + timedelta(hours=23)
    late.update(date=start.date().isoformat(), time=start.strftime("%H:%M"))
    late_order = await db.submit(42, late, -123, "Принята")
    assert not (await db.cancel_customer_order(42, late_order["id"], -123))[0]
    assert (await db.customer_order(42, late_order["id"]))["status"] == "new"
    assert await db.customer_order_history(42, late_order["id"]) == []

    # Cancellation and its notification roll back together if enqueueing fails.
    target = orders[1]["id"]
    monkeypatch.setattr(db, "enqueue", AsyncMock(side_effect=RuntimeError("test failure")))
    with pytest.raises(RuntimeError, match="test failure"):
        await db.cancel_customer_order(42, target, -123)
    assert (await db.customer_order(42, target))["status"] == "new"
    assert await db.customer_order_history(42, target) == []
    monkeypatch.undo()

    future_agreed = orders[2]["id"]
    past_agreed = (await db.submit(42, draft(days=-3), -123, "Принята"))["id"]
    await db.pool.execute(
        "UPDATE orders SET status='agreed' WHERE id=ANY($1::bigint[])", [future_agreed, past_agreed]
    )
    active_ids = {row["id"] for row in await db.customer_orders(42, True, limit=100)}
    history_ids = {row["id"] for row in await db.customer_orders(42, False, limit=100)}
    assert future_agreed in active_ids and future_agreed not in history_ids
    assert past_agreed in history_ids and past_agreed not in active_ids

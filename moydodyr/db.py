import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from moydodyr.domain import MOSCOW, customer_can_cancel, validate_catalog

ROOT = Path(__file__).resolve().parent.parent


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, url: str):
        async def setup(conn):
            await conn.set_type_codec(
                "jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )

        pool = await asyncpg.create_pool(url, min_size=1, max_size=5, init=setup)
        db = cls(pool)
        try:
            await pool.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
            validate_catalog(catalog)
            await pool.execute(
                "INSERT INTO bot_settings(key,value) VALUES('catalog',$1) ON CONFLICT DO NOTHING",
                catalog,
            )
        except BaseException:
            await pool.close()
            raise
        return db

    async def catalog(self):
        return await self.pool.fetchval("SELECT value FROM bot_settings WHERE key='catalog'")

    async def change_catalog(self, change):
        async with self.pool.acquire() as conn, conn.transaction():
            catalog = await conn.fetchval(
                "SELECT value FROM bot_settings WHERE key='catalog' FOR UPDATE"
            )
            change(catalog)
            validate_catalog(catalog)
            await conn.execute("UPDATE bot_settings SET value=$1 WHERE key='catalog'", catalog)

    async def register_user(self, user_id: int, source: str = "Прямой переход"):
        await self.pool.execute(
            "INSERT INTO users(id,source) VALUES($1,$2) ON CONFLICT DO NOTHING", user_id, source
        )

    async def draft_photo(self, user_id: int, draft_id: str, file_id: str):
        return await self.pool.fetchrow(
            "SELECT id,telegram_file_id,telegram_unique_id,content_type,"
            "file_size,sha256,width,height "
            "FROM photo_files WHERE user_id=$1 AND draft_id=$2 AND telegram_file_id=$3",
            user_id,
            UUID(draft_id),
            file_id,
        )

    async def archive_photo(
        self,
        user_id: int,
        draft_id: str,
        file_id: str,
        content: bytes,
        content_type: str,
        unique_id=None,
        width=None,
        height=None,
    ):
        if not 0 < len(content) <= 20 * 1024 * 1024:
            raise ValueError("Invalid photo size")
        await self.register_user(user_id)
        return await self.pool.fetchrow(
            "INSERT INTO photo_files(id,user_id,draft_id,telegram_file_id,telegram_unique_id,"
            "content_type,file_size,sha256,width,height,content) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
            "ON CONFLICT(user_id,draft_id,telegram_file_id) DO UPDATE "
            "SET telegram_file_id=EXCLUDED.telegram_file_id "
            "RETURNING id,telegram_file_id,telegram_unique_id,content_type,"
            "file_size,sha256,width,height",
            uuid4(),
            user_id,
            UUID(draft_id),
            file_id,
            unique_id,
            content_type,
            len(content),
            hashlib.sha256(content).hexdigest(),
            width,
            height,
            content,
        )

    async def customer_order_photos(self, user_id: int, order_id: int):
        return await self.pool.fetch(
            "SELECT p.position,p.telegram_file_id,p.photo_id,f.content_type,f.file_size,f.sha256,"
            "f.width,f.height FROM order_photos p JOIN orders o ON o.id=p.order_id "
            "LEFT JOIN photo_files f ON f.id=p.photo_id AND f.user_id=o.user_id "
            "WHERE o.id=$1 AND o.user_id=$2 ORDER BY p.position",
            order_id,
            user_id,
        )

    async def customer_order_photo(self, user_id: int, order_id: int, photo_id: str):
        return await self.pool.fetchrow(
            "SELECT f.* FROM photo_files f JOIN order_photos p ON p.photo_id=f.id "
            "JOIN orders o ON o.id=p.order_id "
            "WHERE o.id=$1 AND o.user_id=$2 AND f.user_id=$2 AND f.id=$3",
            order_id,
            user_id,
            UUID(photo_id),
        )

    async def unarchived_order_photos(
        self, limit: int = 100, after_order_id: int = 0, after_position: int = -1
    ):
        return await self.pool.fetch(
            "SELECT p.order_id,p.position,p.telegram_file_id,o.user_id,o.draft_id "
            "FROM order_photos p JOIN orders o ON o.id=p.order_id "
            "WHERE p.photo_id IS NULL AND (p.order_id,p.position)>($2,$3) "
            "ORDER BY p.order_id,p.position LIMIT $1",
            limit,
            after_order_id,
            after_position,
        )

    async def link_order_photo(self, user_id: int, order_id: int, position: int, photo_id: str):
        return await self.pool.execute(
            "UPDATE order_photos p SET photo_id=f.id FROM orders o,photo_files f "
            "WHERE p.order_id=o.id AND o.id=$1 AND o.user_id=$2 AND f.user_id=$2 "
            "AND f.draft_id=o.draft_id AND f.telegram_file_id=p.telegram_file_id "
            "AND p.position=$3 AND f.id=$4",
            order_id,
            user_id,
            position,
            UUID(str(photo_id)),
        )

    async def enqueue(self, conn, key: str, chat: int, payload: dict):
        await conn.execute(
            "INSERT INTO outbox(dedupe_key,chat_id,payload) VALUES($1,$2,$3) "
            "ON CONFLICT DO NOTHING",
            key,
            chat,
            payload,
        )

    async def submit(
        self,
        user_id: int,
        draft: dict,
        manager_chat: int,
        accepted: str,
        webhook_payload: dict | None = None,
        receipt_screen: dict | None = None,
    ):
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO users(id,source) VALUES($1,'Прямой переход') ON CONFLICT DO NOTHING",
                user_id,
            )
            source = await conn.fetchval("SELECT source FROM users WHERE id=$1", user_id)
            inserted = await conn.fetchrow(
                "INSERT INTO orders(draft_id,user_id,source,payload) VALUES($1,$2,$3,$4) "
                "ON CONFLICT(draft_id) DO NOTHING RETURNING *",
                UUID(draft["draft_id"]),
                user_id,
                source,
                draft,
            )
            if inserted is None:
                return await conn.fetchrow(
                    "SELECT * FROM orders WHERE draft_id=$1", UUID(draft["draft_id"])
                )
            order_id = inserted["id"]
            attachments = []
            for position, file_id in enumerate(draft.get("photos", [])[:5]):
                photo = await conn.fetchrow(
                    "SELECT id,content_type,file_size,sha256,width,height FROM photo_files "
                    "WHERE user_id=$1 AND draft_id=$2 AND telegram_file_id=$3",
                    user_id,
                    UUID(draft["draft_id"]),
                    file_id,
                )
                await conn.execute(
                    "INSERT INTO order_photos(order_id,position,telegram_file_id,photo_id) "
                    "VALUES($1,$2,$3,$4)",
                    order_id,
                    position,
                    file_id,
                    photo["id"] if photo else None,
                )
                attachment = {
                    "photo_id": str(photo["id"]) if photo else None,
                    "position": position,
                    "file_id": file_id,
                }
                if photo:
                    attachment.update(
                        {
                            key: photo[key]
                            for key in ("content_type", "file_size", "sha256", "width", "height")
                        }
                    )
                attachments.append(attachment)
            if webhook_payload is not None:
                webhook_payload["order_id"] = order_id
                webhook_payload["source"] = source
                webhook_payload["telegram_user_id"] = user_id
                webhook_payload["photos"] = attachments
                await self.enqueue(
                    conn,
                    f"n8n:{order_id}",
                    manager_chat,
                    {"kind": "webhook", "order_id": order_id, "lead": webhook_payload},
                )
            await self.enqueue(
                conn, f"order:{order_id}", manager_chat, {"kind": "order", "order_id": order_id}
            )
            for index, photo in enumerate(draft.get("photos", [])):
                await self.enqueue(
                    conn,
                    f"order:{order_id}:photo:{index}",
                    manager_chat,
                    {"kind": "photo", "photo": photo, "caption": f"Фото к заявке №{order_id}"},
                )
            await self.enqueue(
                conn,
                f"order:{order_id}:receipt",
                user_id,
                {
                    "kind": "text",
                    "text": f"Заявка №{order_id} принята!\n\n{accepted}",
                    "screen": {key: receipt_screen[key] for key in ("message_id", "nonce")}
                    if receipt_screen
                    else None,
                },
            )
            return inserted

    async def order(self, order_id: int):
        return await self.pool.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)

    async def customer_orders(self, user_id: int, active: bool, offset: int = 0, limit: int = 6):
        return await self.pool.fetch(
            "SELECT * FROM orders WHERE user_id=$1 AND "
            "((status IN ('new','working') OR (status='agreed' "
            "AND COALESCE(payload->>'date','') >= $3)) = $2) "
            "ORDER BY created_at DESC,id DESC LIMIT $4 OFFSET $5",
            user_id,
            active,
            datetime.now(MOSCOW).date().isoformat(),
            limit,
            offset,
        )

    async def customer_order(self, user_id: int, order_id: int):
        return await self.pool.fetchrow(
            "SELECT * FROM orders WHERE id=$1 AND user_id=$2",
            order_id,
            user_id,
        )

    async def customer_order_history(self, user_id: int, order_id: int):
        return await self.pool.fetch(
            "SELECT h.* FROM order_history h JOIN orders o ON o.id=h.order_id "
            "WHERE o.id=$1 AND o.user_id=$2 ORDER BY h.created_at DESC,h.id DESC LIMIT 20",
            order_id,
            user_id,
        )

    async def cancel_customer_order(self, user_id: int, order_id: int, manager_chat: int):
        async with self.pool.acquire() as conn, conn.transaction():
            order = await conn.fetchrow(
                "SELECT * FROM orders WHERE id=$1 AND user_id=$2 FOR UPDATE",
                order_id,
                user_id,
            )
            if order is None:
                return False, "Заявка не найдена."
            if order["status"] == "cancelled":
                return False, "Эта заявка уже отменена."
            if not customer_can_cancel(order):
                return False, "Отмена недоступна: до уборки должно оставаться не менее 24 часов."
            await conn.execute(
                "UPDATE orders SET status='cancelled',updated_at=now() WHERE id=$1", order_id
            )
            await conn.execute(
                "INSERT INTO order_history(order_id,actor_user_id,status) "
                "VALUES($1,$2,'cancelled')",
                order_id,
                user_id,
            )
            await self.enqueue(
                conn,
                f"customer-cancel:{order_id}",
                manager_chat,
                {"kind": "order_cancelled", "order_id": order_id},
            )
            return True, "Заявка отменена."

    async def new_orders(self, limit: int = 10):
        return await self.pool.fetch(
            "SELECT * FROM orders WHERE status='new' ORDER BY created_at DESC LIMIT $1", limit
        )

    async def working_orders(self, limit: int = 10):
        return await self.pool.fetch(
            "SELECT * FROM orders WHERE status='working' ORDER BY updated_at DESC LIMIT $1", limit
        )

    async def transition(self, order_id: int, manager: int, status: str):
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM orders WHERE id=$1 FOR UPDATE", order_id)
            if row is None:
                return False, "Заявка не найдена."
            if status == "working":
                if row["status"] != "new":
                    return False, "Заявка уже взята в работу или закрыта."
            elif status in {"agreed", "cancelled"}:
                if row["status"] != "working" or row["manager_id"] != manager:
                    return (
                        False,
                        "Изменить статус может ответственный менеджер после взятия в работу.",
                    )
            else:
                return False, "Недопустимый статус."
            await conn.execute(
                "UPDATE orders SET status=$2,manager_id=$3,updated_at=now() WHERE id=$1",
                order_id,
                status,
                manager,
            )
            await conn.execute(
                "INSERT INTO order_history(order_id,manager_id,status) VALUES($1,$2,$3)",
                order_id,
                manager,
                status,
            )
            return True, "Статус сохранён."

    async def next_notification(self):
        return await self.pool.fetchrow(
            "WITH candidate AS (SELECT id FROM outbox WHERE delivered_at IS NULL "
            "AND failed_at IS NULL AND attempts < 10 AND available_at <= now() ORDER BY id "
            "FOR UPDATE SKIP LOCKED LIMIT 1) "
            "UPDATE outbox SET attempts=attempts+1,available_at=now()+interval '5 minutes' "
            "WHERE id=(SELECT id FROM candidate) RETURNING *"
        )

    async def mark_delivered(self, notification_id: int):
        await self.pool.execute(
            "UPDATE outbox SET delivered_at=now(),last_error=NULL WHERE id=$1", notification_id
        )

    async def defer_notification(self, notification_id: int, error: str):
        await self.pool.execute(
            "UPDATE outbox SET available_at=now()+interval '5 minutes',last_error=$2 WHERE id=$1",
            notification_id,
            error[:500],
        )

    async def fail_notification(self, notification_id: int, error: str):
        await self.pool.execute(
            "UPDATE outbox SET failed_at=now(),last_error=$2 WHERE id=$1",
            notification_id,
            error[:500],
        )

    async def save_external_lead_id(self, order_id: int, lead_id: str | int | None):
        if lead_id is not None:
            await self.pool.execute(
                "UPDATE orders SET external_lead_id=$2 WHERE id=$1", order_id, str(lead_id)
            )

    async def retarget_pending_manager_notifications(self, chat: int):
        return await self.pool.fetchval(
            "WITH moved AS (UPDATE outbox SET chat_id=$1, available_at=now() "
            "WHERE delivered_at IS NULL AND payload->>'kind' IN "
            "('order','photo','manager_message','order_cancelled') "
            "RETURNING id) SELECT count(*) FROM moved",
            chat,
        )

    async def enqueue_message(self, key: str, chat: int, payload: dict):
        await self.pool.execute(
            "INSERT INTO outbox(dedupe_key,chat_id,payload) VALUES($1,$2,$3) "
            "ON CONFLICT DO NOTHING",
            key,
            chat,
            payload,
        )

    async def enqueue_contact(
        self, key: str, chat: int, manager_payload: dict, webhook_lead: dict | None
    ):
        async with self.pool.acquire() as conn, conn.transaction():
            await self.enqueue(conn, key, chat, manager_payload)
            if webhook_lead is not None:
                await self.enqueue(
                    conn,
                    f"n8n:{key}",
                    chat,
                    {"kind": "webhook", "lead": webhook_lead},
                )


class PostgresStorage(BaseStorage):
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def key(key: StorageKey):
        return json.dumps(
            [
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id,
                key.business_connection_id,
                key.destiny,
            ]
        )

    async def set_state(self, key, state=None):
        value = state.state if hasattr(state, "state") else state
        await self.db.pool.execute(
            "INSERT INTO sessions(key,state) VALUES($1,$2) ON CONFLICT(key) DO UPDATE "
            "SET state=$2,updated_at=now()",
            self.key(key),
            value,
        )

    async def get_state(self, key):
        return await self.db.pool.fetchval("SELECT state FROM sessions WHERE key=$1", self.key(key))

    async def set_data(self, key, data):
        await self.db.pool.execute(
            "INSERT INTO sessions(key,data) VALUES($1,$2) ON CONFLICT(key) DO UPDATE "
            "SET data=$2,updated_at=now()",
            self.key(key),
            dict(data),
        )

    async def get_data(self, key):
        return (
            await self.db.pool.fetchval("SELECT data FROM sessions WHERE key=$1", self.key(key))
            or {}
        )

    async def close(self):
        pass  # The application owns and closes the shared connection pool.

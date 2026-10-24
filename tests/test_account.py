from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import EditMessageCaption, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, User

from moydodyr.bot.handlers.account import customer_action, show_customer_orders
from moydodyr.bot.services.screen import SCREEN_KEY
from moydodyr.domain import MOSCOW, cancellation_deadline, customer_can_cancel
from tests.test_welcome import load_catalog


def sample_order(order_id=7):
    return {
        "id": order_id,
        "user_id": 42,
        "status": "new",
        "created_at": datetime.now(MOSCOW),
        "payload": {
            "date": (datetime.now(MOSCOW) + timedelta(days=3)).date().isoformat(),
            "time": "13:00–15:00",
            "area": "65.5",
            "service": "regular",
            "object": "flat",
            "address": "Москва, ул. Тестовая, 1",
            "extras": ["windows"],
            "estimate": {"total": 9000},
            "comment": "Тест <текста>",
        },
    }


@pytest.mark.parametrize("time_text,hour", [("13:00–15:00", 13), ("18:30", 18), ("Любое время", 0)])
def test_cancellation_deadline_is_24_hours_before_start(time_text, hour):
    payload = {"date": "2026-10-27", "time": time_text}
    deadline = cancellation_deadline(payload)
    assert deadline == datetime(
        2026, 10, 26, hour, 30 if time_text == "18:30" else 0, tzinfo=MOSCOW
    )
    order = {"payload": payload, "status": "agreed"}
    assert customer_can_cancel(order, deadline)
    assert not customer_can_cancel(order, deadline + timedelta(microseconds=1))
    assert customer_can_cancel(order, deadline - timedelta(hours=1))
    assert not customer_can_cancel({**order, "status": "cancelled"}, deadline)


@pytest.mark.parametrize("payload", [{}, {"date": "bad"}, {"date": "2026-10-27", "time": "bad"}])
def test_missing_or_invalid_schedule_cannot_be_cancelled(payload):
    assert not customer_can_cancel({"payload": payload, "status": "new"})


async def test_account_paginates_and_uses_customer_id(screen_env):
    _, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    db = SimpleNamespace(
        register_user=AsyncMock(),
        customer_orders=AsyncMock(return_value=[sample_order(i) for i in range(1, 7)]),
        catalog=AsyncMock(return_value=load_catalog()),
    )
    await show_customer_orders(message, state, db, 42)
    db.register_user.assert_awaited_once_with(42)
    db.customer_orders.assert_awaited_once_with(42, active=True, offset=0)
    edit = session.calls[-1]
    assert isinstance(edit, EditMessageCaption)
    callbacks = [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row]
    assert "client:list:a:1" in callbacks
    assert sum(data.startswith("client:detail:") for data in callbacks) == 5
    assert "Открыть заявку №6" not in str(edit.reply_markup)


async def setup_account(screen_env, owner=True):
    bot, session, state, message = screen_env
    await state.update_data(**{SCREEN_KEY: {"message_id": 100, "photo": True}})
    order = sample_order()
    db = SimpleNamespace(
        register_user=AsyncMock(),
        catalog=AsyncMock(return_value=load_catalog()),
        customer_orders=AsyncMock(return_value=[order]),
        customer_order=AsyncMock(return_value=order if owner else None),
        customer_order_history=AsyncMock(return_value=[]),
        cancel_customer_order=AsyncMock(),
    )
    settings = SimpleNamespace(manager_chat_id=-123)

    async def click(data):
        query = CallbackQuery(
            id="account",
            chat_instance="chat",
            from_user=message.from_user,
            data=data,
            message=message.model_copy(
                update={
                    "message_id": 100,
                    "from_user": User(id=bot.id, is_bot=True, first_name="Bot"),
                }
            ),
        ).as_(bot)
        await customer_action(query, state, db, settings)

    return session, state, order, db, click


@pytest.mark.parametrize("action", ["detail", "cancel", "confirm"])
async def test_foreign_order_never_exposes_details_or_cancels(screen_env, action):
    session, _, _, db, click = await setup_account(screen_env, owner=False)
    await click(f"client:{action}:7:a:0")
    db.customer_order.assert_awaited_once_with(42, 7)
    db.customer_order_history.assert_not_awaited()
    db.cancel_customer_order.assert_not_awaited()
    assert not any(isinstance(call, (EditMessageCaption, SendMessage)) for call in session.calls)


async def test_cancellation_needs_confirmation_and_cannot_be_replayed(screen_env):
    session, state, order, db, click = await setup_account(screen_env)
    await click("client:confirm:7:a:0:000000000000")
    db.cancel_customer_order.assert_not_awaited()
    await click("client:cancel:7:a:0")
    pending = (await state.get_data())[SCREEN_KEY]["cancel_confirmation"]

    async def cancel(*args):
        order["status"] = "cancelled"
        return True, "Заявка отменена."

    db.cancel_customer_order.side_effect = cancel
    callback = f"client:confirm:7:a:0:{pending['token']}"
    await click(callback)
    db.cancel_customer_order.assert_awaited_once_with(42, 7, -123)
    assert "Статус: Отменена" in session.calls[-1].caption
    assert "Отменить заявку" not in str(session.calls[-1].reply_markup)
    await click(callback)
    assert db.cancel_customer_order.await_count == 1
    assert not any(isinstance(call, (SendMessage, SendPhoto)) for call in session.calls)


async def test_leaving_confirmation_invalidates_old_button(screen_env):
    _, state, _, db, click = await setup_account(screen_env)
    await click("client:cancel:7:a:0")
    token = (await state.get_data())[SCREEN_KEY]["cancel_confirmation"]["token"]
    await click("client:detail:7:a:0")
    await click(f"client:confirm:7:a:0:{token}")
    db.cancel_customer_order.assert_not_awaited()


async def test_expired_order_has_no_cancel_button(screen_env):
    session, _, order, db, click = await setup_account(screen_env)
    order["payload"]["date"] = datetime.now(MOSCOW).date().isoformat()
    await click("client:detail:7:a:0")
    assert "Отменить заявку" not in str(session.calls[-1].reply_markup)
    await click("client:cancel:7:a:0")
    db.cancel_customer_order.assert_not_awaited()

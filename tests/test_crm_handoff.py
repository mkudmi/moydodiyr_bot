import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery

from moydodyr.bot.formatting import customer_order_text
from moydodyr.bot.handlers.group import order_action
from moydodyr.bot.services.outbox import deliver_outbox
from tests.test_account import sample_order
from tests.test_welcome import load_catalog


async def test_old_manager_callback_redirects_to_crm(screen_env):
    bot, session, _, message = screen_env
    query = CallbackQuery(
        id="old-manager-button",
        chat_instance="test",
        from_user=message.from_user,
        message=message,
        data="order:7:take",
    ).as_(bot)
    await order_action(query, SimpleNamespace(admin_ids={42}))
    assert "CRM" in session.calls[-1].text
    assert session.calls[-1].show_alert


async def test_new_order_notification_has_no_management_buttons(screen_env):
    bot, session, _, _ = screen_env
    order = {**sample_order(), "manager_id": None, "source": "Тест"}
    db = SimpleNamespace(
        next_notification=AsyncMock(
            side_effect=[
                {"id": 1, "chat_id": -123, "payload": {"kind": "order", "order_id": 7}},
                asyncio.CancelledError(),
            ]
        ),
        order=AsyncMock(return_value=order),
        catalog=AsyncMock(return_value=load_catalog()),
        mark_delivered=AsyncMock(),
    )
    with pytest.raises(asyncio.CancelledError):
        await deliver_outbox(bot, db, None)
    assert isinstance(session.calls[-1], SendMessage)
    assert session.calls[-1].reply_markup is None
    db.mark_delivered.assert_awaited_once_with(1)


def test_completed_order_shows_confirmed_schedule_and_price():
    order = sample_order()
    order.update(status="completed", crm={"date": order["payload"]["date"], "price": "12345.50"})
    text = customer_order_text(order, load_catalog(), [])
    assert "Выполнена" in text
    assert "Подтверждённый выезд" in text
    assert "12 345,5 ₽" in text

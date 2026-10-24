import html
import re
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from moydodyr.bot.formatting import ORDER_STATUSES, customer_order_text, display_date
from moydodyr.bot.services.screen import SCREEN_KEY, bind_screen, clear_flow, show_screen
from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.domain import cancellation_deadline, customer_can_cancel

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
PAGE_SIZE = 5


def button(text: str, data: str):
    return InlineKeyboardButton(text=text, callback_data=data)


async def show_customer_orders(
    message: Message, state: FSMContext, db: Database, user_id: int, scope: str = "a", page: int = 0
):
    await db.register_user(user_id)
    rows = await db.customer_orders(user_id, active=scope == "a", offset=page * PAGE_SIZE)
    if not rows and page:
        page = 0
        rows = await db.customer_orders(user_id, active=scope == "a", offset=0)
    catalog = await db.catalog()
    title = "Актуальные заявки" if scope == "a" else "История заявок"
    lines = [f"📋 Мои заявки · {title}", ""]
    keyboard = [[button("Актуальные", "client:list:a:0"), button("История", "client:list:h:0")]]
    for order in rows[:PAGE_SIZE]:
        service = (
            catalog["services"].get(order["payload"].get("service"), {}).get("title", "Уборка")
        )
        lines.append(
            f"№{order['id']} · {service}\n"
            f"{display_date(order['payload'].get('date'))} · {ORDER_STATUSES[order['status']]}"
        )
        keyboard.append(
            [
                button(
                    f"Открыть заявку №{order['id']}", f"client:detail:{order['id']}:{scope}:{page}"
                )
            ]
        )
    if not rows:
        lines.append("Здесь пока нет заявок. Оформить новую можно в главном меню.")
    else:
        lines += ["", "Выберите заявку, чтобы посмотреть подробности и доступные действия."]
    navigation = []
    if page:
        navigation.append(button("← Предыдущие", f"client:list:{scope}:{page - 1}"))
    if len(rows) > PAGE_SIZE:
        navigation.append(button("Следующие →", f"client:list:{scope}:{page + 1}"))
    if navigation:
        keyboard.append(navigation)
    keyboard.append([button("Обновить", f"client:list:{scope}:{page}")])
    await clear_flow(state)
    await show_screen(
        message,
        state,
        html.escape("\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def show_customer_order(
    message: Message,
    state: FSMContext,
    db: Database,
    user_id: int,
    order_id: int,
    scope: str,
    page: int,
    notice: str = "",
):
    order = await db.customer_order(user_id, order_id)
    if order is None:
        await show_customer_orders(message, state, db, user_id, scope, page)
        return
    history = await db.customer_order_history(user_id, order_id)
    text = customer_order_text(order, await db.catalog(), history)
    if notice:
        text = html.escape(notice) + "\n\n" + text
    keyboard = []
    if customer_can_cancel(order):
        deadline = cancellation_deadline(order["payload"])
        text += f"\n\nОтмена доступна до {deadline:%d.%m.%Y %H:%M} (МСК)."
        keyboard.append([button("Отменить заявку", f"client:cancel:{order_id}:{scope}:{page}")])
    elif order["status"] != "cancelled":
        text += (
            "\n\nСамостоятельная отмена доступна минимум за 24 часа до уборки. "
            "По другим вопросам свяжитесь с администратором."
        )
    keyboard += [
        [button("Обновить", f"client:detail:{order_id}:{scope}:{page}")],
        [button("← К списку заявок", f"client:list:{scope}:{page}")],
    ]
    await clear_flow(state)
    await show_screen(
        message, state, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.message(Command("orders"))
@router.message(F.text == "📋 Мои заявки")
async def my_orders(message: Message, state: FSMContext, db: Database):
    await show_customer_orders(message, state, db, message.from_user.id)


@router.callback_query(F.data.startswith("client:"))
async def customer_action(
    query: CallbackQuery, state: FSMContext, db: Database, settings: Settings
):
    if not isinstance(query.message, Message) or not await bind_screen(query.message, state):
        await query.answer("Используйте текущий экран или /start.")
        return
    data = query.data or ""
    match = re.fullmatch(r"client:list:([ah]):(\d{1,5})", data)
    if match:
        await query.answer()
        await show_customer_orders(
            query.message, state, db, query.from_user.id, match[1], int(match[2])
        )
        return
    match = re.fullmatch(
        r"client:(detail|cancel|confirm):(\d{1,19}):([ah]):(\d{1,5})(?::([a-f0-9]{12}))?", data
    )
    if not match:
        await query.answer("Неизвестное действие.")
        return
    action, raw_id, scope, raw_page, token = match.groups()
    order_id, page, user_id = int(raw_id), int(raw_page), query.from_user.id
    if order_id > 9223372036854775807:
        await query.answer("Заявка не найдена.")
        return
    order = await db.customer_order(user_id, order_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    if action == "detail":
        await query.answer()
        await show_customer_order(query.message, state, db, user_id, order_id, scope, page)
    elif action == "cancel":
        if not customer_can_cancel(order):
            await query.answer(
                "Отмена недоступна: нужно минимум 24 часа до уборки.", show_alert=True
            )
            return
        await query.answer()
        token = uuid4().hex[:12]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    button(
                        "Да, отменить заявку", f"client:confirm:{order_id}:{scope}:{page}:{token}"
                    )
                ],
                [button("Нет, оставить заявку", f"client:detail:{order_id}:{scope}:{page}")],
            ]
        )
        await clear_flow(state)
        await show_screen(
            query.message,
            state,
            f"Отменить заявку №{order_id}?\n\n"
            "Отмену нельзя будет вернуть. Вы сможете оформить новую заявку.",
            reply_markup=keyboard,
        )
        screen = (await state.get_data())[SCREEN_KEY]
        screen["cancel_confirmation"] = {"order_id": order_id, "token": token}
        await state.update_data(**{SCREEN_KEY: screen})
    else:
        screen = (await state.get_data()).get(SCREEN_KEY, {})
        if not token or screen.get("cancel_confirmation") != {"order_id": order_id, "token": token}:
            await query.answer("Откройте заявку и подтвердите отмену заново.", show_alert=True)
            return
        screen.pop("cancel_confirmation", None)
        await state.update_data(**{SCREEN_KEY: screen})
        await query.answer()
        ok, notice = await db.cancel_customer_order(user_id, order_id, settings.manager_chat_id)
        await show_customer_order(
            query.message,
            state,
            db,
            user_id,
            order_id,
            "h" if ok else scope,
            0 if ok else page,
            notice,
        )

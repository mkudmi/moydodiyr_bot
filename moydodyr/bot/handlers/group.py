from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from moydodyr.bot.formatting import order_text
from moydodyr.bot.keyboards import ADMIN_MENU
from moydodyr.config import Settings
from moydodyr.db import Database

group_router = Router()
group_router.message.filter(F.chat.type.in_({"group", "supergroup"}))


async def show_group_admin_panel(message: Message, settings: Settings):
    if (
        message.chat.id != settings.manager_chat_id
        or message.from_user.id not in settings.admin_ids
    ):
        await message.answer("Панель доступна администраторам в рабочей группе.")
        return
    await message.answer(
        "Панель менеджера «Мойдодыр»\n"
        "Новые заявки приходят сюда отдельными сообщениями. "
        "Для управления заявкой используйте кнопки под ней.",
        reply_markup=ADMIN_MENU,
    )


@group_router.message(CommandStart())
async def group_start(message: Message, settings: Settings):
    await show_group_admin_panel(message, settings)


@group_router.message(Command("admin"))
async def group_admin(message: Message, settings: Settings):
    await show_group_admin_panel(message, settings)


@group_router.message(F.text == "📬 Новые заявки")
async def group_new_orders(message: Message, db: Database, settings: Settings):
    if (
        message.chat.id != settings.manager_chat_id
        or message.from_user.id not in settings.admin_ids
    ):
        await message.answer("Недостаточно прав.")
        return
    orders = await db.new_orders(limit=10)
    catalog = await db.catalog()
    if not orders:
        await message.answer("Новых заявок нет.", reply_markup=ADMIN_MENU)
        return
    for order in orders:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Взять в работу", callback_data=f"order:{order['id']}:take"
                    )
                ]
            ]
        )
        await message.answer(order_text(order, catalog), reply_markup=keyboard)


@group_router.message(F.text == "🛠 Заявки в работе")
async def group_working_orders(message: Message, db: Database, settings: Settings):
    if (
        message.chat.id != settings.manager_chat_id
        or message.from_user.id not in settings.admin_ids
    ):
        await message.answer("Недостаточно прав.")
        return
    orders = await db.working_orders(limit=10)
    catalog = await db.catalog()
    if not orders:
        await message.answer("Заявок в работе нет.", reply_markup=ADMIN_MENU)
        return
    for order in orders:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Согласована", callback_data=f"order:{order['id']}:agree"
                    ),
                    InlineKeyboardButton(
                        text="Отменена", callback_data=f"order:{order['id']}:cancel"
                    ),
                ]
            ]
        )
        await message.answer(order_text(order, catalog), reply_markup=keyboard)


@group_router.message(F.text.in_({"⚙️ Настройки", "ℹ️ Команды администратора"}))
async def group_admin_help(message: Message, settings: Settings):
    if (
        message.chat.id != settings.manager_chat_id
        or message.from_user.id not in settings.admin_ids
    ):
        await message.answer("Недостаточно прав.")
        return
    await message.answer(
        "Настройки каталога доступны командами:\n"
        "/admin — справка по командам и тарифам\n"
        "Команды изменения настроек отправляйте в личном чате с ботом.",
        reply_markup=ADMIN_MENU,
    )


@group_router.callback_query(F.data.startswith("order:"))
async def order_action(query: CallbackQuery, db: Database, settings: Settings):
    if query.from_user.id not in settings.admin_ids:
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    _, raw_id, action = query.data.split(":")
    status = {"take": "working", "agree": "agreed", "cancel": "cancelled"}.get(action)
    if status is None:
        await query.answer("Недопустимое действие.", show_alert=True)
        return
    ok, note = await db.transition(int(raw_id), query.from_user.id, status)
    await query.answer(note, show_alert=not ok)
    if ok and query.message:
        order = await db.order(int(raw_id))
        catalog = await db.catalog()
        if status == "working":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Согласована", callback_data=f"order:{raw_id}:agree"
                        ),
                        InlineKeyboardButton(
                            text="Отменена", callback_data=f"order:{raw_id}:cancel"
                        ),
                    ]
                ]
            )
        else:
            keyboard = None
        await query.message.edit_text(order_text(order, catalog), reply_markup=keyboard)

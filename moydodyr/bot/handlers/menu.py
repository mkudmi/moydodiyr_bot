import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
)
from aiogram.utils.deep_linking import decode_payload

from moydodyr.bot.formatting import services_text
from moydodyr.bot.handlers.contact import contact_manager
from moydodyr.bot.handlers.orders import begin_form
from moydodyr.bot.keyboards import (
    MENU,
)
from moydodyr.bot.services.screen import (
    bind_screen,
    clear_flow,
    pages_for,
    remove_legacy_keyboard,
    show_screen,
)
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
WELCOME_PHOTO = Path(__file__).resolve().parents[3] / "assets" / "welcome.png"


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database):
    await remove_legacy_keyboard(message, state)
    await state.clear()
    source = "Прямой переход"
    if message.text and len(message.text.split(maxsplit=1)) > 1:
        raw = message.text.split(maxsplit=1)[1].strip()
        try:
            raw = decode_payload(raw)
        except (ValueError, UnicodeDecodeError):
            pass
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", raw):
            source = raw
    await db.register_user(message.from_user.id, source)
    catalog = await db.catalog()
    welcome = catalog["welcome"]
    sent = await message.answer_photo(
        FSInputFile(WELCOME_PHOTO),
        caption=pages_for(welcome, 1024)[0],
        reply_markup=MENU,
        parse_mode=None,
    )
    await bind_screen(sent, state)
    await show_screen(message, state, welcome, reply_markup=MENU)


@router.message(Command("cancel"))
@router.message(F.text == "↩️ Отмена")
async def cancel(message: Message, state: FSMContext):
    await clear_flow(state)
    await show_screen(message, state, "Действие отменено. Выберите действие:", reply_markup=MENU)


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext):
    await clear_flow(state)
    await show_screen(message, state, "Главное меню:", reply_markup=MENU)


@router.message(F.text == "🧹 Услуги и цены")
async def services(message: Message, db: Database, state: FSMContext):
    catalog = await db.catalog()
    await show_screen(
        message,
        state,
        services_text(catalog),
        reply_markup=MENU,
        image="services",
    )


@router.message(F.text == "🔥 Акции")
async def promotions(message: Message, db: Database, state: FSMContext):
    await show_screen(message, state, (await db.catalog())["promotions"], reply_markup=MENU)


@router.message(F.text == "⭐ Отзывы")
async def reviews(message: Message, db: Database, state: FSMContext):
    await show_screen(message, state, (await db.catalog())["reviews"], reply_markup=MENU)


@router.message(F.text == "❓ Частые вопросы")
async def faq(message: Message, db: Database, state: FSMContext):
    await show_screen(message, state, (await db.catalog())["faq"], reply_markup=MENU)


@router.callback_query(
    F.data.in_(
        {
            "menu:estimate",
            "menu:order",
            "menu:services",
            "menu:promotions",
            "menu:reviews",
            "menu:faq",
            "menu:contact",
            "menu:home",
        }
    )
)
async def menu_action(query: CallbackQuery, state: FSMContext, db: Database):
    if not isinstance(query.message, Message):
        await query.answer()
        return
    if not await bind_screen(query.message, state):
        await query.answer("Это старое меню. Используйте последнее сообщение или /start.")
        return
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action in {"estimate", "order"}:
        await begin_form(query.message, state, db, mode=action)
    elif action == "contact":
        await clear_flow(state)
        await contact_manager(query.message, state)
    elif action == "home":
        await clear_flow(state)
        await show_screen(query.message, state, (await db.catalog())["welcome"], reply_markup=MENU)
    else:
        handlers = {"services": services, "promotions": promotions, "reviews": reviews, "faq": faq}
        await clear_flow(state)
        await handlers[action](query.message, db, state)

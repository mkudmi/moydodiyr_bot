import html
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.deep_linking import decode_payload

from moydodyr.bot.handlers.contact import contact_manager
from moydodyr.bot.handlers.orders import begin_form
from moydodyr.bot.keyboards import (
    MENU,
)
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
WELCOME_PHOTO = Path(__file__).resolve().parents[3] / "assets" / "welcome.png"


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database):
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
    await message.answer("🫧 Наведём порядок вместе!", reply_markup=ReplyKeyboardRemove())
    if len(welcome.encode("utf-16-le")) // 2 <= 1024:
        await message.answer_photo(FSInputFile(WELCOME_PHOTO), caption=welcome, reply_markup=MENU)
    else:
        # Admin-managed welcome texts allow 3000 characters; photo captions allow 1024.
        await message.answer_photo(FSInputFile(WELCOME_PHOTO))
        await message.answer(welcome, reply_markup=MENU)


@router.message(Command("cancel"))
@router.message(F.text == "↩️ Отмена")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Выберите действие:", reply_markup=MENU)


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Возвращаемся в меню.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Главное меню:", reply_markup=MENU)


@router.message(F.text == "🧹 Услуги и цены")
async def services(message: Message, db: Database):
    catalog = await db.catalog()
    lines = []
    for item in catalog["services"].values():
        if item.get("enabled"):
            lines.append(f"• {html.escape(item['title'])}")
    await message.answer(
        "Доступные услуги:\n"
        + ("\n".join(lines) or "Список уточните у менеджера.")
        + "\n\nСтоимость зависит от объекта, площади и объёма работ. Итог подтверждает менеджер.",
        reply_markup=MENU,
    )


@router.message(F.text == "🔥 Акции")
async def promotions(message: Message, db: Database):
    await message.answer((await db.catalog())["promotions"], reply_markup=MENU)


@router.message(F.text == "⭐ Отзывы")
async def reviews(message: Message, db: Database):
    await message.answer((await db.catalog())["reviews"], reply_markup=MENU)


@router.message(F.text == "❓ Частые вопросы")
async def faq(message: Message, db: Database):
    await message.answer((await db.catalog())["faq"], reply_markup=MENU)


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
        }
    )
)
async def menu_action(query: CallbackQuery, state: FSMContext, db: Database):
    await query.answer()
    if not isinstance(query.message, Message):
        return
    action = query.data.split(":", 1)[1]
    if action in {"estimate", "order"}:
        await begin_form(query.message, state, db, mode=action)
    elif action == "contact":
        await state.clear()
        await contact_manager(query.message, state)
    else:
        handlers = {"services": services, "promotions": promotions, "reviews": reviews, "faq": faq}
        await handlers[action](query.message, db)

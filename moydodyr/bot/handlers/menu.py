import html
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
)
from aiogram.utils.deep_linking import decode_payload

from moydodyr.bot.keyboards import (
    MENU,
)
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")


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
    await message.answer(catalog["welcome"], reply_markup=MENU)


@router.message(Command("cancel"))
@router.message(F.text == "↩️ Отмена")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Вы можете начать заново в меню.", reply_markup=MENU)


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext):
    await state.clear()
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
        + "\n\nСтоимость зависит от объекта, площади и объёма работ. Итог подтверждает менеджер."
    )


@router.message(F.text == "🔥 Акции")
async def promotions(message: Message, db: Database):
    await message.answer((await db.catalog())["promotions"])


@router.message(F.text == "⭐ Отзывы")
async def reviews(message: Message, db: Database):
    await message.answer((await db.catalog())["reviews"])


@router.message(F.text == "❓ Частые вопросы")
async def faq(message: Message, db: Database):
    await message.answer((await db.catalog())["faq"])

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.deep_linking import decode_payload

from moydodyr.bot.keyboards import mini_app_keyboard
from moydodyr.bot.services.screen import SCREEN_IMAGES, clear_flow
from moydodyr.config import Settings
from moydodyr.db import Database

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

WELCOME = (
    "Добро пожаловать в «Мойдодыр»! 🫧\n\n"
    "Откройте приложение, чтобы выбрать уборку, узнать предварительную стоимость "
    "и оставить заявку."
)


@router.message()
async def welcome(message: Message, state: FSMContext, db: Database, settings: Settings):
    source = "Прямой переход"
    text = message.text or ""
    if text.startswith("/start "):
        raw = text.split(maxsplit=1)[1].strip()
        try:
            raw = decode_payload(raw)
        except (ValueError, UnicodeDecodeError):
            pass
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", raw):
            source = raw
    await db.register_user(message.from_user.id, source)
    await clear_flow(state)
    if settings.mini_app_url:
        text = WELCOME
    else:
        text = "Добро пожаловать в «Мойдодыр»! 🫧\n\nПриложение пока недоступно. Попробуйте позже."
    await message.answer_photo(
        FSInputFile(SCREEN_IMAGES["welcome"]),
        caption=text,
        reply_markup=mini_app_keyboard(settings.mini_app_url),
        parse_mode=None,
    )


@router.callback_query()
async def old_button(query: CallbackQuery):
    await query.answer("Это меню больше не используется. Отправьте /start.", show_alert=True)

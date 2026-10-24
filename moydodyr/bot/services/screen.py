from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from moydodyr.bot.keyboards import MENU

SCREEN_KEY = "_screen"
ASSETS = Path(__file__).resolve().parents[3] / "assets"
SCREEN_IMAGES = {"welcome": ASSETS / "welcome.png", "estimate": ASSETS / "estimate.png"}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def pages_for(text: str, limit: int) -> list[str]:
    parser = PlainText()
    parser.feed(text)
    parser.close()
    plain = "".join(parser.parts)
    pages, part, size = [], "", 0
    for char in plain:
        width = len(char.encode("utf-16-le")) // 2
        if size + width > limit:
            pages.append(part)
            part, size = "", 0
        part += char
        size += width
    return pages + [part or "Выберите действие:"]


async def clear_flow(state: FSMContext):
    screen = (await state.get_data()).get(SCREEN_KEY)
    await state.clear()
    if screen:
        screen = {**screen, "history": [], "state": None, "draft": {}, "image": "welcome"}
        await state.update_data(**{SCREEN_KEY: screen})


async def bind_screen(message: Message, state: FSMContext) -> bool:
    screen = (await state.get_data()).get(SCREEN_KEY)
    if screen and screen["message_id"] != message.message_id:
        return False
    if not screen:
        await remove_legacy_keyboard(message, state)
        await state.update_data(
            **{
                SCREEN_KEY: {
                    "message_id": message.message_id,
                    "photo": bool(message.photo),
                }
            }
        )
    return True


async def remove_legacy_keyboard(message: Message, state: FSMContext):
    if await state.get_state() and not (await state.get_data()).get(SCREEN_KEY):
        # Telegram can only remove an old reply keyboard with a new message.
        # Delete this migration-only message immediately, leaving one visible screen.
        temporary = await message.answer("…", reply_markup=ReplyKeyboardRemove())
        await temporary.delete()


def keyboard_for(screen: dict, page: int) -> InlineKeyboardMarkup:
    rows = InlineKeyboardMarkup.model_validate(screen["markup"]).inline_keyboard.copy()
    if screen.get("history"):
        back = InlineKeyboardButton(
            text="← Назад", callback_data=f"screen:back:{screen['nonce']}:0"
        )
        home_row = next(
            (
                index
                for index, row in enumerate(rows)
                if any(button.callback_data == "menu:home" for button in row)
            ),
            None,
        )
        if home_row is not None:
            rows[home_row] = [back, *rows[home_row]]
        else:
            rows.append([back])
    if len(screen["pages"]) > 1:
        rows += [
            [
                InlineKeyboardButton(
                    text=f"{'←' if index < page else '→'} {index + 1}/{len(screen['pages'])}",
                    callback_data=f"screen:page:{screen['nonce']}:{index}",
                )
                for index in (page - 1, page + 1)
                if 0 <= index < len(screen["pages"])
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_page(message: Message, screen: dict, page: int):
    kwargs = dict(
        chat_id=message.chat.id,
        message_id=screen["message_id"],
        reply_markup=keyboard_for(screen, page),
        parse_mode=None,
    )
    try:
        image = screen.get("image", "welcome")
        if image != screen.get("displayed_image", "welcome"):
            await message.bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=screen["message_id"],
                media=InputMediaPhoto(
                    media=FSInputFile(SCREEN_IMAGES[image]),
                    caption=screen["pages"][page],
                    parse_mode=None,
                ),
                reply_markup=keyboard_for(screen, page),
            )
            screen.update(photo=True, displayed_image=image)
        elif screen["photo"]:
            await message.bot.edit_message_caption(caption=screen["pages"][page], **kwargs)
        else:
            await message.bot.edit_message_text(text=screen["pages"][page], **kwargs)
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message:
            raise


async def show_screen(
    message: Message, state: FSMContext, text: str, reply_markup=None, image=None
):
    data = await state.get_data()
    screen = dict(data.get(SCREEN_KEY) or {})
    if image is not None:
        screen["image"] = image
    current_state = await state.get_state()
    history = list(screen.get("history", []))
    if current_state is None:
        history = []
    elif screen.get("state") and screen["state"] != current_state:
        history.append(deepcopy({key: value for key, value in screen.items() if key != "history"}))
    nonce = uuid4().hex[:12]
    buttons = []
    if isinstance(reply_markup, ReplyKeyboardMarkup):
        rows = []
        for row in reply_markup.keyboard:
            inline_row = []
            for button in row:
                if button.text == "↩️ Отмена":
                    continue
                index = len(buttons)
                buttons.append("__contact_help__" if button.request_contact else button.text)
                inline_row.append(
                    InlineKeyboardButton(
                        text="Как отправить телефон?" if button.request_contact else button.text,
                        callback_data=f"screen:pick:{nonce}:{index}",
                    )
                )
            if inline_row:
                rows.append(inline_row)
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
    elif isinstance(reply_markup, InlineKeyboardMarkup):
        markup = reply_markup
    elif reply_markup is None and screen.get("markup"):
        # Validation errors retain the choices of the current step.
        markup = InlineKeyboardMarkup.model_validate(screen["markup"])
        buttons = screen.get("buttons", [])
        nonce = screen["nonce"]
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[])
    if markup != MENU and not any(
        b.callback_data == "menu:home" for row in markup.inline_keyboard for b in row
    ):
        markup = InlineKeyboardMarkup(
            inline_keyboard=markup.inline_keyboard
            + [[InlineKeyboardButton(text="↩️ Главное меню", callback_data="menu:home")]]
        )
    screen.update(nonce=nonce, buttons=buttons, markup=markup.model_dump(mode="json"))
    screen.update(
        state=current_state,
        history=history,
        draft=deepcopy({key: value for key, value in data.items() if key != SCREEN_KEY}),
    )
    screen["pages"] = pages_for(text, 1024 if screen.get("photo") else 4096)
    if "message_id" not in screen:
        sent = await message.answer(
            screen["pages"][0], reply_markup=keyboard_for(screen, 0), parse_mode=None
        )
        screen.update(message_id=sent.message_id, photo=False)
    else:
        await render_page(message, screen, 0)
    await state.update_data(**{SCREEN_KEY: screen})


async def go_back(message: Message, state: FSMContext):
    screen = (await state.get_data()).get(SCREEN_KEY, {})
    history = list(screen.get("history", []))
    if not history:
        return
    previous = deepcopy(history.pop())
    previous["displayed_image"] = screen.get("displayed_image", "welcome")
    previous["history"] = history
    previous["nonce"] = uuid4().hex[:12]
    for row in previous["markup"]["inline_keyboard"]:
        for button in row:
            callback = button.get("callback_data") or ""
            if callback.startswith("screen:pick:"):
                button["callback_data"] = (
                    f"screen:pick:{previous['nonce']}:{callback.split(':')[-1]}"
                )
    await render_page(message, previous, 0)
    await state.set_state(previous["state"])
    await state.set_data({**previous["draft"], SCREEN_KEY: previous})

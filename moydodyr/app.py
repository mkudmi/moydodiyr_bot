import asyncio
import calendar
import html
import json
import logging
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.deep_linking import decode_payload

from moydodyr.config import Settings
from moydodyr.db import Database, PostgresStorage
from moydodyr.domain import MOSCOW, area_value, date_value, estimate, phone_value, time_value

router = Router()
router.message.filter(F.chat.type == "private")
group_router = Router()
group_router.message.filter(F.chat.type.in_({"group", "supergroup"}))
ROOT = Path(__file__).resolve().parent.parent
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧹 Услуги и цены"), KeyboardButton(text="🧮 Рассчитать стоимость")],
        [KeyboardButton(text="📅 Оставить заявку"), KeyboardButton(text="🔥 Акции")],
        [KeyboardButton(text="⭐ Отзывы"), KeyboardButton(text="📞 Связаться с менеджером")],
        [KeyboardButton(text="❓ Частые вопросы")],
    ],
    resize_keyboard=True,
)
PENDING_ALBUMS: dict[tuple[int, int, str], list[Message]] = {}
ALBUM_TASKS: dict[tuple[int, int, str], asyncio.Task] = {}
ALBUM_GUARD = asyncio.Lock()
PHOTO_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📬 Новые заявки"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="🛠 Заявки в работе")],
        [KeyboardButton(text="ℹ️ Команды администратора")],
    ],
    resize_keyboard=True,
)


class Form(StatesGroup):
    object = State()
    service = State()
    area = State()
    extras = State()
    estimate_offer = State()
    address = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    comment = State()
    photos = State()
    confirm = State()
    manager_message = State()


class PermanentWebhookError(Exception):
    pass


def post_n8n_lead(url: str, secret: str, lead: dict) -> str | int | None:
    body = {**lead, "secret": secret}
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            content = response.read()
    except HTTPError as error:
        if error.code == 401:
            raise PermanentWebhookError("n8n returned HTTP 401; check N8N_WEBHOOK_SECRET") from None
        raise RuntimeError(f"n8n returned HTTP {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"n8n connection failed: {type(error.reason).__name__}") from None
    if status != 200:
        raise RuntimeError(f"n8n returned HTTP {status}")
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("n8n returned invalid JSON") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("n8n response did not confirm success")
    return result.get("lead_id")


async def send_n8n_lead(url: str, secret: str, lead: dict) -> str | int | None:
    return await asyncio.to_thread(post_n8n_lead, url, secret, lead)


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


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database, settings: Settings):
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=4)
    if len(parts) == 1:
        await message.answer(
            "Команды администратора:\n"
            "/admin price <service_key> <flat|house> <руб/м²>\n"
            "/admin minimum <service_key> <руб>\n"
            "/admin extra <extra_key> <цена>\n"
            "/admin enabled <service|extra> <key> <true|false>\n"
            "/admin text <welcome|accepted|promotions|reviews|faq> <новый текст>\n"
            "Например: /admin price regular flat 120"
        )
        return
    try:
        catalog = await db.catalog()
        updated = deepcopy(catalog)
        action = parts[1]
        if action == "price" and len(parts) == 5:
            _, _, key, object_key, raw = parts
            field = {"flat": "rate_flat", "house": "rate_house"}.get(object_key)
            if key not in updated["services"] or field is None:
                raise ValueError("Укажите существующий ключ услуги и объект flat или house.")
            updated["services"][key][field] = float(Decimal(raw.replace(",", ".")))
        elif action == "minimum" and len(parts) == 4:
            _, _, key, raw = parts
            if key not in updated["services"]:
                raise ValueError("Неизвестный ключ услуги.")
            updated["services"][key]["minimum"] = float(Decimal(raw.replace(",", ".")))
        elif action == "extra" and len(parts) == 4:
            _, _, key, raw = parts
            if key not in updated["extras"]:
                raise ValueError("Неизвестный ключ дополнительной услуги.")
            updated["extras"][key]["price"] = float(Decimal(raw.replace(",", ".")))
        elif action == "enabled" and len(parts) == 5:
            _, _, kind, key, raw = parts
            group = {"service": "services", "extra": "extras"}.get(kind)
            if group is None or key not in updated[group] or raw.lower() not in {"true", "false"}:
                raise ValueError("Формат: service|extra, ключ и true|false.")
            updated[group][key]["enabled"] = raw.lower() == "true"
        elif action == "text" and len(parts) == 4:
            _, _, key, value = parts
            if key not in {"welcome", "accepted", "promotions", "reviews", "faq"}:
                raise ValueError("Неизвестный текстовый раздел.")
            updated[key] = value
        else:
            raise ValueError("Неизвестная команда или неверное число аргументов. Откройте /admin.")
        await db.change_catalog(lambda current: (current.clear(), current.update(updated)))
    except (ValueError, ArithmeticError) as error:
        await message.answer(f"Не удалось сохранить: {error}")
        return
    await message.answer("Настройки сохранены.")


def choices(items: dict, columns: int = 2) -> ReplyKeyboardMarkup:
    buttons = [KeyboardButton(text=value) for value in items.values()]
    rows = [buttons[i : i + columns] for i in range(0, len(buttons), columns)]
    rows.append([KeyboardButton(text="↩️ Отмена")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def item_keyboard(items: dict) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=item["title"])] for key, item in items.items() if item.get("enabled")
    ]
    rows.append([KeyboardButton(text="↩️ Отмена")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def extras_keyboard(catalog: dict, selected: set[str]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=f"{'✅' if key in selected else '▫️'} {item['title']}")]
        for key, item in catalog["extras"].items()
        if item.get("enabled")
    ]
    rows += [[KeyboardButton(text="Готово")], [KeyboardButton(text="↩️ Отмена")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


MONTHS_RU = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    today = datetime.now(MOSCOW).date()
    last_day = today + timedelta(days=365)
    first_day = date(year, month, 1)
    previous_month = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    can_go_previous = previous_month >= today.replace(day=1)
    next_button = InlineKeyboardButton(
        text="›" if next_month <= last_day.replace(day=1) else "·",
        callback_data=f"cal:next:{year:04d}-{month:02d}"
        if next_month <= last_day.replace(day=1)
        else "cal:none",
    )
    rows = [
        [
            InlineKeyboardButton(
                text="‹" if can_go_previous else "·",
                callback_data=f"cal:prev:{year:04d}-{month:02d}" if can_go_previous else "cal:none",
            ),
            InlineKeyboardButton(
                text=f"{MONTHS_RU[month - 1].capitalize()} {year}", callback_data="cal:none"
            ),
            next_button,
        ],
        [
            InlineKeyboardButton(text=day, callback_data="cal:none")
            for day in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        ],
    ]
    week = []
    for _ in range(first_day.weekday()):
        week.append(InlineKeyboardButton(text="·", callback_data="cal:none"))
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        selected_date = date(year, month, day)
        available = today <= selected_date <= last_day
        past = selected_date < today
        week.append(
            InlineKeyboardButton(
                text=str(day) if available else f"×{day}" if past else "·",
                callback_data=f"cal:select:{selected_date.isoformat()}"
                if available
                else "cal:past:0"
                if past
                else "cal:none",
            )
        )
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        week.extend(
            InlineKeyboardButton(text="·", callback_data="cal:none") for _ in range(7 - len(week))
        )
        rows.append(week)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def time_keyboard(day: str) -> InlineKeyboardMarkup:
    selected_day = date.fromisoformat(day)
    now = datetime.now(MOSCOW)
    slots = [f"{hour:02d}:00–{hour + 2:02d}:00" for hour in range(9, 21, 2)]
    if selected_day == now.date():
        slots = [
            slot
            for slot in slots
            if datetime.combine(
                selected_day, datetime.strptime(slot[:5], "%H:%M").time(), tzinfo=MOSCOW
            )
            > now
        ]
    rows = []
    row = []
    for slot in slots:
        row.append(InlineKeyboardButton(text=slot, callback_data=f"tm:select:{slot}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.extend(
        [
            [InlineKeyboardButton(text="Время не важно", callback_data="tm:any")],
            [InlineKeyboardButton(text="Ввести вручную", callback_data="tm:custom")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def photo_lock(user_key: tuple[int, int]) -> asyncio.Lock:
    return PHOTO_LOCKS.setdefault(user_key, asyncio.Lock())


async def append_photos(messages: list[Message], state: FSMContext):
    if not messages:
        return
    user_key = (messages[0].chat.id, messages[0].from_user.id)
    async with photo_lock(user_key):
        data = await state.get_data()
        existing = list(data.get("photos", []))
        remaining = max(0, 5 - len(existing))
        incoming = [message.photo[-1].file_id for message in messages if message.photo]
        added = incoming[:remaining]
        photos = existing + added
        await state.update_data(photos=photos)
        note = f"Добавлено фото: {len(photos)}/5. Пришлите ещё или нажмите «Готово»."
        if len(incoming) > remaining:
            note = f"В заявке максимум 5 фото. Сохранено {len(photos)}. Нажмите «Готово»."
        await messages[-1].answer(note)


async def flush_album(key: tuple[int, int, str], state: FSMContext):
    await asyncio.sleep(0.8)
    user_key = key[:2]
    async with photo_lock(user_key):
        async with ALBUM_GUARD:
            messages = PENDING_ALBUMS.pop(key, [])
            ALBUM_TASKS.pop(key, None)
        if messages:
            await append_photos_unlocked(messages, state)


async def append_photos_unlocked(messages: list[Message], state: FSMContext):
    data = await state.get_data()
    existing = list(data.get("photos", []))
    incoming = [message.photo[-1].file_id for message in messages if message.photo]
    added = incoming[: max(0, 5 - len(existing))]
    photos = existing + added
    await state.update_data(photos=photos)
    if len(incoming) > len(added):
        note = f"В заявке максимум 5 фото. Сохранено {len(photos)}. Нажмите «Готово»."
    else:
        note = f"Добавлено {len(added)} фото ({len(photos)}/5). Нажмите «Готово», когда закончите."
    await messages[-1].answer(note)


async def flush_user_albums(message: Message, state: FSMContext):
    user_key = (message.chat.id, message.from_user.id)
    async with photo_lock(user_key):
        async with ALBUM_GUARD:
            keys = [key for key in PENDING_ALBUMS if key[:2] == user_key]
            messages = []
            for key in keys:
                messages.extend(PENDING_ALBUMS.pop(key, []))
                task = ALBUM_TASKS.pop(key, None)
                if task and task is not asyncio.current_task():
                    task.cancel()
        if messages:
            await append_photos_unlocked(messages, state)


def order_text(order, catalog: dict) -> str:
    payload = order["payload"]
    status_label = {
        "new": "Новая",
        "working": "В работе",
        "agreed": "Согласована",
        "cancelled": "Отменена",
    }.get(order["status"], order["status"])
    obj = html.escape(catalog["objects"].get(payload.get("object"), payload.get("object", "—")))
    service = html.escape(catalog["services"].get(payload.get("service"), {}).get("title", "—"))
    result = [
        f"<b>Заявка №{order['id']}</b>",
        f"Статус: {html.escape(status_label)}",
        f"Клиент: {html.escape(payload.get('name', '—'))}",
        f"Телефон: {html.escape(payload.get('phone', '—'))}",
        f"Объект: {obj}",
        f"Услуга: {service}",
        f"Площадь: {html.escape(payload.get('area', '—'))} м²",
        f"Адрес: {html.escape(payload.get('address', '—'))}",
        "Дата и время: "
        f"{html.escape(payload.get('date', '—'))}, "
        f"{html.escape(payload.get('time', '—'))}",
        f"Источник: {html.escape(order['source'])}",
    ]
    if order["manager_id"]:
        result.append(f'Ответственный: <a href="tg://user?id={order["manager_id"]}">менеджер</a>')
    if payload.get("estimate"):
        result.append(f"Предварительно: {payload['estimate']['total']:,} ₽".replace(",", " "))
    if payload.get("extras"):
        labels = [catalog["extras"][key]["title"] for key in payload["extras"]]
        result.append("Дополнительно: " + html.escape(", ".join(labels)))
    if payload.get("comment"):
        result.append("Комментарий: " + html.escape(payload["comment"]))
    return "\n".join(result)


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


@router.message(F.text.in_({"🧮 Рассчитать стоимость", "📅 Оставить заявку"}))
async def begin(message: Message, state: FSMContext, db: Database):
    catalog = await db.catalog()
    objects = {key: value for key, value in catalog["objects"].items()}
    await state.clear()
    mode = "estimate" if message.text == "🧮 Рассчитать стоимость" else "order"
    await state.update_data(draft_id=str(uuid4()), extras=[], photos=[], mode=mode)
    await state.set_state(Form.object)
    await message.answer("Выберите тип объекта:", reply_markup=choices(objects))


@router.message(Form.object)
async def choose_object(message: Message, state: FSMContext, db: Database):
    catalog = await db.catalog()
    key = next(
        (key for key, title in catalog["objects"].items() if title == (message.text or "")),
        None,
    )
    if key is None:
        await message.answer("Выберите тип объекта на клавиатуре.")
        return
    await state.update_data(object=key)
    await state.set_state(Form.service)
    await message.answer("Выберите вид уборки:", reply_markup=item_keyboard(catalog["services"]))


@router.message(Form.service)
async def choose_service(message: Message, state: FSMContext, db: Database):
    catalog = await db.catalog()
    key = next(
        (
            key
            for key, item in catalog["services"].items()
            if item.get("enabled") and item["title"] == (message.text or "")
        ),
        None,
    )
    item = catalog["services"].get(key) if key else None
    if not item or not item.get("enabled"):
        await message.answer("Выберите доступную услугу на клавиатуре.")
        return
    await state.update_data(service=key)
    await state.set_state(Form.area)
    await message.answer(
        "Укажите площадь в м² (например, 65 или 65,5).", reply_markup=ReplyKeyboardRemove()
    )


@router.message(Form.area)
async def choose_area(message: Message, state: FSMContext, db: Database):
    try:
        area = area_value(message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(area=area)
    catalog = await db.catalog()
    available = {key: x for key, x in catalog["extras"].items() if x.get("enabled")}
    await state.set_state(Form.extras)
    if available:
        await message.answer(
            "Выберите дополнительные услуги (можно несколько), затем нажмите «Готово»:",
            reply_markup=extras_keyboard(catalog, set()),
        )
    else:
        await complete_service_selection(message, state, db)


async def complete_service_selection(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    if data.get("mode") == "estimate":
        catalog = await db.catalog()
        result = estimate(catalog, data)
        await state.update_data(estimate=result)
        await state.set_state(Form.estimate_offer)
        if result is None:
            answer = (
                "Не удалось рассчитать ориентировочную стоимость: тарифы для выбранных "
                "услуг ещё не настроены. Вы можете отправить заявку, и менеджер рассчитает цену."
            )
        else:
            answer = (
                f"Ориентировочная стоимость: <b>{result['total']:,} ₽</b>.\n"
                "Итоговая цена зависит от объёма работ и будет подтверждена менеджером."
            ).replace(",", " ")
        await message.answer(
            answer,
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📩 Оставить заявку")],
                    [KeyboardButton(text="↩️ Отмена")],
                ],
                resize_keyboard=True,
            ),
        )
        return
    await ask_address(message, state)


@router.message(Form.estimate_offer, F.text == "📩 Оставить заявку")
async def continue_from_estimate(message: Message, state: FSMContext):
    await ask_address(message, state)


@router.message(Form.estimate_offer)
async def wait_estimate_choice(message: Message):
    await message.answer("Нажмите «📩 Оставить заявку» или отмените действие.")


async def ask_address(message: Message, state: FSMContext):
    await state.set_state(Form.address)
    await message.answer(
        "Укажите адрес уборки или отправьте местоположение.", reply_markup=ReplyKeyboardRemove()
    )


@router.message(Form.extras)
async def choose_extras(message: Message, state: FSMContext, db: Database):
    catalog = await db.catalog()
    data = await state.get_data()
    selected = set(data.get("extras", []))
    if message.text == "Готово":
        await complete_service_selection(message, state, db)
        return
    for key, item in catalog["extras"].items():
        if item.get("enabled") and message.text in {f"▫️ {item['title']}", f"✅ {item['title']}"}:
            selected.symmetric_difference_update({key})
            await state.update_data(extras=list(selected))
            await message.answer(
                "Отметьте дополнительные услуги или нажмите «Готово»:",
                reply_markup=extras_keyboard(catalog, selected),
            )
            return
    await message.answer("Выберите услугу на клавиатуре или нажмите «Готово».")


@router.message(Form.address, F.location)
async def address_location(message: Message, state: FSMContext):
    loc = message.location
    await state.update_data(address=f"Геолокация: {loc.latitude:.6f}, {loc.longitude:.6f}")
    await ask_date(message, state)


@router.message(Form.address)
async def address_text(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 5 or len(value) > 500:
        await message.answer("Напишите адрес длиной от 5 до 500 символов.")
        return
    await state.update_data(address=value)
    await ask_date(message, state)


async def ask_date(message: Message, state: FSMContext):
    await state.set_state(Form.date)
    today = datetime.now(MOSCOW).date()
    await message.answer(
        "Выберите желаемую дату. Доступность подтвердит менеджер.",
        reply_markup=calendar_keyboard(today.year, today.month),
    )


@router.callback_query(Form.date, F.data.startswith("cal:"))
async def calendar_callback(query: CallbackQuery, state: FSMContext):
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3 or not query.message:
        await query.answer()
        return
    action, value = parts[1], parts[2]
    if action == "past":
        await query.answer("Прошедшую дату выбрать нельзя.", show_alert=True)
        return
    if action == "none":
        await query.answer()
        return
    if action in {"prev", "next"}:
        year, month = (int(part) for part in value.split("-"))
        next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        target = (
            date(year - (month == 1), 12 if month == 1 else month - 1, 1)
            if action == "prev"
            else next_month
        )
        today = datetime.now(MOSCOW).date()
        if today.replace(day=1) <= target <= (today + timedelta(days=365)).replace(day=1):
            await query.message.edit_reply_markup(
                reply_markup=calendar_keyboard(target.year, target.month)
            )
        await query.answer()
        return
    if action == "select":
        try:
            selected = date.fromisoformat(value)
            normalized = date_value(selected.strftime("%d.%m.%Y"))
        except ValueError:
            await query.answer("Выберите доступную дату.", show_alert=True)
            return
    await state.update_data(date=normalized)
    await state.set_state(Form.time)
    await query.answer()
    await query.message.edit_text(f"Выбрана дата: {selected.strftime('%d.%m.%Y')}.")
    await ask_time(query.message, state)


@router.message(Form.date)
async def choose_date(message: Message, state: FSMContext):
    try:
        value = date_value(message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(date=value)
    await ask_time(message, state)


async def ask_time(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Form.time)
    await message.answer(
        "Выберите желаемое время. Доступность подтвердит менеджер.",
        reply_markup=time_keyboard(data["date"]),
    )


@router.callback_query(Form.time, F.data.startswith("tm:"))
async def time_callback(query: CallbackQuery, state: FSMContext):
    if not query.message:
        await query.answer()
        return
    action = (query.data or "").split(":", 2)
    if len(action) < 2:
        await query.answer()
        return
    if action[1] == "custom":
        await query.answer()
        await query.message.edit_text("Напишите желаемое время в формате ЧЧ:ММ или «любое».")
        return
    value = "Любое время" if action[1] == "any" else action[2]
    try:
        value = time_value(value)
    except (ValueError, IndexError):
        await query.answer("Выберите время на клавиатуре.", show_alert=True)
        return
    await state.update_data(time=value)
    await state.set_state(Form.name)
    await query.answer()
    await query.message.edit_text(f"Выбрано время: {value}.")
    await query.message.answer("Как к вам обращаться?")


@router.message(Form.time)
async def choose_time(message: Message, state: FSMContext):
    try:
        value = time_value(message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(time=value)
    await ask_name(message, state)


async def ask_name(message: Message, state: FSMContext):
    await state.set_state(Form.name)
    await message.answer("Как к вам обращаться?")


@router.message(Form.name)
async def choose_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not 2 <= len(value) <= 100:
        await message.answer("Имя должно содержать от 2 до 100 символов.")
        return
    await state.update_data(name=value)
    await state.set_state(Form.phone)
    await message.answer(
        "Оставьте номер телефона для связи.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отправить телефон", request_contact=True)],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Form.phone, F.contact)
async def choose_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    try:
        phone = phone_value(phone if phone.startswith("+") else "+" + phone)
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(phone=phone)
    await ask_comment(message, state)


@router.message(Form.phone)
async def choose_phone(message: Message, state: FSMContext):
    try:
        phone = phone_value(message.text or "")
    except ValueError as e:
        await message.answer(str(e))
        return
    await state.update_data(phone=phone)
    await ask_comment(message, state)


async def ask_comment(message: Message, state: FSMContext):
    await state.set_state(Form.comment)
    await message.answer(
        "Комментарий к заказу? Напишите его или нажмите «Пропустить».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Пропустить")], [KeyboardButton(text="↩️ Отмена")]],
            resize_keyboard=True,
        ),
    )


@router.message(Form.comment)
async def choose_comment(message: Message, state: FSMContext):
    comment = "" if message.text == "Пропустить" else (message.text or "").strip()
    if len(comment) > 1500:
        await message.answer("Комментарий должен быть не длиннее 1500 символов.")
        return
    await state.update_data(comment=comment)
    await state.set_state(Form.photos)
    await message.answer(
        "Пришлите до 5 фотографий объекта. Когда закончите, нажмите «Готово» или «Пропустить».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Готово"), KeyboardButton(text="Пропустить")],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Form.photos, F.photo)
async def add_photo(message: Message, state: FSMContext):
    if not message.media_group_id:
        await append_photos([message], state)
        return
    key = (message.chat.id, message.from_user.id, message.media_group_id)
    async with ALBUM_GUARD:
        PENDING_ALBUMS.setdefault(key, []).append(message)
        previous_task = ALBUM_TASKS.get(key)
        if previous_task and not previous_task.done():
            previous_task.cancel()
        ALBUM_TASKS[key] = asyncio.create_task(flush_album(key, state))


@router.message(Form.photos)
async def finish_photos(message: Message, state: FSMContext, db: Database):
    if message.text not in {"Готово", "Пропустить"}:
        await message.answer("Пришлите фотографию или нажмите «Готово».")
        return
    await flush_user_albums(message, state)
    data = await state.get_data()
    catalog = await db.catalog()
    estimate_data = estimate(catalog, data)
    await state.update_data(estimate=estimate_data)
    await state.set_state(Form.confirm)
    object_label = catalog["objects"][data["object"]]
    service_label = catalog["services"][data["service"]]["title"]
    lines = [
        "Проверьте заявку:",
        f"Объект: {object_label}",
        f"Уборка: {service_label}",
        f"Площадь: {data['area']} м²",
        f"Адрес: {data['address']}",
        f"Дата и время: {data['date']}, {data['time']}",
        f"Имя: {data['name']}",
        f"Телефон: {data['phone']}",
    ]
    if estimate_data:
        lines.append(f"Предварительная стоимость: {estimate_data['total']:,} ₽".replace(",", " "))
    else:
        lines.append("Стоимость подтвердит менеджер после уточнения тарифа и объёма работ.")
    if data.get("comment"):
        lines.append(f"Комментарий: {data['comment']}")
    await message.answer(
        "\n".join(lines),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Подтвердить заявку")],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Form.confirm, F.text == "✅ Подтвердить заявку")
async def confirm(message: Message, state: FSMContext, db: Database, settings: Settings):
    data = await state.get_data()
    selected_date = date.fromisoformat(data["date"])
    if selected_date < datetime.now(MOSCOW).date():
        await message.answer("Выбранная дата уже прошла. Выберите новую дату.")
        await ask_date(message, state)
        return
    data["telegram_user"] = message.from_user.id
    catalog = await db.catalog()
    webhook_payload = None
    if settings.webhook_enabled:
        extras = [catalog["extras"][key]["title"] for key in data.get("extras", [])]
        webhook_payload = {
            "name": data["name"],
            "phone": data["phone"],
            "telegram": f"@{message.from_user.username}"
            if message.from_user.username
            else str(message.from_user.id),
            "service": catalog["services"][data["service"]]["title"],
            "address": data["address"],
            "date": data["date"],
            "price": (data.get("estimate") or {}).get("total"),
            "comment": data.get("comment", ""),
            "object": catalog["objects"][data["object"]],
            "area": data["area"],
            "time": data["time"],
            "extras": extras,
        }
    await db.submit(
        message.from_user.id,
        data,
        settings.manager_chat_id,
        catalog["accepted"],
        webhook_payload=webhook_payload,
    )
    await state.clear()


@router.message(F.text == "📞 Связаться с менеджером")
async def contact_manager(message: Message, state: FSMContext):
    await state.set_state(Form.manager_message)
    await message.answer(
        "Напишите сообщение менеджеру. Я передам его вместе с вашим контактом.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отправить телефон", request_contact=True)],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(Form.manager_message, F.contact)
async def manager_contact_only(message: Message, state: FSMContext):
    await state.update_data(contact=message.contact.phone_number)
    await message.answer("Теперь напишите сообщение менеджеру.")


@router.message(Form.manager_message)
async def manager_message(message: Message, state: FSMContext, db: Database, settings: Settings):
    data = await state.get_data()
    text = (message.text or "").strip()
    if len(text) < 1 or len(text) > 3000:
        await message.answer("Сообщение должно быть не длиннее 3000 символов.")
        return
    user = message.from_user
    source = await db.pool.fetchval("SELECT source FROM users WHERE id=$1", user.id)
    request_key = f"contact:{user.id}:{uuid4()}"
    phone = data.get("contact", "не указан")
    manager_payload = {
        "kind": "manager_message",
        "text": (
            f"Сообщение от {html.escape(user.full_name)} "
            f"(@{html.escape(user.username or 'нет username')}, id {user.id})\n"
            f"Телефон: {html.escape(phone)}\n\n{html.escape(text)}"
        ),
    }
    webhook_lead = None
    if settings.webhook_enabled:
        webhook_lead = {
            "name": user.full_name,
            "phone": phone if phone != "не указан" else "",
            "telegram": f"@{user.username}" if user.username else str(user.id),
            "service": "Связаться с менеджером",
            "address": "",
            "date": None,
            "price": None,
            "comment": text,
            "source": source,
            "lead_type": "manager_contact",
        }
    await db.enqueue_contact(
        request_key,
        settings.manager_chat_id,
        manager_payload,
        webhook_lead,
    )
    await state.clear()
    await message.answer(
        "Передал сообщение менеджеру. Он ответит вам напрямую в Telegram.", reply_markup=MENU
    )


@router.callback_query(F.data.startswith("order:"))
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


async def deliver_outbox(bot: Bot, db: Database, settings: Settings):
    while True:
        row = await db.next_notification()
        if row is None:
            await asyncio.sleep(1)
            continue
        try:
            data = row["payload"]
            if data["kind"] == "order":
                order = await db.order(data["order_id"])
                catalog = await db.catalog()
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Взять в работу", callback_data=f"order:{order['id']}:take"
                            )
                        ]
                    ]
                )
                await bot.send_message(
                    row["chat_id"], order_text(order, catalog), reply_markup=markup
                )
            elif data["kind"] == "photo":
                await bot.send_photo(row["chat_id"], data["photo"], caption=data["caption"])
            elif data["kind"] == "text":
                await bot.send_message(row["chat_id"], data["text"], reply_markup=MENU)
            elif data["kind"] == "webhook":
                if not settings.webhook_enabled:
                    raise PermanentWebhookError("n8n webhook is not configured")
                lead_id = await send_n8n_lead(
                    settings.n8n_webhook_url,
                    settings.n8n_webhook_secret,
                    data["lead"],
                )
                if data.get("order_id") is not None:
                    await db.save_external_lead_id(data["order_id"], lead_id)
            else:
                await bot.send_message(row["chat_id"], data["text"])
            await db.mark_delivered(row["id"])
        except PermanentWebhookError as error:
            await db.fail_notification(row["id"], str(error))
            logging.error("Permanently failed outbox item %s: %s", row["id"], error)
        except Exception as error:
            # Webhook errors are deliberately reduced to their message: our HTTP
            # client messages contain status/reason, but never request body or secret.
            safe_error = str(error)[:500] or type(error).__name__
            if row["attempts"] >= 10:
                await db.fail_notification(row["id"], safe_error)
                logging.error("Outbox item %s exhausted retries (%s)", row["id"], safe_error)
            else:
                await db.defer_notification(row["id"], safe_error)
                logging.warning("Failed to deliver outbox item %s (%s)", row["id"], safe_error)


@router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Выберите действие в меню или отправьте /start.", reply_markup=MENU)


async def run():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    db = await Database.connect(settings.database_url)
    moved = await db.retarget_pending_manager_notifications(settings.manager_chat_id)
    if moved:
        logging.info("Retargeted %s pending manager notifications", moved)
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=PostgresStorage(db))
    dp.include_router(group_router)
    dp.include_router(router)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(deliver_outbox(bot, db, settings))
            tasks.create_task(
                dp.start_polling(
                    bot, db=db, settings=settings, allowed_updates=dp.resolve_used_update_types()
                )
            )
    finally:
        await bot.session.close()
        await db.pool.close()

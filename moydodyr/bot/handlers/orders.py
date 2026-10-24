import html
from datetime import date, datetime, timedelta
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from moydodyr.bot.keyboards import (
    calendar_keyboard,
    choices,
    extras_keyboard,
    item_keyboard,
    time_keyboard,
)
from moydodyr.bot.services.messages import delete_saved_input
from moydodyr.bot.services.photo_archive import ensure_draft_photos
from moydodyr.bot.services.photos import flush_user_albums
from moydodyr.bot.services.screen import SCREEN_KEY, bind_screen, clear_flow, show_screen
from moydodyr.bot.states import Form
from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.domain import MOSCOW, area_value, date_value, estimate, phone_value, time_value

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(F.text.in_({"🧮 Рассчитать стоимость", "📅 Оставить заявку"}))
async def begin(message: Message, state: FSMContext, db: Database):
    mode = "estimate" if message.text == "🧮 Рассчитать стоимость" else "order"
    await begin_form(message, state, db, mode)


async def begin_form(message: Message, state: FSMContext, db: Database, mode: str):
    catalog = await db.catalog()
    objects = {key: value for key, value in catalog["objects"].items()}
    await clear_flow(state)
    await state.update_data(draft_id=str(uuid4()), extras=[], photos=[], mode=mode)
    await state.set_state(Form.object)
    await show_screen(
        message,
        state,
        "Выберите тип объекта:",
        reply_markup=choices(objects),
        image="estimate" if mode == "estimate" else "order",
    )


@router.message(Form.object)
async def choose_object(message: Message, state: FSMContext, db: Database):
    catalog = await db.catalog()
    key = next(
        (key for key, title in catalog["objects"].items() if title == (message.text or "")),
        None,
    )
    if key is None:
        await show_screen(message, state, "Выберите тип объекта на клавиатуре.")
        return
    await state.update_data(object=key)
    await state.set_state(Form.service)
    await show_screen(
        message, state, "Выберите вид уборки:", reply_markup=item_keyboard(catalog["services"])
    )


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
        await show_screen(message, state, "Выберите доступную услугу на клавиатуре.")
        return
    await state.update_data(service=key)
    await state.set_state(Form.area)
    await show_screen(
        message,
        state,
        "Напишите площадь в м² и отправьте её сообщением в этот чат.\n\nНапример: 65 или 65,5.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Form.area)
async def choose_area(message: Message, state: FSMContext, db: Database):
    try:
        area = area_value(message.text or "")
    except ValueError as e:
        await show_screen(
            message,
            state,
            f"❗ {e}\n\nНапишите корректную площадь и отправьте её сообщением в этот чат.",
        )
        await delete_saved_input(message)
        return
    await state.update_data(area=area)
    catalog = await db.catalog()
    available = {key: x for key, x in catalog["extras"].items() if x.get("enabled")}
    await state.set_state(Form.extras)
    if available:
        await show_screen(
            message,
            state,
            "Выберите дополнительные услуги (можно несколько), затем нажмите «Готово»:",
            reply_markup=extras_keyboard(catalog, set()),
        )
    else:
        await complete_service_selection(message, state, db)
    await delete_saved_input(message)


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
        area_label = html.escape(data["area"].replace(".", ","))
        extra_labels = [
            html.escape(catalog["extras"][key]["title"])
            for key in data.get("extras", [])
            if key in catalog["extras"]
        ]
        extras_text = (
            "Дополнительные работы:\n" + "\n".join(f"• {label}" for label in extra_labels)
            if extra_labels
            else "Дополнительные работы: не выбраны."
        )
        answer = f"Площадь: {area_label} м².\n\n{extras_text}\n\n{answer}"
        await show_screen(
            message,
            state,
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
    await ask_address(message, state, image="order")


@router.message(Form.estimate_offer)
async def wait_estimate_choice(message: Message, state: FSMContext):
    await show_screen(message, state, "Нажмите «📩 Оставить заявку» или вернитесь в главное меню.")


async def ask_address(message: Message, state: FSMContext, image=None):
    await state.set_state(Form.address)
    await show_screen(
        message,
        state,
        "Укажите адрес уборки или отправьте местоположение.",
        reply_markup=ReplyKeyboardRemove(),
        image=image,
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
            await show_screen(
                message,
                state,
                "Отметьте дополнительные услуги или нажмите «Готово»:",
                reply_markup=extras_keyboard(catalog, selected),
            )
            return
    await show_screen(message, state, "Выберите услугу на клавиатуре или нажмите «Готово».")


@router.message(Form.address, F.location)
async def address_location(message: Message, state: FSMContext):
    loc = message.location
    await state.update_data(address=f"Геолокация: {loc.latitude:.6f}, {loc.longitude:.6f}")
    await ask_date(message, state)
    await delete_saved_input(message)


@router.message(Form.address)
async def address_text(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 5 or len(value) > 500:
        await show_screen(message, state, "Напишите адрес длиной от 5 до 500 символов.")
        return
    await state.update_data(address=value)
    await ask_date(message, state)
    await delete_saved_input(message)


async def ask_date(message: Message, state: FSMContext):
    await state.set_state(Form.date)
    today = datetime.now(MOSCOW).date()
    await show_screen(
        message,
        state,
        "Выберите желаемую дату. Доступность подтвердит менеджер.",
        reply_markup=calendar_keyboard(today.year, today.month),
    )


@router.callback_query(Form.date, F.data.startswith("cal:"))
async def calendar_callback(query: CallbackQuery, state: FSMContext):
    if not query.message or not await bind_screen(query.message, state):
        await query.answer("Используйте текущий экран или /start.")
        return
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
            await show_screen(
                query.message,
                state,
                "Выберите желаемую дату. Доступность подтвердит менеджер.",
                reply_markup=calendar_keyboard(target.year, target.month),
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
    else:
        await query.answer()
        return
    await state.update_data(date=normalized)
    await state.set_state(Form.time)
    await query.answer()
    await ask_time(query.message, state)


@router.message(Form.date)
async def choose_date(message: Message, state: FSMContext):
    try:
        value = date_value(message.text or "")
    except ValueError as e:
        await show_screen(message, state, str(e))
        return
    await state.update_data(date=value)
    await ask_time(message, state)


async def ask_time(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Form.time)
    await show_screen(
        message,
        state,
        "Выберите желаемое время. Доступность подтвердит менеджер.",
        reply_markup=time_keyboard(data["date"]),
    )


@router.callback_query(Form.time, F.data.startswith("tm:"))
async def time_callback(query: CallbackQuery, state: FSMContext):
    if not query.message or not await bind_screen(query.message, state):
        await query.answer()
        return
    action = (query.data or "").split(":", 2)
    if len(action) < 2:
        await query.answer()
        return
    if action[1] == "custom":
        await query.answer()
        await show_screen(
            query.message,
            state,
            "Напишите желаемое время в формате ЧЧ:ММ или «любое».",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    try:
        value = "Любое время" if action[1] == "any" else action[2]
        value = time_value(value)
    except (ValueError, IndexError):
        await query.answer("Выберите время на клавиатуре.", show_alert=True)
        return
    await state.update_data(time=value)
    await state.set_state(Form.name)
    await query.answer()
    await ask_name(query.message, state)


@router.message(Form.time)
async def choose_time(message: Message, state: FSMContext):
    try:
        value = time_value(message.text or "")
    except ValueError as e:
        await show_screen(message, state, str(e))
        return
    await state.update_data(time=value)
    await ask_name(message, state)


async def ask_name(message: Message, state: FSMContext):
    await state.set_state(Form.name)
    await show_screen(message, state, "Как к вам обращаться?", reply_markup=ReplyKeyboardRemove())


@router.message(Form.name)
async def choose_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not 2 <= len(value) <= 100:
        await show_screen(message, state, "Имя должно содержать от 2 до 100 символов.")
        return
    await state.update_data(name=value)
    await state.set_state(Form.phone)
    await show_screen(
        message,
        state,
        "Оставьте номер телефона для связи.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Отправить телефон", request_contact=True)],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )
    await delete_saved_input(message)


@router.message(Form.phone, F.contact)
async def choose_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    try:
        phone = phone_value(phone if phone.startswith("+") else "+" + phone)
    except ValueError as e:
        await show_screen(message, state, str(e))
        return
    await state.update_data(phone=phone)
    await ask_comment(message, state)
    await delete_saved_input(message)


@router.message(Form.phone)
async def choose_phone(message: Message, state: FSMContext):
    try:
        phone = phone_value(message.text or "")
    except ValueError as e:
        await show_screen(message, state, str(e))
        return
    await state.update_data(phone=phone)
    await ask_comment(message, state)
    await delete_saved_input(message)


async def ask_comment(message: Message, state: FSMContext):
    await state.set_state(Form.comment)
    await show_screen(
        message,
        state,
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
        await show_screen(message, state, "Комментарий должен быть не длиннее 1500 символов.")
        return
    await state.update_data(comment=comment)
    await state.set_state(Form.photos)
    await show_screen(
        message,
        state,
        "Пришлите до 5 фотографий объекта. Когда закончите, нажмите «Готово» или «Пропустить».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Готово"), KeyboardButton(text="Пропустить")],
                [KeyboardButton(text="↩️ Отмена")],
            ],
            resize_keyboard=True,
        ),
    )
    screen_id = (await state.get_data()).get(SCREEN_KEY, {}).get("message_id")
    if message.message_id != screen_id:
        await delete_saved_input(message)


@router.message(Form.photos)
async def finish_photos(message: Message, state: FSMContext, db: Database):
    if message.text not in {"Готово", "Пропустить"}:
        await show_screen(message, state, "Пришлите фотографию или нажмите «Готово».")
        return
    await flush_user_albums(message, state, db)
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
    await show_screen(
        message,
        state,
        html.escape("\n".join(lines)),
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
    receipt_screen = data.pop(SCREEN_KEY, None)
    selected_date = date.fromisoformat(data["date"])
    if selected_date < datetime.now(MOSCOW).date():
        await show_screen(message, state, "Выбранная дата уже прошла. Выберите новую дату.")
        await ask_date(message, state)
        return
    data["telegram_user"] = message.from_user.id
    try:
        await ensure_draft_photos(message.bot, db, message.from_user.id, data)
    except Exception:
        await show_screen(
            message,
            state,
            "❗ Не удалось сохранить фотографии в базе. Попробуйте подтвердить заявку ещё раз.",
        )
        return
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
        receipt_screen=receipt_screen,
    )
    await clear_flow(state)

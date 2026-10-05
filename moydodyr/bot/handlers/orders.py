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
from moydodyr.bot.services.photos import flush_user_albums
from moydodyr.bot.states import Form
from moydodyr.config import Settings
from moydodyr.db import Database
from moydodyr.domain import MOSCOW, area_value, date_value, estimate, phone_value, time_value

router = Router()
router.message.filter(F.chat.type == "private")


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

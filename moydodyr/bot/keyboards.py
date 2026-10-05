import calendar
from datetime import date, datetime, timedelta

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from moydodyr.domain import MOSCOW

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧹 Услуги и цены"), KeyboardButton(text="🧮 Рассчитать стоимость")],
        [KeyboardButton(text="📅 Оставить заявку"), KeyboardButton(text="🔥 Акции")],
        [KeyboardButton(text="⭐ Отзывы"), KeyboardButton(text="📞 Связаться с менеджером")],
        [KeyboardButton(text="❓ Частые вопросы")],
    ],
    resize_keyboard=True,
)


ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📬 Новые заявки"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="🛠 Заявки в работе")],
        [KeyboardButton(text="ℹ️ Команды администратора")],
    ],
    resize_keyboard=True,
)


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

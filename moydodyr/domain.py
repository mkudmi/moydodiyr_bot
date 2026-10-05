import re
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def area_value(text: str) -> str:
    try:
        area = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError("Введите площадь числом, например 65 или 65,5.") from None
    if not area.is_finite() or not 1 <= area <= 10000:
        raise ValueError("Площадь должна быть от 1 до 10 000 м².")
    if area.as_tuple().exponent < -2:
        raise ValueError("Укажите площадь с точностью не более двух знаков после запятой.")
    return str(area)


def phone_value(text: str) -> str:
    if not re.fullmatch(r"[+\d\s()\-]+", text.strip()):
        raise ValueError("Введите телефон, например +7 999 123-45-67.")
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Введите российский телефон: +7 и ещё 10 цифр.")
    return "+" + digits


def date_value(text: str, now: datetime | None = None) -> str:
    now = now or datetime.now(MOSCOW)
    try:
        day = datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        raise ValueError("Введите дату в формате ДД.ММ.ГГГГ.") from None
    if not now.date() <= day <= now.date() + timedelta(days=365):
        raise ValueError("Выберите дату от сегодняшней до года вперёд.")
    return day.isoformat()


def time_value(text: str) -> str:
    value = text.strip().lower()
    if value in {"любое", "любое время", "время не важно"}:
        return "Любое время"
    interval = re.fullmatch(r"(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})", value)
    if interval:
        try:
            start = datetime.strptime(interval.group(1), "%H:%M")
            end = datetime.strptime(interval.group(2), "%H:%M")
        except ValueError:
            raise ValueError("Выберите корректный временной интервал.") from None
        if start >= end:
            raise ValueError("Время окончания должно быть позже времени начала.")
        return f"{start:%H:%M}–{end:%H:%M}"
    try:
        return datetime.strptime(text.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError("Введите время в формате ЧЧ:ММ или «любое».") from None


def estimate(catalog: dict, draft: dict) -> dict | None:
    service = catalog["services"][draft["service"]]
    rate = service.get("rate_" + draft["object"])
    if rate is None or service.get("minimum") is None:
        return None
    total = max(Decimal(str(rate)) * Decimal(draft["area"]), Decimal(str(service["minimum"])))
    items = []
    extras = draft.get("extras", {})
    if isinstance(extras, list):
        extras = dict.fromkeys(extras, 1)
    for key, quantity in extras.items():
        extra = catalog["extras"].get(key)
        if extra is None or extra.get("price") is None:
            return None
        amount = Decimal(str(extra["price"])) * quantity
        total += amount
        items.append({"title": extra["title"], "quantity": quantity, "price": extra["price"]})
    return {
        "total": int(total.quantize(Decimal("1"), rounding=ROUND_CEILING)),
        "rate": rate,
        "minimum": service["minimum"],
        "items": items,
    }


def validate_catalog(catalog: dict) -> None:
    for key in ("welcome", "accepted", "promotions", "reviews", "faq"):
        if not isinstance(catalog.get(key), str) or not 1 <= len(catalog[key]) <= 3000:
            raise ValueError(f"{key}: требуется текст от 1 до 3000 символов")
    if set(catalog.get("objects", {})) != {"flat", "house"} or not catalog.get("services"):
        raise ValueError("Требуются объекты flat, house и хотя бы одна услуга")
    for group in ("services", "extras"):
        for key, item in catalog.get(group, {}).items():
            if not re.fullmatch(r"[a-z_]{1,24}", key):
                raise ValueError("Ключ услуги: латинские буквы и _, до 24 символов")
            if not item.get("title") or len(item["title"]) > 80:
                raise ValueError("Название услуги: 1–80 символов")
            if not isinstance(item.get("enabled"), bool):
                raise ValueError("enabled должен быть true или false")
            fields = ("rate_flat", "rate_house", "minimum") if group == "services" else ("price",)
            for field in fields:
                value = item.get(field)
                if value is not None:
                    try:
                        amount = Decimal(str(value))
                    except InvalidOperation:
                        raise ValueError("Тариф должен быть числом или null") from None
                    if not amount.is_finite() or not 0 <= amount <= 10000000:
                        raise ValueError("Тариф должен быть от 0 до 10 000 000")

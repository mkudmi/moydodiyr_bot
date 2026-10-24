import html
from datetime import date
from decimal import Decimal

from moydodyr.domain import MOSCOW

ORDER_STATUSES = {
    "new": "Новая",
    "working": "В работе",
    "agreed": "Согласована",
    "cancelled": "Отменена",
}


def display_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return value or "не указана"


def customer_order_text(order, catalog: dict, history) -> str:
    payload = order["payload"]
    service = (
        catalog["services"]
        .get(payload.get("service"), {})
        .get("title", payload.get("service", "—"))
    )
    obj = catalog["objects"].get(payload.get("object"), payload.get("object", "—"))
    lines = [
        f"Заявка №{order['id']}",
        f"Статус: {ORDER_STATUSES.get(order['status'], order['status'])}",
        f"Создана: {order['created_at'].astimezone(MOSCOW):%d.%m.%Y %H:%M}",
        f"Услуга: {service}",
        f"Объект: {obj}",
        f"Площадь: {str(payload.get('area', '—')).replace('.', ',')} м²",
        f"Адрес: {payload.get('address', '—')}",
        f"Желаемая дата: {display_date(payload.get('date'))}, {payload.get('time', '—')}",
    ]
    extras = [catalog["extras"].get(key, {}).get("title", key) for key in payload.get("extras", [])]
    if extras:
        lines.append("Дополнительные работы: " + ", ".join(extras))
    estimate = payload.get("estimate")
    if estimate and estimate.get("total") is not None:
        lines.append(f"Предварительно: {rubles(estimate['total'])} ₽")
    else:
        lines.append("Стоимость уточнит менеджер.")
    if payload.get("comment"):
        lines.append(f"Комментарий: {payload['comment']}")
    lines += [
        "",
        "История статусов:",
        f"• {order['created_at'].astimezone(MOSCOW):%d.%m.%Y %H:%M} — Заявка создана",
    ]
    for event in reversed(history):
        actor = " (отменена вами)" if event.get("actor_user_id") == order["user_id"] else ""
        lines.append(
            f"• {event['created_at'].astimezone(MOSCOW):%d.%m.%Y %H:%M} — "
            f"{ORDER_STATUSES.get(event['status'], event['status'])}{actor}"
        )
    return html.escape("\n".join(lines))


def rubles(value) -> str:
    return format(Decimal(str(value)).normalize(), ",f").replace(",", " ").replace(".", ",")


def services_text(catalog: dict) -> str:
    blocks = ["🧹 Услуги и цены\nУбираем квартиры и дома в Москве и Московской области."]
    for item in catalog["services"].values():
        if not item.get("enabled"):
            continue
        lines = [html.escape(item["title"])]
        for key, title in catalog["objects"].items():
            rate = item.get(f"rate_{key}")
            price = f"от {rubles(rate)} ₽/м²" if rate is not None else "цену уточнит менеджер"
            lines.append(f"• {html.escape(title)}: {price}")
        minimum = item.get("minimum")
        lines.append(
            f"Минимальная стоимость: {rubles(minimum)} ₽."
            if minimum is not None
            else "Минимальную стоимость уточнит менеджер."
        )
        blocks.append("\n".join(lines))
    if len(blocks) == 1:
        blocks.append("Доступные виды уборки уточните у менеджера.")
    extras = [
        f"• {html.escape(item['title'])}: "
        + (
            f"от {rubles(item['price'])} ₽"
            if item.get("price") is not None
            else "цену уточнит менеджер"
        )
        for item in catalog["extras"].values()
        if item.get("enabled")
    ]
    if extras:
        blocks.append("Дополнительные работы:\n" + "\n".join(extras))
    blocks.append(
        "Стоимость зависит от площади и объёма работ. Итог подтверждает менеджер.\n\n"
        "Нажмите «🧮 Рассчитать стоимость», чтобы узнать ориентировочную цену вашей уборки."
    )
    return "\n\n".join(blocks)


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

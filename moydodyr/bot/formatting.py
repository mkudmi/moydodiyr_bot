import html
from decimal import Decimal


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

import html


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

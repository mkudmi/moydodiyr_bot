from aiogram.fsm.state import State, StatesGroup


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

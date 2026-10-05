from aiogram import Router

from moydodyr.bot.handlers.admin import router as admin_router
from moydodyr.bot.handlers.contact import router as contact_router
from moydodyr.bot.handlers.fallback import router as fallback_router
from moydodyr.bot.handlers.menu import router as menu_router
from moydodyr.bot.handlers.orders import router as orders_router

router = Router()
router.include_router(menu_router)
router.include_router(admin_router)
router.include_router(orders_router)
router.include_router(contact_router)
router.include_router(fallback_router)

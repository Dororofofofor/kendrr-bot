import asyncio
import html
import json
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ====== НАСТРОЙКИ (задаются в Render, в коде менять не нужно) ======
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # ссылка на мини-приложение (HTTPS)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)  # ваш Telegram ID: сюда приходят заказы

CHANNEL_URL = "https://t.me/KendrrStoree"
DELIVERY_TEXT = (
    "🚚 <b>Доставка</b>\n\n"
    "Доставка идёт до г. Новосибирск 15-25 дней, цена 1100 ₽/кг.\n\n"
    "Как товар приезжает ко мне, я отправлю его вам любым удобным для вас "
    "способом (Яндекс/СДЭК/Авито и т.п.)."
)

RATE = 15          # курс: 1 ¥ = 15 ₽
COMMISSION = 1.3   # комиссия (множитель)
# ===================================================================

# Render сам задаёт адрес сервиса и порт
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "kendrr-store-secret")

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def open_shop_keyboard() -> ReplyKeyboardMarkup:
    # заказ из мини-приложения доходит до бота только если оно открыто кнопкой клавиатуры
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Оформить заказ", web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text="📢 Каналы"), KeyboardButton(text="🚚 Доставка")],
        ],
        resize_keyboard=True,
    )


@dp.message(CommandStart())
async def start(message: Message):
    name = html.escape(message.from_user.first_name or "друг")
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Это <b>Kendrr Store china</b>. Привезём для вас любые товары из Китая: "
        "кроссовки, одежду, электронику и многое другое.\n\n"
        "Чтобы оформить заказ, нажмите кнопку «🛍 Оформить заказ» внизу. "
        "В приложении можно:\n"
        "• добавить товары: ссылка, площадка, размер, количество и цена в юанях;\n"
        "• собрать корзину;\n"
        "• посчитать итоговую стоимость в рублях.",
        reply_markup=open_shop_keyboard(),
    )


def calc(price: float, qty: int) -> dict:
    cny = price * qty
    base = cny * RATE
    total = base * COMMISSION
    return {"cny": cny, "base": base, "total": total}


def rub(x: float) -> str:
    return f"{int(-(-x // 1)):,}".replace(",", " ")  # округление вверх


@dp.message(F.web_app_data)
async def on_order(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        items = data["items"]
        if not items:
            raise ValueError("пустой заказ")
    except Exception:
        await message.answer("Не удалось прочитать заказ. Попробуйте оформить его ещё раз.")
        return

    lines, sum_cny, sum_total = [], 0.0, 0.0
    for i, it in enumerate(items, 1):
        price, qty = float(it["price"]), int(it["qty"])
        r = calc(price, qty)
        sum_cny += r["cny"]
        sum_total += r["total"]
        lines.append(
            f"<b>{i}. {html.escape(str(it['platform']))}</b>\n"
            f"{html.escape(str(it['url']))}\n"
            f"Размер: {html.escape(str(it['size']))} · {qty} шт. · {price:g} ¥ за шт.\n"
            f"Сумма: {r['cny']:g} ¥ → {rub(r['total'])} ₽"
        )

    body = "\n\n".join(lines)
    summary = f"Итого: {sum_cny:g} ¥ → <b>{rub(sum_total)} ₽</b> (курс {RATE} ₽)"

    await message.answer(
        f"✅ Заказ принят!\n\n{body}\n\n{summary}\n\n"
        "Менеджер скоро свяжется с вами для подтверждения.",
        disable_web_page_preview=True,
    )

    if ADMIN_ID:
        u = message.from_user
        who = f"@{u.username}" if u.username else f"<a href='tg://user?id={u.id}'>{html.escape(u.full_name)}</a>"
        await bot.send_message(
            ADMIN_ID,
            f"🆕 Новый заказ от {who} (id {u.id})\n\n{body}\n\n{summary}",
            disable_web_page_preview=True,
        )


@dp.message(F.text == "📢 Каналы")
async def channels(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Открыть канал", url=CHANNEL_URL)]]
    )
    await message.answer("Наш канал Kendrr Store:", reply_markup=kb)


@dp.message(F.text == "🚚 Доставка")
async def delivery(message: Message):
    await message.answer(DELIVERY_TEXT)


@dp.message()
async def fallback(message: Message):
    await message.answer("Нажмите кнопку «🛍 Оформить заказ» внизу.", reply_markup=open_shop_keyboard())


async def on_startup(bot: Bot):
    await bot.set_webhook(
        f"{BASE_URL}{WEBHOOK_PATH}",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    logging.info("Webhook установлен: %s%s", BASE_URL, WEBHOOK_PATH)


async def health(request: web.Request) -> web.Response:
    # на этот адрес заходит UptimeRobot, чтобы бот не засыпал
    return web.Response(text="Kendrr Store bot is running")


def main():
    if not BOT_TOKEN or not WEBAPP_URL:
        raise SystemExit("Не заданы BOT_TOKEN и/или WEBAPP_URL")

    if BASE_URL:
        # режим для Render (webhook)
        dp.startup.register(on_startup)
        app = web.Application()
        app.router.add_get("/", health)
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        # запуск на своём компьютере для проверки (polling)
        async def poll():
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)

        asyncio.run(poll())


if __name__ == "__main__":
    main()

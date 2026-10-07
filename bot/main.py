"""UpdoUP · FastAPI + aiogram webhook."""

from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from fastapi import FastAPI, Request, Response

from bot.handlers import router
from bot.middlewares import LoggingMiddleware
from config import settings
from core.logger import setup_logger

logger = setup_logger("updo.main")

bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())
dp.callback_query.middleware(CallbackAnswerMiddleware())

dp.include_router(router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 UpdoUP v1.0.0 — старт")

    webhook_url = f"{settings.WEBHOOK_URL}/webhook"

    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    logger.info(f"✅ Webhook установлен: {webhook_url}")

    yield

    logger.info("🛑 Остановка. Удаляю webhook...")
    try:
        await bot.delete_webhook()
    except Exception as e:
        logger.error(f"delete_webhook: {e}")
    await bot.session.close()
    logger.info("🛑 Остановлено")


app = FastAPI(title="UpdoUP", version="1.0.0", lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.WEBHOOK_SECRET:
        logger.warning(f"🚫 Неверный secret: {secret}")
        return Response(status_code=403)

    try:
        data = await request.json()
        update = types.Update.model_validate(data, context={"bot": bot})
        logger.debug(f"📥 Update id={update.update_id}")
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.exception(f"❌ webhook error: {e}")

    return Response(status_code=200)


@app.get("/health")
async def health() -> dict:
    info = await bot.get_webhook_info()
    me = await bot.get_me()
    return {
        "status": "ok",
        "version": "1.0.0",
        "bot": me.username,
        "webhook": info.url,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)

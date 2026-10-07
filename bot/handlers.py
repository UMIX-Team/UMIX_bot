"""Telegram-хендлеры UpdoUP."""

import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, FSInputFile, Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from loadtest.reporter import generate_report
from loadtest.runner import LoadTestRunner

router = Router()
runner = LoadTestRunner()

_live_message_id: int | None = None
_live_chat_id: int | None = None
_last_update = 0.0


def _is_owner(user_id: int) -> bool:
    return user_id == settings.OWNER_ID


def _keyboard(running: bool):
    b = InlineKeyboardBuilder()
    if running:
        b.button(text="⏹ Стоп", callback_data="upd:stop")
        b.button(text="📊 Статус", callback_data="upd:status")
        b.adjust(2)
    else:
        b.button(text="🚀 Запустить тест", callback_data="upd:start")
        b.adjust(1)
    return b.as_markup()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    await message.answer(
        "👋 <b>UpdoUP</b>\n\n"
        "Telegram-бот для нагрузочных тестов.\n\n"
        f"Цель: <code>{settings.TARGET_URL}</code>\n"
        f"Макс VU: <b>{settings.MAX_VU}</b>\n"
        f"Автостоп: errors > {settings.ERROR_RATE_LIMIT:.0%}, "
        f"p95 > {settings.LATENCY_P95_LIMIT}ms\n\n"
        "Команды:\n"
        "/test — запустить тест\n"
        "/stop — остановить\n"
        "/status — текущие метрики\n"
        "/report — последний PNG-отчёт",
        parse_mode="HTML",
    )


@router.message(Command("test"))
async def cmd_test(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    if runner.running:
        await message.answer("⚠️ Тест уже идёт.")
        return
    await message.answer(
        "🚀 <b>Запуск нагрузочного теста</b>\n\n"
        f"Цель: <code>{settings.TARGET_URL}</code>\n"
        f"Рамп: 1 → 5 → 10 → 20 → {settings.MAX_VU}\n"
        "Автостоп: errors > 2%, p95 > 1500ms\n\n"
        "Готов?",
        reply_markup=_keyboard(running=False),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "upd:start")
async def cb_start(query: CallbackQuery) -> None:
    global _live_message_id, _live_chat_id
    if not _is_owner(query.from_user.id):
        return
    await query.answer("Запускаю...")

    msg = await query.message.answer("⏳ <b>Инициализация k6...</b>", parse_mode="HTML")
    _live_message_id = msg.message_id
    _live_chat_id = msg.chat.id

    runner.add_listener(on_metric)
    ok = await runner.start()
    if not ok:
        await query.message.answer("❌ Не удалось запустить k6.")


@router.callback_query(F.data == "upd:stop")
async def cb_stop(query: CallbackQuery) -> None:
    if not _is_owner(query.from_user.id):
        return
    await query.answer("Останавливаю...")
    await runner.stop()
    await query.message.answer("⏹ Тест остановлен вручную.")


@router.callback_query(F.data == "upd:status")
async def cb_status(query: CallbackQuery) -> None:
    if not _is_owner(query.from_user.id):
        return
    await query.answer("OK" if runner.running else "Тест не идёт")


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    if not runner.running:
        await message.answer("✅ Тест не идёт.")
        return
    await runner.stop()
    await message.answer("⏹ Остановлено.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    await message.answer("🟢 Идёт." if runner.running else "⚪ Не идёт.")


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    path = generate_report(test_name="updo", target_url=settings.TARGET_URL)
    if not path:
        await message.answer("❌ Нет данных для отчёта.")
        return
    await message.answer_photo(
        FSInputFile(path),
        caption="📊 <b>Отчёт UpdoUP</b>",
        parse_mode="HTML",
    )


async def on_metric(metrics: dict) -> None:
    global _last_update, _live_message_id, _live_chat_id

    if not _live_message_id:
        return

    now = datetime.utcnow().timestamp()
    if now - _last_update < 5 and "STOP" not in metrics:
        return
    _last_update = now

    if "STOP" in metrics:
        text = (
            f"🛑 <b>ТЕСТ ОСТАНОВЛЕН</b>\n\n"
            f"Причина: <code>{metrics['STOP']}</code>\n"
            f"VU: <b>{metrics.get('vu', 0)}</b>\n"
            f"Запросов: <b>{metrics.get('requests', 0):,}</b>"
        )
    else:
        err = metrics.get("error_rate", 0)
        p95 = metrics.get("p95", 0)
        if err > 0.02 or p95 > 1500:
            status = "🔴 деградация"
        elif err > 0.01 or p95 > 1000:
            status = "🟡 нагрузка"
        else:
            status = "🟢 ok"

        text = (
            f"🚀 <b>UpdoUP · Тест идёт</b>\n\n"
            f"VU: <b>{metrics.get('vu', 0)}</b>\n"
            f"Запросов: <b>{metrics.get('requests', 0):,}</b>\n"
            f"Errors: <b>{metrics.get('errors', 0)}</b> ({err:.1%})\n\n"
            f"Latency:\n"
            f"  avg: <b>{metrics.get('avg', 0):.0f}ms</b>\n"
            f"  p50: <b>{metrics.get('p50', 0):.0f}ms</b>\n"
            f"  p95: <b>{p95:.0f}ms</b>\n"
            f"  p99: <b>{metrics.get('p99', 0):.0f}ms</b>\n\n"
            f"Status: {status}"
        )

    try:
        from bot.main import bot
        await bot.edit_message_text(
            chat_id=_live_chat_id,
            message_id=_live_message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_keyboard(running=runner.running),
        )
    except Exception:
        pass

    if "STOP" in metrics:
        await asyncio.sleep(2)
        path = generate_report(test_name="updo", target_url=settings.TARGET_URL)
        if path:
            try:
                from bot.main import bot
                await bot.send_photo(
                    chat_id=_live_chat_id,
                    photo=FSInputFile(path),
                    caption="📊 <b>Итоговый отчёт</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        _live_message_id = None

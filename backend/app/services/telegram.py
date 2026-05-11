from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.models import Reminder, Target
from app.services.parser import extract_reminder_payload
from app.services.recurrence import describe_schedule, make_schedule_meta


logger = logging.getLogger(__name__)

PANEL_STATE_KEYS = ("panel_chat_id", "panel_message_id")


class ReminderStates(StatesGroup):
    waiting_text = State()


@dataclass
class BotClient:
    settings: Settings

    def __post_init__(self) -> None:
        self.bot = Bot(self.settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher(storage=MemoryStorage())
        self.router = Router()
        self._polling_task: asyncio.Task | None = None
        self._register_handlers()
        self.dp.include_router(self.router)

    async def start(self) -> None:
        if self._polling_task and not self._polling_task.done():
            return
        self._polling_task = asyncio.create_task(self.dp.start_polling(self.bot), name="telegram-polling")

    async def stop(self) -> None:
        await self.dp.stop_polling()
        if self._polling_task:
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        await self.bot.session.close()

    async def notify_admin_startup(self) -> None:
        text = (
            "\N{ROCKET} <b>Muad Reminder Bot запущен</b>\n"
            f"Панель: <code>{self.settings.public_base_url}</code>\n"
            "Меню: /menu\n"
            "Привязка топика: отправьте /bind в нужном чате или теме."
        )
        try:
            await self.bot.send_message(self.settings.admin_user_id, text)
        except Exception:
            logger.exception("Unable to notify admin about startup")

    async def send_reminder(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        payload = f"\N{ALARM CLOCK} <b>Напоминание</b>\n{text}"
        try:
            await self.bot.send_message(chat_id, payload, message_thread_id=thread_id)
        except TelegramBadRequest:
            logger.exception("Failed to send reminder to chat_id=%s thread_id=%s", chat_id, thread_id)

    async def send_test_message(self, chat_id: int, thread_id: int | None = None) -> None:
        await self.bot.send_message(
            chat_id,
            "\N{TEST TUBE} <b>Тест уведомления</b>\nСвязка чата и топика работает корректно.",
            message_thread_id=thread_id,
        )

    def _register_handlers(self) -> None:
        self.router.message.register(self.cmd_start, CommandStart())
        self.router.message.register(self.cmd_menu, Command("menu"))
        self.router.message.register(self.cmd_bind, Command("bind"))
        self.router.message.register(self.cmd_targets, Command("targets"))
        self.router.message.register(self.cmd_new, Command("new"))
        self.router.message.register(self.cmd_testtopic, Command("testtopic"))
        self.router.callback_query.register(self.on_callback)
        self.router.message.register(self.on_text_input, ReminderStates.waiting_text)
        self.router.inline_query.register(self.on_inline_query)

    async def cmd_start(self, message: Message, state: FSMContext, command: CommandObject) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("\N{WAVING HAND SIGN} Для настройки откройте бота в личных сообщениях.")
            return

        await self._reset_flow(state)

        if command.args and command.args.startswith("inline_"):
            try:
                raw = base64.urlsafe_b64decode(command.args.removeprefix("inline_").encode()).decode()
            except Exception:
                raw = ""
            if raw:
                await state.update_data(prefill_text=raw)
                await self._send_target_picker(message, state, source="inline")
                return

        await self._show_panel(message, state, self._welcome_text(message), self._main_menu())

    async def cmd_menu(self, message: Message, state: FSMContext) -> None:
        await self._reset_flow(state)
        await self._show_panel(message, state, self._welcome_text(message), self._main_menu())

    async def cmd_bind(self, message: Message) -> None:
        if message.chat.type == ChatType.PRIVATE:
            await message.answer("\N{LINK SYMBOL} Команду /bind нужно отправить в целевом чате или в конкретном топике.")
            return

        thread_id = message.message_thread_id if getattr(message, "is_topic_message", False) else None
        thread_title = f"Топик #{thread_id}" if thread_id else None

        async with SessionLocal() as session:
            stmt = select(Target).where(Target.chat_id == message.chat.id, Target.thread_id == thread_id)
            target = await session.scalar(stmt)
            if target is None:
                target = Target(
                    chat_id=message.chat.id,
                    chat_title=message.chat.title or str(message.chat.id),
                    chat_type=message.chat.type,
                    thread_id=thread_id,
                    thread_title=thread_title,
                    linked_by_user_id=message.from_user.id if message.from_user else self.settings.admin_user_id,
                )
                session.add(target)
            else:
                target.is_active = True
                target.chat_title = message.chat.title or target.chat_title
                target.thread_title = thread_title or target.thread_title
            await session.commit()

        suffix = f" / {thread_title}" if thread_title else ""
        await message.answer(f"\N{WHITE HEAVY CHECK MARK} Привязка сохранена: <b>{message.chat.title}</b>{suffix}")

    async def cmd_targets(self, message: Message, state: FSMContext) -> None:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())

        if message.chat.type == ChatType.PRIVATE:
            await self._show_panel(message, state, self._targets_overview_text(targets), self._main_menu())
            return

        if not targets:
            await message.answer("\N{OPEN MAILBOX WITH LOWERED FLAG} Пока нет привязанных чатов. Сначала используйте /bind в целевом чате.")
            return
        text = "\n".join(f"- {target.display_name}" for target in targets)
        await message.answer(f"\N{PUSHPIN} <b>Доступные цели</b>\n{text}")

    async def cmd_new(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("\N{HAMMER AND WRENCH} Создание напоминаний запускается в личном чате с ботом.")
            return
        await self._reset_flow(state)
        await self._send_target_picker(message, state, source="manual")

    async def cmd_testtopic(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("\N{TEST TUBE} Тест отправляется из личного чата с ботом.")
            return
        await self._reset_flow(state)
        await self._show_panel(message, state, self._test_picker_text(), await self._targets_keyboard("test"))

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return

        data = callback.data or ""
        if data == "menu:new":
            await self._reset_flow(state)
            await self._send_target_picker(callback.message, state, source="manual")
            await callback.answer()
            return

        if data == "menu:targets":
            async with SessionLocal() as session:
                targets = list(
                    (await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all()
                )
            await self._show_panel(callback.message, state, self._targets_overview_text(targets), self._main_menu())
            await callback.answer()
            return

        if data == "menu:test":
            await self._show_panel(callback.message, state, self._test_picker_text(), await self._targets_keyboard("test"))
            await callback.answer()
            return

        if data == "menu:home":
            await self._reset_flow(state)
            await self._show_panel(callback.message, state, self._welcome_text(callback.message), self._main_menu())
            await callback.answer()
            return

        if data.startswith("pick-target:"):
            target_id = int(data.split(":", 1)[1])
            async with SessionLocal() as session:
                target = await session.get(Target, target_id)
            if target is None:
                await self._show_panel(
                    callback.message,
                    state,
                    "\N{WARNING SIGN} Цель не найдена. Обновите список привязок через /bind и попробуйте снова.",
                    self._main_menu(),
                )
                await callback.answer()
                return

            await state.update_data(target_id=target_id, target_name=target.display_name)
            state_data = await state.get_data()
            prefill = state_data.get("prefill_text")

            if prefill:
                if await self._show_preview_from_raw(callback.message, state, prefill):
                    await callback.answer("Цель выбрана")
                    return

            await state.set_state(ReminderStates.waiting_text)
            await self._show_panel(
                callback.message,
                state,
                self._prompt_text(target.display_name),
                self._prompt_keyboard(),
            )
            await callback.answer("Цель выбрана")
            return

        if data.startswith("save-reminder:"):
            schedule_type = data.split(":", 1)[1]
            state_data = await state.get_data()
            try:
                async with SessionLocal() as session:
                    target = await session.get(Target, state_data["target_id"])
                    if target is None:
                        await self._show_panel(
                            callback.message,
                            state,
                            "\N{WARNING SIGN} Цель не найдена. Привяжите чат заново через /bind.",
                            self._main_menu(),
                        )
                        await callback.answer()
                        return

                    start_at = state_data["start_at"]
                    reminder = Reminder(
                        target_id=target.id,
                        text=state_data["text"],
                        source_text=state_data["source_text"],
                        schedule_type=schedule_type,
                        schedule_meta=make_schedule_meta(schedule_type, start_at),
                        timezone=self.settings.default_timezone,
                        start_at=start_at.astimezone(timezone.utc),
                        next_run_at=start_at.astimezone(timezone.utc),
                        created_by_user_id=callback.from_user.id,
                        is_active=True,
                    )
                    session.add(reminder)
                    await session.commit()

                target_name = state_data.get("target_name", target.display_name)
                success_text = (
                    "\N{WHITE HEAVY CHECK MARK} <b>Напоминание сохранено</b>\n"
                    f"Куда: <b>{target_name}</b>\n"
                    f"Текст: <b>{state_data['text']}</b>\n"
                    f"Когда: <b>{state_data['start_at'].strftime('%d.%m.%Y %H:%M')}</b>\n"
                    f"Повтор: <b>{describe_schedule(schedule_type)}</b>\n\n"
                    "Можно сразу создать следующее напоминание или вернуться в меню."
                )
                await self._reset_flow(state)
                await self._show_panel(callback.message, state, success_text, self._saved_keyboard())
            except Exception:
                logger.exception("Failed to save reminder")
                await self._show_panel(
                    callback.message,
                    state,
                    "\N{WARNING SIGN} Не получилось сохранить напоминание. Проверьте настройки и попробуйте снова.",
                    self._main_menu(),
                )
            await callback.answer()
            return

        if data.startswith("test-target:"):
            target_id = int(data.split(":", 1)[1])
            async with SessionLocal() as session:
                target = await session.get(Target, target_id)
            if target is not None:
                await self.send_test_message(target.chat_id, target.thread_id)
                await self._show_panel(
                    callback.message,
                    state,
                    (
                        "\N{TEST TUBE} <b>Тест отправлен</b>\n"
                        f"Цель: <b>{target.display_name}</b>\n\n"
                        "Если сообщение пришло в нужный чат или топик, привязка настроена правильно."
                    ),
                    self._post_test_keyboard(),
                )
            else:
                await self._show_panel(
                    callback.message,
                    state,
                    "\N{WARNING SIGN} Не удалось найти цель для теста.",
                    self._main_menu(),
                )
            await callback.answer("Готово")
            return

        await callback.answer()

    async def on_text_input(self, message: Message, state: FSMContext) -> None:
        state_data = await state.get_data()
        raw_text = (message.text or state_data.get("prefill_text") or "").strip()
        if message.chat.type == ChatType.PRIVATE:
            await self._safe_delete(message)

        if not raw_text:
            await self._show_panel(
                message,
                state,
                "\N{WARNING SIGN} Не вижу текста для разбора.\n\nОтправьте сообщение в формате: <code>завтра в 15:00 оплатить подписку</code>",
                self._prompt_keyboard(),
            )
            return

        await self._show_preview_from_raw(message, state, raw_text)

    async def on_inline_query(self, inline_query: InlineQuery) -> None:
        query = (inline_query.query or "").strip()
        if not query:
            await inline_query.answer(
                results=[
                    InlineQueryResultArticle(
                        id="empty",
                        title="Напишите напоминание",
                        description="Пример: завтра в 15:00 оплатить подписку",
                        input_message_content=InputTextMessageContent(
                            message_text="\N{HOURGLASS WITH FLOWING SAND} Введите текст напоминания после имени бота."
                        ),
                    )
                ],
                cache_time=1,
                is_personal=True,
            )
            return

        try:
            reminder_text, due_at = extract_reminder_payload(query, self.settings.default_timezone)
            preview = f"{due_at.strftime('%d.%m %H:%M')} - {reminder_text}"
        except ValueError:
            reminder_text = None
            due_at = None
            preview = "Не удалось точно распознать дату. Откроем мастер создания в личном чате."

        payload = base64.urlsafe_b64encode(query.encode()).decode()
        deep_link = f"https://t.me/{quote(self.settings.bot_username)}?start=inline_{payload}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="\N{FLOPPY DISK} Сохранить через бота", url=deep_link)]]
        )

        results = [
            InlineQueryResultArticle(
                id="create-reminder",
                title="Создать напоминание",
                description=preview,
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "\N{HOURGLASS WITH FLOWING SAND} <b>Черновик напоминания</b>\n"
                        f"{query}\n\n"
                        "Нажмите кнопку ниже, чтобы выбрать чат и сохранить напоминание."
                    )
                ),
                reply_markup=keyboard,
            )
        ]
        if reminder_text and due_at:
            results.append(
                InlineQueryResultArticle(
                    id="preview-reminder",
                    title="Отправить превью без сохранения",
                    description="Просто вставит в чат красивое превью",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            "\N{PUSHPIN} <b>Будущее напоминание</b>\n"
                            f"Когда: {due_at.strftime('%d.%m.%Y %H:%M')}\n"
                            f"Что: {reminder_text}"
                        )
                    ),
                )
            )
        await inline_query.answer(results=results, cache_time=1, is_personal=True)

    async def _reset_flow(self, state: FSMContext) -> None:
        data = await state.get_data()
        preserved = {key: data.get(key) for key in PANEL_STATE_KEYS if data.get(key) is not None}
        await state.clear()
        if preserved:
            await state.update_data(**preserved)

    async def _show_preview_from_raw(self, message: Message, state: FSMContext, raw_text: str) -> bool:
        try:
            reminder_text, due_at = extract_reminder_payload(raw_text, self.settings.default_timezone)
        except ValueError as exc:
            await state.set_state(ReminderStates.waiting_text)
            await self._show_panel(
                message,
                state,
                (
                    "\N{WARNING SIGN} Не получилось разобрать дату.\n"
                    f"{exc}\n\n"
                    "Попробуйте снова. Пример: <code>завтра в 15:00 оплатить подписку</code>"
                ),
                self._prompt_keyboard(),
            )
            return False

        await state.update_data(text=reminder_text, start_at=due_at, source_text=raw_text)
        await state.set_state(None)
        await self._show_panel(
            message,
            state,
            self._preview_text(await state.get_data(), reminder_text, due_at),
            self._schedule_keyboard(),
        )
        return True

    async def _show_panel(
        self,
        message: Message,
        state: FSMContext,
        text: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        data = await state.get_data()
        panel_chat_id = data.get("panel_chat_id")
        panel_message_id = data.get("panel_message_id")

        if panel_chat_id == message.chat.id and panel_message_id:
            try:
                await self.bot.edit_message_text(
                    text=text,
                    chat_id=panel_chat_id,
                    message_id=panel_message_id,
                    reply_markup=reply_markup,
                )
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
                logger.debug("Unable to edit panel message, sending a new one instead: %s", exc)

        sent = await message.answer(text, reply_markup=reply_markup)
        await state.update_data(panel_chat_id=sent.chat.id, panel_message_id=sent.message_id)

    async def _safe_delete(self, message: Message) -> None:
        try:
            await message.delete()
        except TelegramBadRequest:
            return

    async def _send_target_picker(self, message: Message, state: FSMContext, source: str) -> None:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        if not targets:
            await self._show_panel(
                message,
                state,
                "\N{OPEN MAILBOX WITH LOWERED FLAG} Нет доступных целей.\n\nСначала отправьте <code>/bind</code> в нужный чат или топик, а затем вернитесь сюда.",
                self._main_menu(),
            )
            return

        source_label = "для inline-черновика" if source == "inline" else "для нового напоминания"
        text = (
            "\N{DIRECT HIT} <b>Выберите цель</b>\n"
            f"Куда сохранить напоминание {source_label}?\n\n"
            "После выбора бот откроет тот же экран и продолжит настройку без лишних сообщений."
        )
        await self._show_panel(message, state, text, await self._targets_keyboard("pick"))

    async def _targets_keyboard(self, action: str) -> InlineKeyboardMarkup:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        callback_prefix = "pick-target" if action == "pick" else "test-target"
        rows = [
            [InlineKeyboardButton(text=f"\N{ROUND PUSHPIN} {target.display_name}", callback_data=f"{callback_prefix}:{target.id}")]
            for target in targets
        ]
        rows.append([InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} Главное меню", callback_data="menu:home")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _welcome_text(self, message: Message) -> str:
        lines = [
            "\N{WAVING HAND SIGN} <b>Muad Reminder Bot</b>",
            "Один аккуратный мастер для создания и проверки напоминаний без засорения личного чата.",
            "",
            "Быстрый старт:",
            "1. Отправьте <code>/bind</code> в нужный чат или тему.",
            "2. Нажмите кнопку ниже и выберите цель.",
            f"3. Для быстрых сценариев используйте inline-запрос: <code>@{self.settings.bot_username}</code>",
        ]
        if message.from_user and message.from_user.id == self.settings.admin_user_id:
            lines.extend(["", "\N{SHIELD} Вы определены как администратор проекта."])
        return "\n".join(lines)

    def _targets_overview_text(self, targets: list[Target]) -> str:
        if not targets:
            return "\N{OPEN MAILBOX WITH LOWERED FLAG} Пока нет привязанных чатов.\n\nСначала отправьте <code>/bind</code> в нужный чат или топик."

        body = "\n".join(f"• <b>{target.display_name}</b>" for target in targets)
        return (
            "\N{PUSHPIN} <b>Привязанные цели</b>\n"
            "Ниже список чатов и топиков, куда бот может отправлять напоминания.\n\n"
            f"{body}"
        )

    def _test_picker_text(self) -> str:
        return (
            "\N{TEST TUBE} <b>Проверка доставки</b>\n"
            "Выберите чат или топик, и бот отправит туда тестовое уведомление.\n\n"
            "Этот экран останется тем же: мы просто обновим его результатом проверки."
        )

    def _prompt_text(self, target_name: str) -> str:
        return (
            "\N{WRITING HAND} <b>Введите текст напоминания</b>\n"
            f"Цель: <b>{target_name}</b>\n\n"
            "Напишите сообщение в свободной форме.\n"
            "Пример: <code>завтра в 15:00 оплатить подписку</code>"
        )

    def _preview_text(self, state_data: dict, reminder_text: str, due_at: datetime) -> str:
        target_name = state_data.get("target_name", "не выбрано")
        return (
            "\N{MEMO} <b>Проверьте данные</b>\n"
            f"Куда: <b>{target_name}</b>\n"
            f"Текст: <b>{reminder_text}</b>\n"
            f"Когда: <b>{due_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "Выберите режим повторения:"
        )

    def _main_menu(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\N{HEAVY PLUS SIGN} Создать напоминание", callback_data="menu:new")],
                [InlineKeyboardButton(text="\N{PUSHPIN} Привязанные чаты", callback_data="menu:targets")],
                [InlineKeyboardButton(text="\N{TEST TUBE} Тест топика", callback_data="menu:test")],
            ]
        )

    def _prompt_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} Главное меню", callback_data="menu:home")],
            ]
        )

    def _saved_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\N{HEAVY PLUS SIGN} Создать ещё одно", callback_data="menu:new")],
                [InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} В главное меню", callback_data="menu:home")],
            ]
        )

    def _post_test_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\N{TEST TUBE} Проверить другую цель", callback_data="menu:test")],
                [InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} В главное меню", callback_data="menu:home")],
            ]
        )

    def _schedule_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="1\N{COMBINING ENCLOSING KEYCAP} Один раз", callback_data="save-reminder:once")],
                [InlineKeyboardButton(text="\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} Каждый день", callback_data="save-reminder:daily")],
                [InlineKeyboardButton(text="\N{SPIRAL CALENDAR PAD} Каждую неделю", callback_data="save-reminder:weekly")],
                [InlineKeyboardButton(text="\N{CALENDAR} Каждый месяц", callback_data="save-reminder:monthly")],
                [InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} В главное меню", callback_data="menu:home")],
            ]
        )

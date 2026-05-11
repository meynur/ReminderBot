from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import timezone
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
            "\N{ROCKET} <b>Muad Reminder Bot zapushchen</b>\n"
            f"Panel: <code>{self.settings.public_base_url}</code>\n"
            "Menyu: /menu\n"
            "Privyazka topika: otprav'te /bind v nuzhnom chate ili teme."
        )
        try:
            await self.bot.send_message(self.settings.admin_user_id, text)
        except Exception:
            logger.exception("Unable to notify admin about startup")

    async def send_reminder(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        payload = f"\N{ALARM CLOCK} <b>Napominanie</b>\n{text}"
        try:
            await self.bot.send_message(chat_id, payload, message_thread_id=thread_id)
        except TelegramBadRequest:
            logger.exception("Failed to send reminder to chat_id=%s thread_id=%s", chat_id, thread_id)

    async def send_test_message(self, chat_id: int, thread_id: int | None = None) -> None:
        await self.bot.send_message(
            chat_id,
            "\N{TEST TUBE} <b>Test uvedomleniya</b>\nSvyazka chata i topika rabotaet korrektno.",
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
            await message.answer("\N{WAVING HAND SIGN} Dlya nastroiki otkroyte bota v lichnykh soobshcheniyakh.")
            return

        if command.args and command.args.startswith("inline_"):
            try:
                raw = base64.urlsafe_b64decode(command.args.removeprefix("inline_").encode()).decode()
            except Exception:
                raw = ""
            if raw:
                await state.update_data(prefill_text=raw)
                await self._send_target_picker(message, source="inline")
                return

        lines = [
            "\N{WAVING HAND SIGN} <b>Muad Reminder Bot</b>",
            "Ya pomogu sokhranyat' razovye i regulyarnye napominaniya v vybrannye chaty i topiki.",
            "",
            "Bystryy start:",
            "1. Otprav'te <code>/bind</code> v nuzhnom chate ili teme.",
            "2. Vernites' syuda i nazhmite \"Sozdat' napominanie\".",
            f"3. Dlya bystrykh stsenariev ispol'zuyte inline-zapros: <code>@{self.settings.bot_username}</code>",
        ]
        if message.from_user and message.from_user.id == self.settings.admin_user_id:
            lines.extend(["", "\N{SHIELD} Vy opredeleny kak administrator proekta."])

        await message.answer("\n".join(lines), reply_markup=self._main_menu())

    async def cmd_menu(self, message: Message) -> None:
        await message.answer("\N{CLIPBOARD} Glavnoe menyu", reply_markup=self._main_menu())

    async def cmd_bind(self, message: Message) -> None:
        if message.chat.type == ChatType.PRIVATE:
            await message.answer("\N{LINK SYMBOL} Komandu /bind nuzhno otpravit' v tselevom chate ili v konkretnom topike.")
            return

        thread_id = message.message_thread_id if getattr(message, "is_topic_message", False) else None
        thread_title = f"Topic #{thread_id}" if thread_id else None

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
        await message.answer(f"\N{WHITE HEAVY CHECK MARK} Privyazka sokhranena: <b>{message.chat.title}</b>{suffix}")

    async def cmd_targets(self, message: Message) -> None:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        if not targets:
            await message.answer("\N{OPEN MAILBOX WITH LOWERED FLAG} Poka net privyazannykh chatov. Snachala ispol'zuyte /bind v tselevom chate.")
            return
        text = "\n".join(f"- {target.display_name}" for target in targets)
        await message.answer(f"\N{PUSHPIN} <b>Dostupnye tseli</b>\n{text}")

    async def cmd_new(self, message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("\N{HAMMER AND WRENCH} Sozdanie napominaniy zapuskaetsya v lichnom chate s botom.")
            return
        await state.clear()
        await self._send_target_picker(message, source="manual")

    async def cmd_testtopic(self, message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("\N{TEST TUBE} Test otpravlyaetsya iz lichnogo chata s botom.")
            return
        await message.answer("Vyberite chat ili topik dlya testa:", reply_markup=await self._targets_keyboard("test"))

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        if data == "menu:new":
            await state.clear()
            await self._send_target_picker(callback.message, source="manual")
            await callback.answer()
            return
        if data == "menu:targets":
            await callback.answer()
            await self.cmd_targets(callback.message)
            return
        if data == "menu:test":
            await callback.answer()
            await callback.message.answer("Vyberite chat ili topik dlya testa:", reply_markup=await self._targets_keyboard("test"))
            return
        if data == "menu:home":
            await callback.answer()
            await callback.message.answer("\N{CLIPBOARD} Glavnoe menyu", reply_markup=self._main_menu())
            return

        if data.startswith("pick-target:"):
            target_id = int(data.split(":", 1)[1])
            await state.update_data(target_id=target_id)
            state_data = await state.get_data()
            prefill = state_data.get("prefill_text")
            await state.set_state(ReminderStates.waiting_text)
            prompt = (
                "\N{WRITING HAND} Napishite tekst napominaniya.\n"
                "Primer: <code>zavtra v 15:00 oplatit' podpisku</code>"
            )
            if prefill:
                prompt = (
                    "\N{WRITING HAND} Chernovik iz inline-rezhima uzhe podstavlen nizhe.\n"
                    "Mozhete otpravit' ego kak est' ili otredaktirovat':\n"
                    f"<code>{prefill}</code>"
                )
            await callback.message.answer(prompt)
            await callback.answer("Tsel' vybrana")
            return

        if data.startswith("save-reminder:"):
            schedule_type = data.split(":", 1)[1]
            state_data = await state.get_data()
            try:
                async with SessionLocal() as session:
                    target = await session.get(Target, state_data["target_id"])
                    if target is None:
                        await callback.message.answer("\N{WARNING SIGN} Tsel' ne naydena. Privyazhite chat zanovo cherez /bind.")
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
                    )
                    session.add(reminder)
                    await session.commit()
                await state.clear()
                await callback.message.answer(
                    "\N{WHITE HEAVY CHECK MARK} Napominanie sokhraneno\n"
                    f"Tekst: <b>{state_data['text']}</b>\n"
                    f"Povtor: <b>{describe_schedule(schedule_type)}</b>"
                )
            except Exception:
                logger.exception("Failed to save reminder")
                await callback.message.answer(
                    "\N{WARNING SIGN} Ne poluchilos' sokhranit' napominanie. Proverte nastroiki i poprobuyte snova."
                )
            await callback.answer()
            return

        if data.startswith("test-target:"):
            target_id = int(data.split(":", 1)[1])
            async with SessionLocal() as session:
                target = await session.get(Target, target_id)
            if target is not None:
                await self.send_test_message(target.chat_id, target.thread_id)
                await callback.message.answer(f"\N{TEST TUBE} Test otpravlen v <b>{target.display_name}</b>")
            await callback.answer("Gotovo")
            return

        await callback.answer()

    async def on_text_input(self, message: Message, state: FSMContext) -> None:
        state_data = await state.get_data()
        raw_text = (message.text or state_data.get("prefill_text") or "").strip()
        if not raw_text:
            await message.answer("\N{WARNING SIGN} Ne vizhu teksta dlya razbora. Poprobuyte eshche raz.")
            return

        try:
            reminder_text, due_at = extract_reminder_payload(raw_text, self.settings.default_timezone)
        except ValueError as exc:
            await message.answer(f"\N{WARNING SIGN} {exc}")
            return

        await state.update_data(text=reminder_text, start_at=due_at, source_text=raw_text)
        await message.answer(
            "\N{MEMO} <b>Proverte dannye</b>\n"
            f"Tekst: <b>{reminder_text}</b>\n"
            f"Kogda: <b>{due_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "Vyberite rezhim povtoreniya:",
            reply_markup=self._schedule_keyboard(),
        )

    async def on_inline_query(self, inline_query: InlineQuery) -> None:
        query = (inline_query.query or "").strip()
        if not query:
            await inline_query.answer(
                results=[
                    InlineQueryResultArticle(
                        id="empty",
                        title="Napishete napominanie",
                        description="Primer: zavtra v 15:00 oplatit' podpisku",
                        input_message_content=InputTextMessageContent(
                            message_text="\N{HOURGLASS WITH FLOWING SAND} Vvedite tekst napominaniya posle imeni bota."
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
            preview = "Ne udalos' tochno raspoznat' datu. Otkroem master sozdaniya v lichnom chate."

        payload = base64.urlsafe_b64encode(query.encode()).decode()
        deep_link = f"https://t.me/{quote(self.settings.bot_username)}?start=inline_{payload}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="\N{FLOPPY DISK} Sokhranit' cherez bota", url=deep_link)]]
        )

        results = [
            InlineQueryResultArticle(
                id="create-reminder",
                title="Sozdat' napominanie",
                description=preview,
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "\N{HOURGLASS WITH FLOWING SAND} <b>Chernovik napominaniya</b>\n"
                        f"{query}\n\n"
                        "Nazhmite knopku nizhe, chtoby vybrat' chat i sokhranit' napominanie."
                    )
                ),
                reply_markup=keyboard,
            )
        ]
        if reminder_text and due_at:
            results.append(
                InlineQueryResultArticle(
                    id="preview-reminder",
                    title="Otpravit' prevyu bez sokhraneniya",
                    description="Prosto vstavit v chat krasivoe predsmotrenie",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            "\N{PUSHPIN} <b>Budushchee napominanie</b>\n"
                            f"Kogda: {due_at.strftime('%d.%m.%Y %H:%M')}\n"
                            f"Chto: {reminder_text}"
                        )
                    ),
                )
            )
        await inline_query.answer(results=results, cache_time=1, is_personal=True)

    async def _send_target_picker(self, message: Message, source: str) -> None:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        if not targets:
            await message.answer("\N{OPEN MAILBOX WITH LOWERED FLAG} Net dostupnykh tseley. Snachala otprav'te /bind v nuzhnyy chat ili topik.")
            return
        source_text = "iz inline-rezhima" if source == "inline" else "dlya novogo napominaniya"
        await message.answer(f"\N{DIRECT HIT} Vyberite tsel' {source_text}:", reply_markup=await self._targets_keyboard("pick"))

    async def _targets_keyboard(self, action: str) -> InlineKeyboardMarkup:
        async with SessionLocal() as session:
            targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        callback_prefix = "pick-target" if action == "pick" else "test-target"
        rows = [
            [InlineKeyboardButton(text=f"\N{ROUND PUSHPIN} {target.display_name}", callback_data=f"{callback_prefix}:{target.id}")]
            for target in targets
        ]
        rows.append([InlineKeyboardButton(text="\N{LEFTWARDS BLACK ARROW} Glavnoe menyu", callback_data="menu:home")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _main_menu(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\N{HEAVY PLUS SIGN} Sozdat' napominanie", callback_data="menu:new")],
                [InlineKeyboardButton(text="\N{PUSHPIN} Privyazannye chaty", callback_data="menu:targets")],
                [InlineKeyboardButton(text="\N{TEST TUBE} Test topika", callback_data="menu:test")],
            ]
        )

    def _schedule_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="1\N{COMBINING ENCLOSING KEYCAP} Odin raz", callback_data="save-reminder:once")],
                [InlineKeyboardButton(text="\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} Kazhdyy den'", callback_data="save-reminder:daily")],
                [InlineKeyboardButton(text="\N{SPIRAL CALENDAR PAD} Kazhduyu nedelyu", callback_data="save-reminder:weekly")],
                [InlineKeyboardButton(text="\N{CALENDAR} Kazhdyy mesyats", callback_data="save-reminder:monthly")],
            ]
        )

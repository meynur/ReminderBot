from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Reminder
from app.services.recurrence import compute_next_run


logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, bot_client: "BotClient", poll_interval: int = 15) -> None:
        self.bot_client = bot_client
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="reminder-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler iteration failed")
            await asyncio.sleep(self.poll_interval)

    async def tick(self) -> None:
        if self._lock.locked():
            return

        async with self._lock:
            async with SessionLocal() as session:
                now = datetime.now(timezone.utc)
                stmt = (
                    select(Reminder)
                    .where(Reminder.is_active.is_(True), Reminder.next_run_at <= now)
                    .order_by(Reminder.next_run_at.asc())
                    .limit(20)
                )
                reminders = list((await session.scalars(stmt)).all())

                for reminder in reminders:
                    target = reminder.target
                    await self.bot_client.send_reminder(
                        chat_id=target.chat_id,
                        text=reminder.text,
                        thread_id=target.thread_id,
                    )
                    reminder.last_sent_at = now
                    next_run = compute_next_run(reminder.schedule_type, reminder.next_run_at)
                    if next_run is None:
                        reminder.is_active = False
                    else:
                        reminder.next_run_at = next_run
                await session.commit()


from app.services.telegram import BotClient  # noqa: E402


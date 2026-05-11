from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Target(Base):
    __tablename__ = "targets"
    __table_args__ = (UniqueConstraint("chat_id", "thread_id", name="uq_target_chat_thread"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False, default="private")
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linked_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    reminders: Mapped[list["Reminder"]] = relationship(back_populates="target", lazy="selectin")

    @property
    def display_name(self) -> str:
        if self.thread_id:
            suffix = self.thread_title or f"Топик #{self.thread_id}"
            return f"{self.chat_title} / {suffix}"
        return self.chat_title


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False, default="once")
    schedule_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    target: Mapped[Target] = relationship(back_populates="reminders", lazy="joined")

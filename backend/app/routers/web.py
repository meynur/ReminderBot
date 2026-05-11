from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Reminder, Target
from app.services.recurrence import describe_schedule, make_schedule_meta


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _to_local(dt: datetime, timezone_name: str) -> str:
    return dt.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m.%Y %H:%M")


def _flash_from_status(status: str | None) -> dict[str, str] | None:
    messages = {
        "created": {
            "kind": "success",
            "title": "Напоминание создано",
            "text": "Новое напоминание сохранено и уже доступно в общем списке ниже.",
        },
        "invalid-target": {
            "kind": "error",
            "title": "Цель не найдена",
            "text": "Выберите существующий чат или топик из списка привязок и попробуйте снова.",
        },
        "invalid-date": {
            "kind": "error",
            "title": "Некорректная дата",
            "text": "Проверьте дату и время первого запуска, затем отправьте форму ещё раз.",
        },
        "empty-text": {
            "kind": "error",
            "title": "Пустой текст",
            "text": "Добавьте текст напоминания, чтобы бот понимал, что именно нужно отправить.",
        },
    }
    return messages.get(status)


async def _build_dashboard_context(request: Request, status: str | None = None) -> dict:
    settings = get_settings()
    async with SessionLocal() as session:
        reminders = list((await session.scalars(select(Reminder).order_by(Reminder.next_run_at.asc()))).all())
        targets = list((await session.scalars(select(Target).where(Target.is_active.is_(True)).order_by(Target.chat_title))).all())
        total_reminders = await session.scalar(select(func.count(Reminder.id)).where(Reminder.is_active.is_(True)))
        total_targets = await session.scalar(select(func.count(Target.id)).where(Target.is_active.is_(True)))

    default_next_run = (datetime.now(ZoneInfo(settings.default_timezone)) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    return {
        "request": request,
        "reminders": reminders,
        "targets": targets,
        "total_reminders": total_reminders or 0,
        "total_targets": total_targets or 0,
        "timezone_name": settings.default_timezone,
        "describe_schedule": describe_schedule,
        "to_local": _to_local,
        "default_next_run": default_next_run,
        "flash": _flash_from_status(status),
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login", response_model=None)
async def login_submit(request: Request, token: str = Form(...)) -> Response:
    settings = get_settings()
    if token == settings.panel_token:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("muad_panel_auth", token, httponly=True, samesite="lax")
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный токен панели"}, status_code=401)


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("muad_panel_auth")
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, status: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", await _build_dashboard_context(request, status=status))


@router.post("/reminders/create")
async def create_reminder(
    target_id: int = Form(...),
    text: str = Form(...),
    next_run_at: str = Form(...),
    schedule_type: str = Form(...),
    is_active: str | None = Form(default=None),
) -> RedirectResponse:
    settings = get_settings()
    clean_text = text.strip()
    if not clean_text:
        return RedirectResponse(url="/?status=empty-text", status_code=303)

    try:
        local_dt = datetime.strptime(next_run_at, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(settings.default_timezone))
    except ValueError:
        return RedirectResponse(url="/?status=invalid-date", status_code=303)

    async with SessionLocal() as session:
        target = await session.get(Target, target_id)
        if target is None:
            return RedirectResponse(url="/?status=invalid-target", status_code=303)

        reminder = Reminder(
            target_id=target.id,
            text=clean_text,
            source_text=clean_text,
            schedule_type=schedule_type,
            schedule_meta=make_schedule_meta(schedule_type, local_dt),
            timezone=settings.default_timezone,
            start_at=local_dt.astimezone(timezone.utc),
            next_run_at=local_dt.astimezone(timezone.utc),
            created_by_user_id=settings.admin_user_id,
            is_active=is_active == "on",
        )
        session.add(reminder)
        await session.commit()

    return RedirectResponse(url="/?status=created", status_code=303)


@router.get("/reminders/{reminder_id}/edit", response_class=HTMLResponse)
async def edit_reminder_page(reminder_id: str, request: Request) -> HTMLResponse:
    settings = get_settings()
    async with SessionLocal() as session:
        reminder = await session.get(Reminder, reminder_id)

    if reminder is None:
        return templates.TemplateResponse(
            "edit_reminder.html",
            {"request": request, "reminder": None, "timezone_name": settings.default_timezone, "local_value": ""},
            status_code=404,
        )

    local_value = reminder.next_run_at.astimezone(ZoneInfo(settings.default_timezone)).strftime("%Y-%m-%dT%H:%M")
    return templates.TemplateResponse(
        "edit_reminder.html",
        {
            "request": request,
            "reminder": reminder,
            "timezone_name": settings.default_timezone,
            "local_value": local_value,
        },
    )


@router.post("/reminders/{reminder_id}/edit")
async def edit_reminder_submit(
    reminder_id: str,
    text: str = Form(...),
    next_run_at: str = Form(...),
    schedule_type: str = Form(...),
    is_active: str | None = Form(default=None),
) -> RedirectResponse:
    settings = get_settings()
    local_dt = datetime.strptime(next_run_at, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(settings.default_timezone))
    async with SessionLocal() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is not None:
            reminder.text = text.strip()
            reminder.schedule_type = schedule_type
            reminder.schedule_meta = make_schedule_meta(schedule_type, local_dt)
            reminder.next_run_at = local_dt.astimezone(timezone.utc)
            reminder.start_at = local_dt.astimezone(timezone.utc)
            reminder.is_active = is_active == "on"
            await session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/reminders/{reminder_id}/toggle")
async def toggle_reminder(reminder_id: str) -> RedirectResponse:
    async with SessionLocal() as session:
        reminder = await session.get(Reminder, reminder_id)
        if reminder is not None:
            reminder.is_active = not reminder.is_active
            await session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/targets/{target_id}/test")
async def test_target_message(target_id: int, request: Request) -> RedirectResponse:
    async with SessionLocal() as session:
        target = await session.get(Target, target_id)
    if target is not None:
        await request.app.state.bot_client.send_test_message(target.chat_id, target.thread_id)
    return RedirectResponse(url="/", status_code=303)

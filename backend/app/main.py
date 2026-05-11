from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import Base, engine
from app.routers.web import router as web_router
from app.services.auth import ensure_panel_auth
from app.services.scheduler import ReminderScheduler
from app.services.telegram import BotClient


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

settings = get_settings()
bot_client = BotClient(settings)
scheduler = ReminderScheduler(bot_client)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    await bot_client.start()
    await scheduler.start()
    await bot_client.notify_admin_startup()
    try:
        yield
    finally:
        await scheduler.stop()
        await bot_client.stop()
        await engine.dispose()


app = FastAPI(title="Muad Reminder Bot", lifespan=lifespan)
app.state.bot_client = bot_client
app.state.scheduler = scheduler
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)


@app.middleware("http")
async def panel_auth_middleware(request: Request, call_next):
    auth_response = ensure_panel_auth(request)
    if auth_response is not None:
        return auth_response
    return await call_next(request)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/panel")
async def panel_redirect() -> RedirectResponse:
    return RedirectResponse(url="/")

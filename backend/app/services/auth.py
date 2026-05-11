from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import get_settings


def ensure_panel_auth(request: Request) -> RedirectResponse | None:
    settings = get_settings()
    open_paths = {"/login", "/logout", "/healthz", "/panel"}
    if request.url.path in open_paths or request.url.path.startswith("/static/"):
        return None
    token = request.cookies.get("muad_panel_auth")
    if token == settings.panel_token:
        return None
    return RedirectResponse(url="/login", status_code=303)


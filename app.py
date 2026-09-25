"""HTMX HTML server: pages and fragments, plus a GeoJSON pipeline overlay."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode

import requests
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from wellnav.accounts import CACHE, LOCATION_TTL, OPERATOR_TTL, SAVED, SEARCH_TTL, USERS
from wellnav.feedback import FEEDBACK, KINDS
from wellnav.auth import (
    SESSION_MAX_AGE,
    clear_otp_session,
    current_user_id,
    is_public_path,
    login_user,
    logout_user,
    safe_next,
    session_matches,
    session_secret,
    wants_json,
)
from wellnav.phone_auth import PHONE_AUTH
from wellnav.filters import (
    COLUMN_FILTER_KEYS,
    LIVE_FILTER_MIN,
    empty_filters,
    filter_query,
    has_filter_chips,
    has_filters,
    parse_column_filters,
    parse_filters,
    search_kwargs,
    subtitle as filter_subtitle,
    typed_query_filters,
)
from wellnav.operators import normalize_operator_name
from wellnav.parsers import format_api, normalize_api
from wellnav.repository import REPO
from wellnav.billing import (
    billing_enforced,
    clamp_seats,
    create_checkout_session,
    create_portal_session,
    handle_webhook,
    is_billing_path,
    is_store_client,
    sync_checkout_session,
    update_subscription_seats,
    workspace_for,
)
from wellnav.offline_routes import parse_pack_request, plan_offline_routes
from wellnav.offline_tiles import usgs_url, validate_tile
from wellnav.recordings import (
    append_chunk,
    can_record,
    clarity_project_id,
    contentsquare_tag_id,
    finish_recording,
    hotjar_site_id,
    list_recordings,
    recording_file,
    start_recording,
)
from wellnav.states import APP_STATES, STATE_LABELS, state_from_api

ROOT = Path(__file__).resolve().parent

templates = Jinja2Templates(directory="templates")


def well_href(well: dict) -> str:
    query = urlencode(
        {
            key: value
            for key, value in {
                "name": well.get("well_name") or "",
                "lease": well.get("lease_name") or "",
                "county": well.get("county") or "",
                "operator": well.get("operator") or "",
                "well_no": well.get("well_no") or "",
                "state": well.get("state") or "",
            }.items()
            if value
        }
    )
    path = f"/well/{well.get('api_full') or well['api']}"
    return f"{path}?{query}" if query else path


def request_state_token(source) -> str:
    raw = ""
    if hasattr(source, "query_params"):
        raw = source.query_params.get("state") or ""
    if hasattr(source, "get") and not raw:
        raw = source.get("state") or ""
    text = str(raw or "tx").strip().lower()
    if text == "all":
        return "all"
    return text if text in APP_STATES else "tx"


def app_counts() -> dict:
    """Header totals: every state in the app, not the sidebar selection."""
    return REPO.counts("all")


def search_href(**parts: object) -> str:
    query = urlencode(
        {key: str(value) for key, value in parts.items() if value not in (None, "")}
    )
    return f"/search?{query}" if query else "/search"


def filter_href(filters: dict | None = None, **extra: object) -> str:
    query = filter_query(filters or empty_filters(), **extra)
    return f"/search?{query}" if query else "/search"


_SORT_KEYS = {"name", "api", "status", "lease", "operator", "county"}


def normalize_sort(sort: str, direction: str) -> tuple[str, str]:
    key = (sort or "name").strip().lower()
    if key not in _SORT_KEYS:
        key = "name"
    return key, ("desc" if (direction or "").strip().lower() == "desc" else "asc")


def sort_href(filters: dict | None, current_sort: str, current_dir: str, key: str, **extra: object) -> str:
    sort, direction = normalize_sort(current_sort, current_dir)
    nxt = "desc" if sort == key and direction == "asc" else "asc"
    extra.setdefault("offset", 0)
    return filter_href(filters, sort=key, dir=nxt, **extra)


def cache_key(*parts: object) -> str:
    return "|".join(str(part or "").strip().lower() for part in parts)


templates.env.globals["well_href"] = well_href
templates.env.globals["search_href"] = search_href
templates.env.globals["filter_href"] = filter_href
templates.env.globals["filter_query"] = filter_query
templates.env.globals["sort_href"] = sort_href


def workspace_snapshot(user: dict | None) -> dict:
    if not user:
        return workspace_for(None, None, [])
    org = USERS.org(user.get("org_id")) if user.get("org_id") else None
    members = USERS.org_members(user["org_id"]) if user.get("org_id") else []
    return workspace_for(user, org, members)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def current_user(request: Request, *, revoke_on_mismatch: bool = True) -> dict | None:
    uid = current_user_id(request)
    if not uid:
        return None
    user = USERS.get(uid)
    if session_matches(request, user):
        return user
    if revoke_on_mismatch:
        logout_user(request)
    return None


def view_ctx(request: Request, **extra) -> dict:
    user = current_user(request)
    saved_apis = SAVED.api_set(user["id"]) if user else set()
    recent = CACHE.recent_searches(user["id"]) if user else []
    context = {
        "user": user,
        "saved_apis": saved_apis,
        "saved_count": len(saved_apis),
        "recent": recent,
        "cache_stats": CACHE.stats(user["id"]) if user else None,
        **extra,
    }
    if not context.get("filters"):
        context["filters"] = empty_filters()
    if not context.get("sort"):
        context["sort"] = getattr(request.state, "sort", "name")
    if not context.get("dir"):
        context["dir"] = getattr(request.state, "dir", "asc")
    if not context.get("state"):
        context["state"] = getattr(request.state, "app_state", None) or request_state_token(request)
    if user and "workspace" not in extra:
        context["workspace"] = workspace_snapshot(user)
    context.setdefault("app_states", APP_STATES)
    context.setdefault("state_labels", STATE_LABELS)
    context.setdefault("store_client", is_store_client(request.headers.get("user-agent") or ""))
    context.setdefault("hotjar_id", hotjar_site_id())
    context.setdefault("clarity_id", clarity_project_id())
    context.setdefault("contentsquare_id", contentsquare_tag_id())
    context.setdefault("can_record_ux", can_record(user) and not context.get("store_client"))
    return context


class HtmlCacheMiddleware(BaseHTTPMiddleware):
    """Store WebViews keep a fresh document so label and layout changes show up."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def render(name: str, request: Request | None = None, **context) -> str:
    if request is not None:
        context = {**view_ctx(request), **context}
    return templates.get_template(name).render(context)


def column_filter_state(columns: dict[str, str] | None) -> str:
    """Hidden inputs that keep per-column text filters across sort, paging, and chips."""
    inputs = []
    for key in COLUMN_FILTER_KEYS:
        value = (columns or {}).get(key) or ""
        if not value:
            continue
        inputs.append(
            f'<input type="hidden" name="cf_{html.escape(key, quote=True)}" '
            f'value="{html.escape(value, quote=True)}">'
        )
    return (
        '<div id="column-filters" hidden hx-swap-oob="outerHTML">'
        + "".join(inputs)
        + "</div>"
    )


def page(
    request: Request,
    *,
    results_html: str = "",
    map_html: str = "",
    filters: dict | None = None,
    column_filters: dict | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        view_ctx(
            request,
            counts=app_counts(),
            results_html=results_html,
            map_html=map_html,
            filters=filters or empty_filters(),
            column_filters=column_filters or {},
        ),
    )


def with_oob(
    request: Request,
    html: str,
    *,
    nav: bool = False,
    clear_suggest: bool = False,
    filters: dict | None = None,
) -> str:
    parts = [html]
    if clear_suggest:
        parts.append('<div id="operator-suggest" hx-swap-oob="innerHTML"></div>')
    if filters is not None:
        parts.append(
            '<div id="active-filters" hx-swap-oob="innerHTML">'
            + render(
                "partials/filters.html",
                request,
                filters=filters,
                sort=getattr(request.state, "sort", "name"),
                dir=getattr(request.state, "dir", "asc"),
            )
            + "</div>"
        )
    if nav:
        parts.append(render("partials/account_nav.html", request, oob=True))
    return "".join(parts)


def fragment_or_page(
    request: Request,
    html: str,
    *,
    map_html: str = "",
    nav: bool = False,
    clear_suggest: bool = False,
    filters: dict | None = None,
    column_filters: dict | None = None,
    retarget: str | None = None,
) -> HTMLResponse:
    if is_htmx(request):
        body = html
        if column_filters is not None:
            body += column_filter_state(column_filters)
        response = HTMLResponse(
            with_oob(
                request,
                body,
                nav=nav,
                clear_suggest=clear_suggest,
                filters=filters,
            )
        )
        if retarget:
            response.headers["HX-Retarget"] = retarget
        return response
    return page(
        request,
        results_html=html,
        map_html=map_html,
        filters=filters,
        column_filters=column_filters,
    )


def redirect_to(request: Request, url: str) -> HTMLResponse | RedirectResponse:
    if is_htmx(request):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = url
        return response
    return RedirectResponse(url, status_code=303)


def auth_page(request: Request, html: str) -> HTMLResponse:
    if is_htmx(request):
        return HTMLResponse(html)
    return templates.TemplateResponse(
        request,
        "gate.html",
        {**view_ctx(request), "auth_html": html, "counts": app_counts()},
    )


def login_required_html(request: Request, message: str = "Sign in to continue.") -> HTMLResponse:
    nxt = safe_next(request.url.path)
    target = "/login" if nxt in {"/", "/login"} else f"/login?next={quote(nxt)}"
    return redirect_to(request, target)


class RequireSignInMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path):
            return await call_next(request)
        user = current_user(request)
        if user:
            USERS.touch_activity(user["id"])
            if (
                billing_enforced()
                and not is_billing_path(request.url.path)
                and not workspace_snapshot(user)["allowed"]
            ):
                target = "/billing"
                if wants_json(request):
                    return JSONResponse({"error": "billing_required"}, status_code=402)
                if is_htmx(request):
                    response = HTMLResponse("")
                    response.headers["HX-Redirect"] = target
                    return response
                return RedirectResponse(target, status_code=303)
            return await call_next(request)
        if wants_json(request):
            return JSONResponse({"error": "sign_in_required"}, status_code=401)
        nxt = request.url.path
        if request.url.query:
            nxt = f"{nxt}?{request.url.query}"
        nxt = safe_next(nxt)
        target = "/login" if nxt in {"/", "/login"} else f"/login?next={quote(nxt, safe='/?&=')}"
        if is_htmx(request):
            response = HTMLResponse("")
            response.headers["HX-Redirect"] = target
            return response
        return RedirectResponse(target, status_code=303)


def _verify_urls(purpose: str) -> tuple[str, str]:
    if purpose == "register":
        return "/register/verify", "/register/resend"
    if purpose == "recovery":
        return "/login/forgot/verify", "/login/forgot/resend"
    return "/login/verify", "/login/resend"


def _verify_html(request: Request, challenge: dict, *, error: str | None = None) -> str:
    view = PHONE_AUTH.challenge_view(challenge)
    verify_action, resend_action = _verify_urls(view["purpose"])
    return render(
        "partials/verify.html",
        request,
        error=error,
        next_path=request.session.get("auth_next") or "/",
        verify_action=verify_action,
        resend_action=resend_action,
        **view,
    )


def _remember_challenge(request: Request, challenge: dict, next_path: str) -> None:
    clear_otp_session(request)
    request.session["otp_id"] = challenge["id"]
    request.session["otp_purpose"] = challenge["purpose"]
    request.session["auth_next"] = safe_next(next_path)
    if challenge.get("pending_id"):
        request.session["pending_signup"] = challenge["pending_id"]
    pending_uid = challenge.get("pending_uid") or challenge.get("user_id")
    if pending_uid:
        request.session["pending_uid"] = int(pending_uid)


def _params(request: Request) -> dict[str, str]:
    source = request.query_params if request.method == "GET" else {}
    return {key: (value or "").strip() for key, value in source.items()}


async def _form_params(request: Request) -> dict[str, str]:
    if request.method != "POST":
        return _params(request)
    form = await request.form()
    return {key: str(value).strip() if value is not None else "" for key, value in form.items()}


async def _request_source(request: Request):
    if request.method == "POST":
        return await request.form()
    return request.query_params


async def index(request: Request) -> HTMLResponse:
    return page(request)


async def account(request: Request) -> HTMLResponse:
    user = current_user(request)
    workspace = workspace_snapshot(user)
    store = is_store_client(request.headers.get("user-agent") or "")
    html = render(
        "partials/account.html",
        request,
        org=workspace.get("org"),
        workspace=workspace,
        recordings=list_recordings() if can_record(user) and not store else [],
    )
    return fragment_or_page(request, html, clear_suggest=True)


def _simba_user(request: Request):
    user = current_user(request, revoke_on_mismatch=False)
    if not user:
        if wants_json(request):
            return None, JSONResponse({"error": "sign_in_required"}, status_code=401)
        return None, login_required_html(request)
    if is_store_client(request.headers.get("user-agent") or ""):
        return None, JSONResponse({"error": "not available in the store app"}, status_code=403)
    if not can_record(user):
        return None, JSONResponse({"error": "Screen recording is only available to Simba Services."}, status_code=403)
    return user, None


async def ux_recording_start(request: Request) -> Response:
    user, error = _simba_user(request)
    if error:
        return error
    try:
        meta = start_recording(user, request.headers.get("user-agent") or "")
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return JSONResponse({"id": meta["id"]})


async def ux_recording_chunk(request: Request) -> Response:
    user, error = _simba_user(request)
    if error:
        return error
    payload = await request.body()
    try:
        meta = append_chunk(request.path_params["recording_id"], user, payload)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"id": meta["id"], "bytes": meta.get("bytes") or 0})


async def ux_recording_finish(request: Request) -> Response:
    user, error = _simba_user(request)
    if error:
        return error
    try:
        meta = finish_recording(request.path_params["recording_id"], user)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"id": meta["id"], "bytes": meta.get("bytes") or 0})


async def ux_recording_file(request: Request) -> Response:
    user, error = _simba_user(request)
    if error:
        return error
    found = recording_file(request.path_params["recording_id"])
    if not found:
        return PlainTextResponse("recording not found", status_code=404)
    path, meta = found
    suffix = path.suffix or ".jsonl"
    name = f"wellnav-ux-{meta.get('started_at') or meta['id']}{suffix}".replace(":", "")
    media = "application/x-ndjson" if suffix == ".jsonl" else "video/webm"
    return FileResponse(path, filename=name, media_type=media)


async def ux_recording_replay(request: Request) -> Response:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if is_store_client(request.headers.get("user-agent") or "") or not can_record(user):
        return PlainTextResponse("not found", status_code=404)
    found = recording_file(request.path_params["recording_id"])
    if not found:
        return PlainTextResponse("recording not found", status_code=404)
    _path, meta = found
    return templates.TemplateResponse(
        request,
        "ux_replay.html",
        {**view_ctx(request), "recording": meta, "local_player": False},
    )


async def account_delete(request: Request) -> HTMLResponse | RedirectResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if request.method == "GET":
        html = render("partials/delete_account.html", request)
        return fragment_or_page(request, html, clear_suggest=True)
    data = await request.form()
    confirm = (data.get("confirm") or "").strip()
    if confirm.upper() != "DELETE":
        html = render(
            "partials/delete_account.html",
            request,
            error="Type DELETE to confirm you want this account removed.",
        )
        return fragment_or_page(request, html, clear_suggest=True)
    ok, error = USERS.delete_account(user, data.get("password") or "")
    if not ok:
        html = render("partials/delete_account.html", request, error=error)
        return fragment_or_page(request, html, clear_suggest=True)
    logout_user(request)
    return redirect_to(request, "/login?forget=1")


async def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "privacy.html", view_ctx(request))


async def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms.html", view_ctx(request))


def _feedback_html(
    request: Request,
    user: dict,
    *,
    error: str | None = None,
    draft: dict | None = None,
) -> str:
    org = USERS.org(user.get("org_id")) if user.get("org_id") else None
    messages, truncated = FEEDBACK.list_for(user) if org else ([], False)
    return render(
        "partials/feedback.html",
        request,
        org=org,
        messages=messages,
        truncated=truncated,
        kinds=KINDS,
        error=error,
        draft=draft or {},
    )


async def feedback_panel(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    html = _feedback_html(request, user)
    return fragment_or_page(request, html, clear_suggest=True)


async def feedback_create(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    data = await _form_params(request)
    _, error = FEEDBACK.post(
        user,
        kind=data.get("kind", ""),
        place=data.get("place", ""),
        body=data.get("body", ""),
    )
    draft = data if error else {}
    html = _feedback_html(request, user, error=error, draft=draft)
    return fragment_or_page(request, html, clear_suggest=True)


async def feedback_delete(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    _, error = FEEDBACK.delete(user, int(request.path_params["message_id"]))
    html = _feedback_html(request, user, error=error)
    return fragment_or_page(request, html, clear_suggest=True)


async def org_panel(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if not user.get("org_id"):
        html = render(
            "partials/account.html",
            request,
            org=None,
            error="This account is not in an organization yet.",
        )
        return fragment_or_page(request, html, clear_suggest=True)
    workspace = workspace_snapshot(user)
    html = render(
        "partials/org.html",
        request,
        org=workspace.get("org"),
        members=workspace.get("members") or [],
        workspace=workspace,
    )
    return fragment_or_page(request, html, clear_suggest=True)


async def org_remove(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    _, error = USERS.remove_member(user, int(request.path_params["member_id"]))
    if error:
        html = render(
            "partials/org.html",
            request,
            org=USERS.org(user.get("org_id")),
            members=USERS.org_members(user["org_id"]) if user.get("org_id") else [],
            error=error,
        )
        return fragment_or_page(request, html, clear_suggest=True)
    return await org_panel(request)


async def org_role(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    data = await _form_params(request)
    _, error = USERS.set_member_role(user, int(request.path_params["member_id"]), data.get("role", ""))
    if error:
        html = render(
            "partials/org.html",
            request,
            org=USERS.org(user.get("org_id")),
            members=USERS.org_members(user["org_id"]) if user.get("org_id") else [],
            error=error,
        )
        return fragment_or_page(request, html, clear_suggest=True)
    return await org_panel(request)


async def billing_panel(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    workspace = workspace_snapshot(user)
    html = render("partials/billing.html", request, workspace=workspace)
    return fragment_or_page(request, html, clear_suggest=True)


STORE_BILLING_MESSAGE = (
    "Seat billing is not available in the App Store or Play Store app. "
    "Open wellnav.simba.services in Safari or Chrome to buy or change seats."
)


async def billing_checkout(request: Request) -> HTMLResponse | RedirectResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if is_store_client(request.headers.get("user-agent") or ""):
        return await billing_panel_error(request, STORE_BILLING_MESSAGE)
    if not user.get("is_admin"):
        return await billing_panel_error(request, "Only an organization admin can change seats.")
    workspace = workspace_snapshot(user)
    org = workspace.get("org")
    if not org:
        return await billing_panel_error(request, "This account is not in an organization yet.")
    if workspace.get("complimentary"):
        return redirect_to(request, "/org")
    data = await _form_params(request)
    try:
        url = create_checkout_session(org, user, clamp_seats(data.get("seats") or workspace.get("suggested_seats")))
    except Exception as exc:
        return await billing_panel_error(request, str(exc))
    return redirect_to(request, url)


async def billing_seats(request: Request) -> HTMLResponse | RedirectResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if is_store_client(request.headers.get("user-agent") or ""):
        return await billing_panel_error(request, STORE_BILLING_MESSAGE)
    if not user.get("is_admin"):
        return await billing_panel_error(request, "Only an organization admin can change seats.")
    workspace = workspace_snapshot(user)
    org = workspace.get("org")
    if not org:
        return await billing_panel_error(request, "This account is not in an organization yet.")
    if workspace.get("complimentary"):
        return redirect_to(request, "/org")
    data = await _form_params(request)
    try:
        if org.get("stripe_subscription_item_id"):
            update_subscription_seats(org, clamp_seats(data.get("seats") or workspace.get("suggested_seats")))
            return redirect_to(request, "/org")
        url = create_checkout_session(org, user, clamp_seats(data.get("seats") or workspace.get("suggested_seats")))
    except Exception as exc:
        return await billing_panel_error(request, str(exc))
    return redirect_to(request, url)


async def billing_portal(request: Request) -> HTMLResponse | RedirectResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request)
    if is_store_client(request.headers.get("user-agent") or ""):
        return await billing_panel_error(request, STORE_BILLING_MESSAGE)
    if not user.get("is_admin"):
        return await billing_panel_error(request, "Only an organization admin can open the billing portal.")
    workspace = workspace_snapshot(user)
    org = workspace.get("org")
    if not org:
        return await billing_panel_error(request, "This account is not in an organization yet.")
    try:
        url = create_portal_session(org)
    except Exception as exc:
        return await billing_panel_error(request, str(exc))
    return redirect_to(request, url)


async def billing_success(request: Request) -> HTMLResponse | RedirectResponse:
    session_id = (request.query_params.get("session_id") or "").strip()
    if session_id:
        try:
            sync_checkout_session(session_id)
        except Exception:
            pass
    user = current_user(request)
    if not user:
        html = render("billing_return.html", request, paid=bool(session_id))
        return HTMLResponse(html)
    workspace = workspace_snapshot(user)
    html = render(
        "partials/billing.html",
        request,
        workspace=workspace,
        success=bool(workspace.get("allowed") and workspace.get("paid")),
    )
    return fragment_or_page(request, html, clear_suggest=True)


async def billing_panel_error(request: Request, error: str) -> HTMLResponse:
    user = current_user(request)
    workspace = workspace_snapshot(user)
    html = render("partials/billing.html", request, workspace=workspace, error=error)
    return fragment_or_page(request, html, clear_suggest=True)


async def billing_webhook(request: Request) -> Response:
    payload = await request.body()
    signature = request.headers.get("stripe-signature") or ""
    try:
        kind = handle_webhook(payload, signature)
    except Exception:
        return PlainTextResponse("invalid webhook", status_code=400)
    return PlainTextResponse(kind or "ok")


async def login(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, safe_next(request.query_params.get("next")))
    if request.method == "GET":
        html = render(
            "partials/login.html",
            request,
            error=None,
            next_path=safe_next(request.query_params.get("next")),
        )
        return auth_page(request, html)
    data = await _form_params(request)
    next_path = safe_next(data.get("next"))
    email = data.get("email") or data.get("username", "")
    user, error = PHONE_AUTH.start_login(email, data.get("password", ""))
    if error or not user:
        html = render(
            "partials/login.html",
            request,
            error=error or "Email or password is incorrect.",
            next_path=next_path,
            email=email,
        )
        return auth_page(request, html)
    login_user(request, user["id"], user["username"])
    return redirect_to(request, next_path)


async def register(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, "/")
    if request.method == "GET":
        html = render("partials/register.html", request, error=None)
        return auth_page(request, html)
    data = await _form_params(request)
    email = data.get("email") or data.get("username", "")
    result, error = PHONE_AUTH.start_signup(email, data.get("password", ""))
    if error or not result:
        html = render(
            "partials/register.html",
            request,
            error=error or "Could not start account creation.",
            email=email,
        )
        return auth_page(request, html)
    _remember_challenge(request, result, "/")
    return auth_page(request, _verify_html(request, result))


async def verify_code(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, "/")
    challenge_id = request.session.get("otp_id") or ""
    challenge = PHONE_AUTH.get_challenge(challenge_id)
    if request.method == "GET":
        if not challenge:
            return redirect_to(request, "/login")
        return auth_page(request, _verify_html(request, challenge))
    data = await _form_params(request)
    next_path = safe_next(data.get("next") or request.session.get("auth_next"))
    if not challenge:
        html = render(
            "partials/login.html",
            request,
            error="Request a new code to continue.",
            next_path=next_path,
        )
        return auth_page(request, html)
    user, error = PHONE_AUTH.verify(challenge_id, data.get("code", ""))
    if error or not user:
        fresh = PHONE_AUTH.get_challenge(challenge_id) or challenge
        return auth_page(request, _verify_html(request, fresh, error=error))
    if challenge.get("purpose") == "recovery":
        clear_otp_session(request)
        request.session["pending_uid"] = user["id"]
        request.session["otp_purpose"] = "recovery"
        request.session["auth_next"] = next_path
        html = render("partials/reset_password.html", request, error=None, next_path=next_path)
        return auth_page(request, html)
    login_user(request, user["id"], user["username"])
    return redirect_to(request, next_path)


async def resend_code(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, "/")
    data = await _form_params(request)
    next_path = safe_next(data.get("next") or request.session.get("auth_next"))
    challenge_id = request.session.get("otp_id") or ""
    result, error = PHONE_AUTH.resend(challenge_id)
    current = PHONE_AUTH.get_challenge(challenge_id)
    if error or not result:
        if current:
            return auth_page(request, _verify_html(request, current, error=error))
        html = render(
            "partials/login.html",
            request,
            error=error or "Request a new code to continue.",
            next_path=next_path,
        )
        return auth_page(request, html)
    _remember_challenge(request, result, next_path)
    return auth_page(request, _verify_html(request, result))


async def forgot(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, "/")
    next_path = safe_next(request.query_params.get("next") or request.session.get("auth_next"))
    if request.method == "GET":
        html = render("partials/forgot.html", request, error=None, next_path=next_path)
        return auth_page(request, html)
    data = await _form_params(request)
    next_path = safe_next(data.get("next") or next_path)
    email = data.get("email") or data.get("username", "")
    result, error = PHONE_AUTH.start_recovery(email)
    if error or not result:
        html = render(
            "partials/forgot.html",
            request,
            error=error or "No account matches that email address.",
            next_path=next_path,
            email=email,
        )
        return auth_page(request, html)
    _remember_challenge(request, result, next_path)
    return auth_page(request, _verify_html(request, result))


async def reset_password(request: Request) -> HTMLResponse | RedirectResponse:
    if current_user(request):
        return redirect_to(request, "/")
    pending_uid = request.session.get("pending_uid")
    next_path = safe_next(request.session.get("auth_next") or request.query_params.get("next"))
    if not pending_uid or request.session.get("otp_purpose") != "recovery":
        return redirect_to(request, "/login/forgot")
    if request.method == "GET":
        html = render("partials/reset_password.html", request, error=None, next_path=next_path)
        return auth_page(request, html)
    data = await _form_params(request)
    next_path = safe_next(data.get("next") or next_path)
    user, error = USERS.set_password(int(pending_uid), data.get("password", ""))
    if error or not user:
        html = render(
            "partials/reset_password.html",
            request,
            error=error or "Could not update that password.",
            next_path=next_path,
        )
        return auth_page(request, html)
    login_user(request, user["id"], user["username"])
    return redirect_to(request, next_path)


async def logout(request: Request) -> HTMLResponse | RedirectResponse:
    logout_user(request)
    return redirect_to(request, "/login")


async def operators(request: Request) -> HTMLResponse:
    if request.query_params.get("mode", "operator") != "operator":
        return fragment_or_page(request, "")
    q = request.query_params.get("q", "").strip()
    user = current_user(request)
    state = request_state_token(request)
    rows = REPO.search_operators(q, state=state)
    if user and rows:
        CACHE.set(user["id"], "operators", cache_key(q, state), rows, OPERATOR_TTL)
    html = render("partials/operators.html", request, operators=rows, query=q, source="local")
    return fragment_or_page(request, html)


async def search(request: Request) -> HTMLResponse:
    source = await _request_source(request)
    data = {key: str(value or "").strip() for key, value in source.items()}
    user = current_user(request)
    mode = data.get("mode") or "name"
    page_size = int(data.get("page_size") or 50)
    offset = int(data.get("offset") or 0)
    sort, direction = normalize_sort(data.get("sort", ""), data.get("dir", ""))
    request.state.sort = sort
    request.state.dir = direction
    q = data.get("q", "")
    if (data.get("scope") or "wells") == "pipelines":
        return await pipeline_search(request)
    if (data.get("scope") or "wells") == "disposal":
        return await disposal_search(request)
    filters = parse_filters(source)
    committing = data.get("commit") == "1"
    column_filters = (
        {}
        if committing or data.get("clear_filters") == "1"
        else parse_column_filters(source)
    )
    column_partial = is_htmx(request) and request.headers.get("x-column-filter") == "1"
    added_operator = bool(data.get("add_op_number") or data.get("add_op_name"))
    clear_query = committing or added_operator
    error = None
    known_numbers = {op["number"] for op in filters["operators"]}
    known_names = {normalize_operator_name(op["name"]) for op in filters["operators"]}

    if committing and mode == "operator" and q:
        matches = [
            row
            for row in REPO.search_operators(
                q, state=request_state_token(data) or request_state_token(request)
            )
            if row["number"] not in known_numbers
            and normalize_operator_name(row["name"]) not in known_names
        ]
        if not matches and not filters["operators"]:
            error = f"No operators match “{q}” in the local database."
        elif len(matches) == 1:
            filters["operators"].append(
                {"number": matches[0]["number"], "name": matches[0]["name"], "active": True}
            )
        elif len(matches) > 1:
            if user:
                CACHE.set(
                    user["id"],
                    "operators",
                    cache_key(q, request_state_token(data) or request_state_token(request)),
                    matches[:40],
                    OPERATOR_TTL,
                )
            html = render("partials/operators.html", request, operators=matches[:40], query=q)
            return fragment_or_page(
                request,
                html,
                clear_suggest=False,
                filters=filters,
                column_filters=column_filters,
                retarget="#operator-suggest",
            )
    live = is_htmx(request) and request.headers.get("x-live-filter") == "1"
    filters = typed_query_filters(filters, mode=mode, q=q, committing=committing, live=live)
    if live:
        offset = 0
    if (
        live
        and mode in {"name", "api"}
        and len(q) < LIVE_FILTER_MIN
        and not has_filters(filters)
        and not data.get("lease_no")
        and not column_filters
    ):
        return fragment_or_page(
            request,
            (
                '<div class="empty">'
                "Search wells, pipelines, and waste sites in Texas, New Mexico, "
                "Oklahoma, and Louisiana. Pick a state or All, then search."
                "</div>"
            ),
            clear_suggest=True,
            filters=filters,
            column_filters=column_filters,
        )

    stacked = search_kwargs(filters)
    context = {
        "mode": mode,
        "query": q,
        "subtitle": "",
        "operator_number": "",
        "operator_name": "",
        "lease_no": data.get("lease_no", ""),
        "district": data.get("district", ""),
        "lease_type": data.get("lease_type", ""),
        "page_size": page_size,
        "offset": offset,
        "from_cache": False,
        "filters": filters,
        "clear_query": clear_query,
        "sort": sort,
        "dir": direction,
        "column_filters": column_filters,
        "column_partial": column_partial,
    }
    state = request_state_token(data) if data.get("state") else request_state_token(request)
    request.state.app_state = state
    context["state"] = state
    counts = REPO.counts(state)
    try:
        if user and not live and (has_filter_chips(filters) or q):
            CACHE.remember_recent(
                user["id"],
                {
                    "mode": mode,
                    "q": q,
                    "href": filter_href(filters, mode=mode, state=state),
                    "label": filter_subtitle(filters, mode=mode, q=q) or q,
                },
            )
        result = REPO.search(
            state=state,
            mode=mode,
            q="" if (stacked["operator_numbers"] or stacked["name"] or stacked["api"]) else q,
            operator_number="",
            operator_numbers=stacked["operator_numbers"],
            operator_names=stacked.get("operator_names") or [],
            name=stacked["name"],
            api=stacked["api"],
            lease_no=context["lease_no"],
            district=context["district"],
            page_size=page_size,
            offset=offset,
            sort=sort,
            direction=direction,
            column_filters=column_filters,
        )
        if (
            not live
            and not column_filters
            and not column_partial
            and result["total"] == 0
            and mode == "name"
            and q
            and not context["lease_no"]
            and not stacked["operator_numbers"]
        ):
            leases = REPO.search_leases(q, state=state)
            if len(leases) == 1:
                context["lease_no"] = leases[0]["lease_no"]
                context["district"] = leases[0]["district"]
                result = REPO.search(
                    state=state,
                    mode=mode,
                    q=q,
                    lease_no=context["lease_no"],
                    district=context["district"],
                    page_size=page_size,
                    offset=offset,
                    sort=sort,
                    direction=direction,
                    column_filters=column_filters,
                )
                context["subtitle"] = f"Lease {leases[0]['name']}"
            elif len(leases) > 1:
                html = render("partials/leases.html", request, leases=leases, query=q)
                return fragment_or_page(
                    request,
                    html,
                    clear_suggest=True,
                    filters=filters,
                    column_filters=column_filters,
                )
        if result["total"] == 0 and counts["total"] == 0:
            context["subtitle"] = "Local database is empty — run python -m wellnav.ingest load-texas"
        elif result["total"] and not context["subtitle"]:
            context["subtitle"] = filter_subtitle(filters, mode=mode, q=q)
        if user and result["total"]:
            CACHE.set(
                user["id"],
                "search",
                cache_key(
                    state,
                    mode,
                    stacked["name"],
                    stacked["api"],
                    ",".join(stacked["operator_numbers"]),
                    ",".join(stacked.get("operator_names") or []),
                    context["lease_no"],
                    context["district"],
                    offset,
                    page_size,
                    ",".join(f"{key}={column_filters[key]}" for key in COLUMN_FILTER_KEYS if column_filters.get(key)),
                ),
                {
                    "wells": result["wells"],
                    "total": result["total"],
                    "start": result["start"],
                    "end": result["end"],
                    "subtitle": context["subtitle"],
                },
                SEARCH_TTL,
            )
        wells, total, start, end = (
            result["wells"],
            result["total"],
            result["start"],
            result["end"],
        )
    except Exception as exc:
        wells, total, start, end = [], 0, 0, 0
        error = str(exc)

    html = render(
        "partials/wells.html",
        request,
        wells=wells,
        total=total,
        start=start,
        end=end,
        error=error,
        **context,
    )
    if column_partial:
        return HTMLResponse(html + column_filter_state(column_filters))
    return fragment_or_page(
        request,
        html,
        clear_suggest=True,
        filters=filters,
        column_filters=column_filters,
    )


def _location_for(api8: str, stored: dict | None) -> dict:
    if stored and stored.get("wellhead_lat") is not None:
        return {
            "api": api8,
            "found": True,
            "kind": stored.get("location_kind") or stored.get("record_kind"),
            "wellhead": {
                "lat": stored["wellhead_lat"],
                "lon": stored["wellhead_lon"],
                "source_label": stored.get("wellhead_crs") or "local sqlite WGS84",
            },
            "toe": (
                {"lat": stored["toe_lat"], "lon": stored["toe_lon"], "source_label": "local sqlite WGS84"}
                if stored.get("toe_lat") is not None
                else None
            ),
            "note": (
                "Coordinates loaded from the local well table."
                if stored.get("record_kind") == "as_drilled"
                else "Permit still in its lifetime table; wellhead is the permitted surface location."
            ),
            "symbol": stored.get("status"),
            "location_source": stored.get("location_source"),
            "from_cache": False,
        }
    return {
        "api": api8,
        "found": False,
        "kind": "missing",
        "wellhead": None,
        "toe": None,
        "note": "No wellhead coordinates in the local well tables for this API.",
        "from_cache": False,
    }


async def well_detail(request: Request) -> HTMLResponse:
    api = request.path_params["api"]
    _, _, eight = normalize_api(api)
    user = current_user(request)
    state = request.query_params.get("state") or state_from_api(api) or "tx"
    stored = REPO.get_well(api, state=state)
    location = _location_for(eight, stored)
    if user and location.get("found"):
        CACHE.set(user["id"], "location", eight, location, LOCATION_TTL)
    well_state = (stored or {}).get("state") or state
    well = {
        "api": eight,
        "api_full": (stored or {}).get("api_full") or api,
        "state": well_state,
        "api_display": format_api((stored or {}).get("api_full") or api, well_state),
        "well_name": request.query_params.get("name") or (stored or {}).get("well_name") or format_api(eight, well_state),
        "lease_name": request.query_params.get("lease") or (stored or {}).get("lease_name") or "",
        "county": request.query_params.get("county") or (stored or {}).get("county") or "",
        "operator": request.query_params.get("operator") or (stored or {}).get("operator") or "",
        "well_no": request.query_params.get("well_no") or (stored or {}).get("well_no") or "",
        "symbol": (stored or {}).get("symbol") or "",
        "status_label": (stored or {}).get("status_label") or "",
    }
    map_html = render("partials/well_detail.html", request, well=well, location=location)
    if is_htmx(request):
        return HTMLResponse(map_html)
    return page(request, map_html=map_html)


async def saved_list(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to see saved wells.")
    wells = SAVED.list(user["id"])
    extras = {}
    for well in wells:
        found = REPO.get_well(well["api"], state=well.get("state") or "tx")
        if found:
            extras[f"{well.get('state') or 'tx'}:{well['api']}"] = found
    for well in wells:
        extra = extras.get(f"{well.get('state') or 'tx'}:{well['api']}")
        if extra:
            well["wellhead_lat"] = extra.get("wellhead_lat")
            well["wellhead_lon"] = extra.get("wellhead_lon")
            well["toe_lat"] = extra.get("toe_lat")
            well["toe_lon"] = extra.get("toe_lon")
            well["symbol"] = extra.get("symbol") or ""
            well["status_label"] = extra.get("status_label") or ""
    html = render("partials/saved.html", request, wells=wells)
    return fragment_or_page(request, html, clear_suggest=True)


async def saved_bulk(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to save wells.")
    data = await _form_params(request)
    wells: list[dict] = []
    raw = data.get("wells") or ""
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            wells = [item for item in parsed if isinstance(item, dict)]
    result = SAVED.save_many(user["id"], wells)
    html = render("partials/save_status.html", request, **result)
    if is_htmx(request):
        return HTMLResponse(with_oob(request, html, nav=True))
    if result["added"] or result["kept"]:
        return RedirectResponse("/saved", status_code=303)
    return RedirectResponse("/", status_code=303)


async def saved_toggle(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to save wells.")
    api = request.path_params["api"]
    _, _, eight = normalize_api(api)
    data = await _form_params(request)
    state = data.get("state") or state_from_api(api) or "tx"
    well = {
        "api": eight,
        "state": state,
        "well_name": data.get("name") or data.get("well_name") or format_api(eight, state),
        "well_no": data.get("well_no", ""),
        "lease_name": data.get("lease") or data.get("lease_name") or "",
        "county": data.get("county", ""),
        "operator": data.get("operator", ""),
    }
    if SAVED.is_saved(user["id"], eight, state=state):
        SAVED.remove(user["id"], eight, state=state)
    else:
        SAVED.save(user["id"], well)
    variant = data.get("variant") or "row"
    if variant == "saved":
        html = render("partials/saved.html", request, wells=SAVED.list(user["id"]))
        return fragment_or_page(request, html, nav=True, clear_suggest=True)
    html = render("partials/save_button.html", request, well=well, variant=variant)
    if is_htmx(request):
        return HTMLResponse(with_oob(request, html, nav=True))
    return RedirectResponse(well_href(well), status_code=303)


async def cache_panel(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to manage your cache.")
    CACHE.purge_expired(user["id"])
    html = render(
        "partials/cache.html",
        request,
        entries=CACHE.list_entries(user["id"]),
        stats=CACHE.stats(user["id"]),
    )
    return fragment_or_page(request, html, clear_suggest=True)


async def cache_clear(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to manage your cache.")
    data = await _form_params(request)
    CACHE.clear(user["id"], data.get("kind") or None)
    return await cache_panel(request)


async def cache_delete(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to manage your cache.")
    CACHE.delete(user["id"], int(request.path_params["entry_id"]))
    return await cache_panel(request)


async def cache_purge(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to manage your cache.")
    CACHE.purge_expired(user["id"])
    return await cache_panel(request)


async def healthz(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def pipelines(request: Request) -> JSONResponse:
    from wellnav.pipelines import query_geojson

    try:
        zoom = int(request.query_params.get("z") or 10)
    except ValueError:
        zoom = 10
    try:
        payload = query_geojson(
            bbox=request.query_params.get("bbox"),
            zoom=zoom,
            abandoned=request.query_params.get("abandoned") == "1",
            p5=request.query_params.get("p5") or "",
            system=request.query_params.get("system") or "",
            operator=request.query_params.get("operator") or "",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(payload)


async def pipeline_suggest(request: Request) -> HTMLResponse:
    from wellnav.pipelines import search_operators, search_systems

    q = request.query_params.get("q", "").strip()
    kind = request.query_params.get("pipe_mode") or "operator"
    if kind == "name":
        hits = search_systems(q, limit=20)
    else:
        hits = search_operators(q, limit=20)
        kind = "operator"
    html = render(
        "partials/pipeline_suggest.html",
        request,
        hits=hits,
        query=q,
        kind=kind,
    )
    return fragment_or_page(request, html)


async def pipeline_search(request: Request) -> HTMLResponse:
    from wellnav.pipelines import list_systems, owner_summary, search_operators, search_systems

    source = await _request_source(request)
    data = {key: str(value or "").strip() for key, value in source.items()}
    q = data.get("q", "")
    pipe_mode = data.get("pipe_mode") or "operator"
    p5 = data.get("p5", "")
    system = data.get("system", "")
    operator = data.get("operator", "")
    error = None
    auto_map = bool(p5 or system)
    kind = "operators"
    rows: list[dict] = []
    focus = None

    if p5 or system:
        rows = list_systems(p5=p5 or None, system=system or None, operator=operator or None)
        kind = "systems"
        focus = owner_summary(p5=p5 or None, system=system or None, operator=operator or None)
        if not rows and not focus:
            error = "No pipeline records match that operator or system."
    elif pipe_mode == "name":
        kind = "systems"
        if len(q) < 2:
            error = "Enter at least two characters of a pipeline or system name."
        else:
            rows = search_systems(q)
            if not rows:
                error = f"No pipeline names match “{q}”."
            elif len(rows) == 1:
                auto_map = True
                focus = owner_summary(p5=rows[0]["p5"] or None, system=rows[0]["system"] or None)
    else:
        kind = "operators"
        if len(q) < 2:
            error = "Enter at least two characters of a pipeline operator name or P-5 number."
        else:
            rows = search_operators(q)
            if not rows:
                error = f"No pipeline operators match “{q}”."
            elif len(rows) == 1:
                p5 = rows[0]["p5"]
                operator = rows[0]["operator"]
                auto_map = True
                rows = list_systems(p5=p5 or None, operator=None if p5 else operator)
                kind = "systems"
                focus = owner_summary(p5=p5 or None, operator=None if p5 else operator)

    html = render(
        "partials/pipeline_results.html",
        request,
        rows=rows,
        kind=kind,
        query=q,
        pipe_mode=pipe_mode,
        p5=p5,
        system=system,
        operator=operator or ((focus or {}).get("operator") or ""),
        focus=focus,
        auto_map=auto_map,
        error=error,
        subtitle=_pipeline_subtitle(kind, q=q, focus=focus, p5=p5, system=system),
    )
    return fragment_or_page(request, html, clear_suggest=True)


def _pipeline_subtitle(
    kind: str,
    *,
    q: str = "",
    focus: dict | None = None,
    p5: str = "",
    system: str = "",
) -> str:
    if focus:
        bits = [focus.get("operator") or "Pipeline operator"]
        if focus.get("p5"):
            bits.append(f"P-5 {focus['p5']}")
        if system:
            bits.append(system)
        return " · ".join(bits)
    if system:
        return system
    if p5:
        return f"P-5 {p5}"
    if q:
        return f"{'Pipeline' if kind == 'systems' else 'Operator'} “{q}”"
    return ""


async def pipeline_segment(request: Request) -> JSONResponse:
    from wellnav.pipelines import segment_detail

    try:
        tpms_id = int(request.path_params["tpms_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "invalid pipeline id"}, status_code=400)
    payload = segment_detail(tpms_id)
    if not payload:
        return JSONResponse({"error": "pipeline segment not found"}, status_code=404)
    return JSONResponse(payload)


async def pipeline_owner(request: Request) -> JSONResponse:
    from wellnav.pipelines import owner_summary

    payload = owner_summary(
        p5=request.query_params.get("p5") or "",
        system=request.query_params.get("system") or "",
        operator=request.query_params.get("operator") or "",
    )
    if not payload:
        return JSONResponse({"error": "no pipeline ownership match"}, status_code=404)
    return JSONResponse(payload)


async def disposal_sites(request: Request) -> JSONResponse:
    from wellnav.disposal import query_geojson

    site_raw = request.query_params.get("id")
    try:
        site_id = int(site_raw) if site_raw else None
    except ValueError:
        return JSONResponse({"error": "id must be an integer"}, status_code=400)
    try:
        payload = query_geojson(
            bbox=request.query_params.get("bbox"),
            site_id=site_id,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(payload)




async def _wait_json_body(request: Request) -> dict:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    form = await request.form()
    return {key: value for key, value in form.items()}


async def disposal_wait_summary(request: Request) -> JSONResponse:
    from wellnav.wait_reports import get_site_wait_summary, get_user_pref

    user = current_user(request)
    if not user:
        return JSONResponse({"error": "sign_in_required"}, status_code=401)
    try:
        site_id = int(request.path_params["site_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "site_id must be an integer"}, status_code=400)
    window_raw = request.query_params.get("window_hours")
    if window_raw:
        window_hours = window_raw
    else:
        window_hours = get_user_pref(user["id"])["avg_window_hours"]
    org_id = user.get("org_id")
    payload = get_site_wait_summary(site_id, window_hours=window_hours, org_id=org_id)
    return JSONResponse(payload)


async def disposal_wait_create(request: Request) -> JSONResponse:
    from wellnav.wait_reports import create_report

    user = current_user(request)
    if not user:
        return JSONResponse({"error": "sign_in_required"}, status_code=401)
    try:
        site_id = int(request.path_params["site_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "site_id must be an integer"}, status_code=400)
    data = await _wait_json_body(request)
    kind = str(data.get("report_kind") or data.get("kind") or "actual").strip().lower() or "actual"
    if kind != "actual":
        return JSONResponse(
            {
                "error": (
                    "Submit arrival and departure for the visit. "
                    "Estimated trips are added when directions are generated."
                )
            },
            status_code=400,
        )
    try:
        report = create_report(
            disposal_site_id=site_id,
            user_id=int(user["id"]),
            report_kind="actual",
            arrival_at=(str(data["arrival_at"]) if data.get("arrival_at") not in (None, "") else None),
            departure_at=(
                str(data["departure_at"]) if data.get("departure_at") not in (None, "") else None
            ),
            open_lanes=data.get("open_lanes"),
            notes=(str(data["notes"]) if data.get("notes") not in (None, "") else None),
            org_id=user.get("org_id"),
            now=(str(data["now"]) if data.get("now") not in (None, "") else None),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(report, status_code=201)


async def disposal_wait_flag(request: Request) -> JSONResponse:
    from wellnav.wait_reports import flag_report

    user = current_user(request)
    if not user:
        return JSONResponse({"error": "sign_in_required"}, status_code=401)
    try:
        report_id = int(request.path_params["report_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "report_id must be an integer"}, status_code=400)
    data = await _wait_json_body(request)
    reason = str(data.get("reason") or "").strip()
    try:
        report = flag_report(report_id, int(user["id"]), reason)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(report)


async def account_wait_prefs(request: Request) -> JSONResponse:
    from wellnav.wait_reports import get_user_pref, set_user_pref

    user = current_user(request)
    if not user:
        return JSONResponse({"error": "sign_in_required"}, status_code=401)
    if request.method == "GET":
        return JSONResponse(get_user_pref(int(user["id"])))
    data = await _wait_json_body(request)
    raw = data.get("avg_window_hours", request.query_params.get("avg_window_hours"))
    try:
        pref = set_user_pref(int(user["id"]), raw if raw is not None else 24)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(pref)


async def disposal_direction_estimate(request: Request) -> JSONResponse:
    """Create an organization estimated trip from generated directions to a disposal site."""
    from wellnav.disposal import drive_minutes, get_site
    from wellnav.wait_reports import get_user_pref, record_estimated_trip

    user = current_user(request)
    if not user:
        return JSONResponse({"error": "sign_in_required"}, status_code=401)
    try:
        site_id = int(request.path_params["site_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "site_id must be an integer"}, status_code=400)
    site = get_site(site_id)
    if not site:
        return JSONResponse({"error": "disposal site not found"}, status_code=404)
    data = await _wait_json_body(request)
    try:
        lat = _parse_optional_float(
            "" if data.get("lat") in (None, "") else str(data.get("lat")),
            name="lat",
        )
        lon = _parse_optional_float(
            "" if data.get("lon") in (None, "") else str(data.get("lon")),
            name="lon",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if lat is None or lon is None:
        return JSONResponse({"error": "lat and lon are required"}, status_code=400)
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return JSONResponse({"error": "lat and lon are out of range"}, status_code=400)
    org_id = user.get("org_id")
    if not org_id:
        return JSONResponse({"recorded": False, "reason": "no_org"})
    duration = drive_minutes(lat, lon, float(site["lat"]), float(site["lon"]))
    window_hours = get_user_pref(int(user["id"]))["avg_window_hours"]
    try:
        report = record_estimated_trip(
            disposal_site_id=site_id,
            user_id=int(user["id"]),
            org_id=int(org_id),
            duration_minutes=duration,
            window_hours=window_hours,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(report, status_code=201)


async def disposal_site_detail(request: Request) -> JSONResponse:
    from wellnav.disposal import get_site

    try:
        site_id = int(request.path_params["site_id"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "site_id must be an integer"}, status_code=400)
    site = get_site(site_id)
    if not site:
        return JSONResponse({"error": "disposal site not found"}, status_code=404)
    return JSONResponse(site)


async def disposal_suggest(request: Request) -> HTMLResponse:
    from wellnav.disposal import search_sites

    q = request.query_params.get("q", "").strip()
    kind = request.query_params.get("disp_mode") or "name"
    hits = search_sites(q, mode=kind, limit=20)
    html = render(
        "partials/disposal_suggest.html",
        request,
        hits=hits,
        query=q,
        kind=kind,
    )
    return fragment_or_page(request, html)


def _parse_optional_float(raw: str | None, *, name: str) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


async def disposal_search(request: Request) -> HTMLResponse:
    from wellnav.disposal import get_site, search_sites, stats

    source = await _request_source(request)
    data = {key: str(value or "").strip() for key, value in source.items()}
    q = data.get("q", "")
    disp_mode = data.get("disp_mode") or "name"
    site_id = data.get("site_id", "")
    error = None
    auto_map = False
    rows: list[dict] = []
    stored = stats().get("count") or 0

    if site_id:
        try:
            site = get_site(int(site_id))
        except ValueError:
            site = None
        if site:
            rows = [site]
            auto_map = True
        else:
            error = "That waste disposal site was not found in the local overlay."
    elif len(q) < 2:
        if stored == 0:
            error = "Local disposal overlay is empty — run python -m wellnav.ingest load-disposal"
        else:
            error = "Enter at least two characters of a facility, operator, permit, or county."
    else:
        rows = search_sites(q, mode=disp_mode)
        if not rows:
            error = f"No commercial waste disposal sites match “{q}”."
        elif len(rows) == 1:
            auto_map = True

    html = render(
        "partials/disposal_results.html",
        request,
        rows=rows,
        query=q,
        disp_mode=disp_mode,
        auto_map=auto_map,
        error=error,
        stored=stored,
        subtitle=_disposal_subtitle(q=q, rows=rows, disp_mode=disp_mode),
    )
    return fragment_or_page(request, html, clear_suggest=True)


def _disposal_subtitle(*, q: str = "", rows: list[dict] | None = None, disp_mode: str = "name") -> str:
    if rows and len(rows) == 1:
        site = rows[0]
        bits = [site.get("facility") or site.get("operator") or "Waste site"]
        if site.get("county"):
            bits.append(f"{site['county']} County")
        if site.get("permit_no"):
            bits.append(site["permit_no"])
        return " · ".join(bits)
    labels = {"operator": "Operator", "permit": "Permit", "county": "County"}
    label = labels.get(disp_mode, "Facility")
    if q:
        return f"{label} “{q}”"
    return ""


def service_worker(_request: Request) -> FileResponse:
    return FileResponse(
        ROOT / "static" / "js" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def web_manifest(_request: Request) -> FileResponse:
    return FileResponse(
        ROOT / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def offline_routes(request: Request) -> JSONResponse:
    """Driving or direct routes from the device location to each pinned place."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Expected a JSON body."}, status_code=400)
    try:
        origin, destinations = parse_pack_request(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(plan_offline_routes(origin, destinations))


def offline_usgs_tile(request: Request) -> Response:
    try:
        z = int(request.path_params["z"])
        y = int(request.path_params["y"])
        x = int(request.path_params["x"])
    except (KeyError, TypeError, ValueError):
        return PlainTextResponse("invalid tile", status_code=400)
    if not validate_tile(z, x, y):
        return PlainTextResponse("tile out of range", status_code=404)
    try:
        upstream = requests.get(
            usgs_url(z, x, y),
            timeout=20,
            headers={"User-Agent": "WellNavigation/1.0 (offline pack; wellnav@simba.services)"},
        )
    except requests.RequestException:
        return PlainTextResponse("tile upstream unavailable", status_code=502)
    if upstream.status_code != 200 or not upstream.content:
        return PlainTextResponse("tile not found", status_code=upstream.status_code or 404)
    content_type = upstream.headers.get("Content-Type") or "image/jpeg"
    if "text" in content_type or "json" in content_type:
        return PlainTextResponse("tile not found", status_code=404)
    return Response(
        upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


app = Starlette(
    routes=[
        Route("/", index),
        Route("/account", account),
        Route("/account/delete", account_delete, methods=["GET", "POST"]),
        Route("/ux/recordings", ux_recording_start, methods=["POST"]),
        Route("/ux/recordings/{recording_id}/chunk", ux_recording_chunk, methods=["POST"]),
        Route("/ux/recordings/{recording_id}/finish", ux_recording_finish, methods=["POST"]),
        Route("/ux/recordings/{recording_id}/file", ux_recording_file),
        Route("/ux/recordings/{recording_id}", ux_recording_replay),
        Route("/privacy", privacy),
        Route("/terms", terms),
        Route("/feedback", feedback_panel, methods=["GET"]),
        Route("/feedback", feedback_create, methods=["POST"]),
        Route("/feedback/{message_id:int}/delete", feedback_delete, methods=["POST"]),
        Route("/org", org_panel),
        Route("/org/members/{member_id:int}/remove", org_remove, methods=["POST"]),
        Route("/org/members/{member_id:int}/role", org_role, methods=["POST"]),
        Route("/billing", billing_panel),
        Route("/billing/checkout", billing_checkout, methods=["POST"]),
        Route("/billing/seats", billing_seats, methods=["POST"]),
        Route("/billing/portal", billing_portal, methods=["POST"]),
        Route("/billing/success", billing_success),
        Route("/billing/webhook", billing_webhook, methods=["POST"]),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/login/verify", verify_code, methods=["GET", "POST"]),
        Route("/login/resend", resend_code, methods=["POST"]),
        Route("/login/forgot", forgot, methods=["GET", "POST"]),
        Route("/login/forgot/verify", verify_code, methods=["GET", "POST"]),
        Route("/login/forgot/resend", resend_code, methods=["POST"]),
        Route("/login/forgot/password", reset_password, methods=["GET", "POST"]),
        Route("/register", register, methods=["GET", "POST"]),
        Route("/register/verify", verify_code, methods=["GET", "POST"]),
        Route("/register/resend", resend_code, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/operators", operators),
        Route("/search", search, methods=["GET", "POST"]),
        Route("/well/{api}", well_detail),
        Route("/saved", saved_list),
        Route("/saved/bulk", saved_bulk, methods=["POST"]),
        Route("/saved/{api}", saved_toggle, methods=["POST"]),
        Route("/cache", cache_panel),
        Route("/cache/clear", cache_clear, methods=["POST"]),
        Route("/cache/purge", cache_purge, methods=["POST"]),
        Route("/cache/{entry_id:int}/delete", cache_delete, methods=["POST"]),
        Route("/pipelines", pipelines),
        Route("/pipelines/suggest", pipeline_suggest),
        Route("/pipelines/search", pipeline_search, methods=["GET", "POST"]),
        Route("/pipelines/owner", pipeline_owner),
        Route("/pipelines/segment/{tpms_id}", pipeline_segment),
        Route("/disposal", disposal_sites),
        Route("/disposal/suggest", disposal_suggest),
        Route("/disposal/search", disposal_search, methods=["GET", "POST"]),
        Route("/disposal/site/{site_id:int}", disposal_site_detail),
        Route("/disposal/{site_id:int}/wait", disposal_wait_summary, methods=["GET"]),
        Route("/disposal/{site_id:int}/wait", disposal_wait_create, methods=["POST"]),
        Route("/disposal/{site_id:int}/directions", disposal_direction_estimate, methods=["POST"]),
        Route("/disposal/wait/{report_id:int}/flag", disposal_wait_flag, methods=["POST"]),
        Route("/account/wait-prefs", account_wait_prefs, methods=["GET", "POST"]),
        Route("/healthz", healthz),
        Route("/sw.js", service_worker),
        Route("/manifest.webmanifest", web_manifest),
        Route("/offline/routes", offline_routes, methods=["POST"]),
        Route("/offline/tiles/{z:int}/{y:int}/{x:int}", offline_usgs_tile),
        Mount("/static", StaticFiles(directory="static"), name="static"),
    ],
    middleware=[
        Middleware(GZipMiddleware, minimum_size=500),
        Middleware(HtmlCacheMiddleware),
        Middleware(
            SessionMiddleware,
            secret_key=session_secret(),
            session_cookie="wellnav",
            same_site="lax",
            https_only=(os.environ.get("WELLNAV_HTTPS") or "").strip().lower() in {"1", "true", "yes"},
            max_age=SESSION_MAX_AGE,
        ),
        Middleware(RequireSignInMiddleware),
    ],
)

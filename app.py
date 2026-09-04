"""HTMX HTML server: pages and fragments only. No JSON API."""

from __future__ import annotations

from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from wellnav.accounts import CACHE, LOCATION_TTL, OPERATOR_TTL, SAVED, SEARCH_TTL, USERS
from wellnav.auth import current_user_id, login_user, logout_user, session_secret
from wellnav.parsers import format_api, normalize_api
from wellnav.repository import REPO

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
            }.items()
            if value
        }
    )
    path = f"/well/{well['api']}"
    return f"{path}?{query}" if query else path


def search_href(**parts: object) -> str:
    query = urlencode(
        {key: str(value) for key, value in parts.items() if value not in (None, "")}
    )
    return f"/search?{query}" if query else "/search"


def cache_key(*parts: object) -> str:
    return "|".join(str(part or "").strip().lower() for part in parts)


templates.env.globals["well_href"] = well_href
templates.env.globals["search_href"] = search_href


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def current_user(request: Request) -> dict | None:
    uid = current_user_id(request)
    return USERS.get(uid) if uid else None


def view_ctx(request: Request, **extra) -> dict:
    user = current_user(request)
    saved_apis = SAVED.api_set(user["id"]) if user else set()
    recent = CACHE.recent_searches(user["id"]) if user else []
    return {
        "user": user,
        "saved_apis": saved_apis,
        "saved_count": len(saved_apis),
        "recent": recent,
        "cache_stats": CACHE.stats(user["id"]) if user else None,
        **extra,
    }


def render(name: str, request: Request | None = None, **context) -> str:
    if request is not None:
        context = {**view_ctx(request), **context}
    return templates.get_template(name).render(context)


def page(request: Request, *, results_html: str = "", map_html: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        view_ctx(request, counts=REPO.counts("tx"), results_html=results_html, map_html=map_html),
    )


def with_oob(
    request: Request,
    html: str,
    *,
    recent: bool = False,
    nav: bool = False,
    clear_suggest: bool = False,
) -> str:
    parts = [html]
    if clear_suggest:
        parts.append('<div id="operator-suggest" hx-swap-oob="innerHTML"></div>')
    if recent:
        parts.append(
            '<div id="recent-slot" hx-swap-oob="innerHTML">'
            + render("partials/recent.html", request)
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
    recent: bool = False,
    nav: bool = False,
    clear_suggest: bool = False,
) -> HTMLResponse:
    if is_htmx(request):
        return HTMLResponse(
            with_oob(request, html, recent=recent, nav=nav, clear_suggest=clear_suggest)
        )
    return page(request, results_html=html, map_html=map_html)


def redirect_to(request: Request, url: str) -> HTMLResponse | RedirectResponse:
    if is_htmx(request):
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = url
        return response
    return RedirectResponse(url, status_code=303)


def login_required_html(request: Request, message: str = "Sign in to continue.") -> HTMLResponse:
    html = render("partials/login.html", request, error=message, next_path=request.url.path)
    return fragment_or_page(request, html, clear_suggest=True)


def _params(request: Request) -> dict[str, str]:
    source = request.query_params if request.method == "GET" else {}
    return {key: (value or "").strip() for key, value in source.items()}


async def _form_params(request: Request) -> dict[str, str]:
    if request.method != "POST":
        return _params(request)
    form = await request.form()
    return {key: str(value).strip() if value is not None else "" for key, value in form.items()}


async def index(request: Request) -> HTMLResponse:
    return page(request)


async def account(request: Request) -> HTMLResponse:
    html = render("partials/account.html", request)
    return fragment_or_page(request, html, clear_suggest=True)


async def login(request: Request) -> HTMLResponse | RedirectResponse:
    if request.method == "GET":
        html = render("partials/login.html", request, error=None, next_path=request.query_params.get("next") or "/")
        return fragment_or_page(request, html)
    data = await _form_params(request)
    user = USERS.authenticate(data.get("username", ""), data.get("password", ""))
    if not user:
        html = render(
            "partials/login.html",
            request,
            error="Username or password is incorrect.",
            next_path=data.get("next") or "/",
            username=data.get("username", ""),
        )
        return fragment_or_page(request, html)
    login_user(request, user["id"], user["username"])
    return redirect_to(request, data.get("next") or "/")


async def register(request: Request) -> HTMLResponse | RedirectResponse:
    if request.method == "GET":
        html = render("partials/register.html", request, error=None)
        return fragment_or_page(request, html)
    data = await _form_params(request)
    user, error = USERS.register(data.get("username", ""), data.get("password", ""))
    if error or not user:
        html = render(
            "partials/register.html",
            request,
            error=error or "Could not create the account.",
            username=data.get("username", ""),
        )
        return fragment_or_page(request, html)
    login_user(request, user["id"], user["username"])
    return redirect_to(request, "/")


async def logout(request: Request) -> HTMLResponse | RedirectResponse:
    logout_user(request)
    return redirect_to(request, "/")


async def operators(request: Request) -> HTMLResponse:
    if request.query_params.get("mode", "operator") != "operator":
        return fragment_or_page(request, "")
    q = request.query_params.get("q", "").strip()
    user = current_user(request)
    rows = REPO.search_operators(q)
    if user and rows:
        CACHE.set(user["id"], "operators", cache_key(q), rows, OPERATOR_TTL)
    html = render("partials/operators.html", request, operators=rows, query=q, source="local")
    return fragment_or_page(request, html)


async def search(request: Request) -> HTMLResponse:
    data = await _form_params(request)
    user = current_user(request)
    mode = data.get("mode") or "api"
    page_size = int(data.get("page_size") or 50)
    offset = int(data.get("offset") or 0)
    q = data.get("q", "")
    operator_number = data.get("operator_number", "")
    operator_name = data.get("operator_name", "")
    error = None
    context = {
        "mode": mode,
        "query": q or operator_name,
        "subtitle": "",
        "operator_number": operator_number,
        "operator_name": operator_name,
        "lease_no": data.get("lease_no", ""),
        "district": data.get("district", ""),
        "lease_type": data.get("lease_type", ""),
        "page_size": page_size,
        "offset": offset,
        "from_cache": False,
    }
    counts = REPO.counts("tx")
    try:
        if user and (q or operator_number):
            CACHE.remember_recent(
                user["id"],
                {
                    "mode": mode,
                    "q": q,
                    "operator_number": operator_number,
                    "operator_name": operator_name,
                    "label": operator_name or q,
                },
            )
        if mode == "operator" and not operator_number and q:
            matches = REPO.search_operators(q)
            if not matches:
                error = f"No operators match “{q}” in the local database."
            elif len(matches) > 1:
                if user:
                    CACHE.set(user["id"], "operators", cache_key(q), matches[:40], OPERATOR_TTL)
                html = render("partials/operators.html", request, operators=matches[:40], query=q)
                return fragment_or_page(request, html, recent=True, clear_suggest=True)
            else:
                operator_number, operator_name = matches[0]["number"], matches[0]["name"]
                context["operator_number"] = operator_number
                context["operator_name"] = operator_name
                context["query"] = operator_name
        result = REPO.search(
            mode=mode,
            q=q,
            operator_number=operator_number,
            lease_no=context["lease_no"],
            district=context["district"],
            page_size=page_size,
            offset=offset,
        )
        if result["total"] == 0 and mode == "name" and q and not context["lease_no"]:
            leases = REPO.search_leases(q)
            if len(leases) == 1:
                context["lease_no"] = leases[0]["lease_no"]
                context["district"] = leases[0]["district"]
                result = REPO.search(
                    mode=mode,
                    q=q,
                    lease_no=context["lease_no"],
                    district=context["district"],
                    page_size=page_size,
                    offset=offset,
                )
                context["subtitle"] = f"Lease {leases[0]['name']}"
            elif len(leases) > 1:
                html = render("partials/leases.html", request, leases=leases, query=q)
                return fragment_or_page(request, html, recent=True, clear_suggest=True)
        if result["total"] == 0 and counts["total"] == 0:
            context["subtitle"] = "Local database is empty — run python -m wellnav.ingest load-texas"
        elif result["total"] and not context["subtitle"]:
            if mode == "api" and q:
                context["subtitle"] = f"API {q}"
            elif mode == "operator":
                context["subtitle"] = f"Operator {operator_name or q}"
            elif context["lease_no"]:
                context["subtitle"] = f"Lease {q}"
            elif q:
                context["subtitle"] = f"Name “{q}”"
        if user and result["total"]:
            CACHE.set(
                user["id"],
                "search",
                cache_key(mode, q, operator_number, context["lease_no"], context["district"], offset, page_size),
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
    return fragment_or_page(request, html, recent=True, clear_suggest=True)


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
    stored = REPO.get_well(eight)
    location = _location_for(eight, stored)
    if user and location.get("found"):
        CACHE.set(user["id"], "location", eight, location, LOCATION_TTL)
    well = {
        "api": eight,
        "api_display": format_api(eight),
        "well_name": request.query_params.get("name") or (stored or {}).get("well_name") or format_api(eight),
        "lease_name": request.query_params.get("lease") or (stored or {}).get("lease_name") or "",
        "county": request.query_params.get("county") or (stored or {}).get("county") or "",
        "operator": request.query_params.get("operator") or (stored or {}).get("operator") or "",
        "well_no": request.query_params.get("well_no") or (stored or {}).get("well_no") or "",
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
    html = render("partials/saved.html", request, wells=wells)
    return fragment_or_page(request, html, clear_suggest=True)


async def saved_toggle(request: Request) -> HTMLResponse:
    user = current_user(request)
    if not user:
        return login_required_html(request, "Sign in to save wells.")
    api = request.path_params["api"]
    _, _, eight = normalize_api(api)
    data = await _form_params(request)
    well = {
        "api": eight,
        "well_name": data.get("name") or data.get("well_name") or format_api(eight),
        "well_no": data.get("well_no", ""),
        "lease_name": data.get("lease") or data.get("lease_name") or "",
        "county": data.get("county", ""),
        "operator": data.get("operator", ""),
    }
    if SAVED.is_saved(user["id"], eight):
        SAVED.remove(user["id"], eight)
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


app = Starlette(
    routes=[
        Route("/", index),
        Route("/account", account),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/register", register, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/operators", operators),
        Route("/search", search, methods=["GET", "POST"]),
        Route("/well/{api}", well_detail),
        Route("/saved", saved_list),
        Route("/saved/{api}", saved_toggle, methods=["POST"]),
        Route("/cache", cache_panel),
        Route("/cache/clear", cache_clear, methods=["POST"]),
        Route("/cache/purge", cache_purge, methods=["POST"]),
        Route("/cache/{entry_id:int}/delete", cache_delete, methods=["POST"]),
        Route("/healthz", healthz),
        Mount("/static", StaticFiles(directory="static"), name="static"),
    ],
    middleware=[
        Middleware(
            SessionMiddleware,
            secret_key=session_secret(),
            session_cookie="wellnav",
            same_site="lax",
            https_only=False,
            max_age=60 * 60 * 24 * 30,
        )
    ],
)

"""HTTP and WebSocket API for the route simulator."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from route_sim.gpx import route_gpx
from route_sim.route_io import parse_coordinates
from route_sim.simulator import TICK_SECONDS, Simulator

SIM = Simulator()
_CLIENTS: set[WebSocket] = set()


async def _json_object(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception as exc:
        raise ValueError("Body must be JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _error(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


async def broadcast(payload: dict) -> None:
    dead: list[WebSocket] = []
    for socket in list(_CLIENTS):
        try:
            await socket.send_json(payload)
        except Exception:
            dead.append(socket)
    for socket in dead:
        _CLIENTS.discard(socket)


async def emit(force: bool = False) -> dict:
    payload, changed = SIM.capture()
    if force or changed:
        await broadcast(payload)
    return payload


async def index(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("Route simulator API\n")


async def get_location(_request: Request) -> JSONResponse:
    return JSONResponse(await emit(force=False))


async def get_route(_request: Request) -> JSONResponse:
    return JSONResponse(SIM.route_payload())


async def post_route(request: Request) -> JSONResponse:
    try:
        coordinates = parse_coordinates(await _json_object(request))
    except ValueError as exc:
        return _error(str(exc))
    SIM.set_route(coordinates)
    await emit(force=True)
    return JSONResponse(SIM.route_payload())


async def post_simulation(request: Request) -> JSONResponse:
    try:
        body = await _json_object(request)
        if "action" not in body or not isinstance(body.get("action"), str):
            raise ValueError('action must be "play", "pause", or "reset"')
        speed = body["speedKmh"] if "speedKmh" in body else None
        loop = body["loop"] if "loop" in body else None
        SIM.apply(body["action"], speed_kmh=speed, loop=loop)
    except ValueError as exc:
        return _error(str(exc))
    return JSONResponse(await emit(force=True))


async def get_route_gpx(_request: Request) -> Response:
    body = route_gpx(SIM.coordinates)
    return Response(
        body,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": 'attachment; filename="route.gpx"'},
    )


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _CLIENTS.add(websocket)
    try:
        payload, changed = SIM.capture()
        if changed:
            await broadcast(payload)
        else:
            await websocket.send_json(payload)
        while True:
            incoming = await websocket.receive()
            if incoming["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        _CLIENTS.discard(websocket)


async def ticker() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        await emit(force=True)


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    task = asyncio.create_task(ticker())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/location", get_location, methods=["GET"]),
        Route("/api/route", get_route, methods=["GET"]),
        Route("/api/route", post_route, methods=["POST"]),
        Route("/api/simulation", post_simulation, methods=["POST"]),
        Route("/api/route.gpx", get_route_gpx, methods=["GET"]),
        WebSocketRoute("/ws", websocket_endpoint),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ],
    lifespan=lifespan,
)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the route simulator API")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

"""Entrypoint thật của paosd (doc 19 P-M0-5, lát 5c). `paosd` (`[project.scripts]`
ở pyproject.toml) trỏ vào `main()` ở đây."""

from __future__ import annotations

import asyncio
from pathlib import Path

import uvicorn

from apps.paosd.wiring import build_daemon

_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "workspace" / ".paos" / "state.db"
_HOST = "127.0.0.1"
_PORT = 8787


async def _run() -> None:
    daemon = await build_daemon(_DEFAULT_DB_PATH)
    config = uvicorn.Config(daemon.app, host=_HOST, port=_PORT, log_level="info")
    server = uvicorn.Server(config)
    try:
        # uvicorn.Server tự cài signal handler cho SIGINT/SIGTERM (cả Windows) —
        # serve() chỉ trả về sau khi nhận tín hiệu dừng, không cần tự bắt riêng.
        await server.serve()
    finally:
        await daemon.stop()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

"""StateStore — chủ sở hữu duy nhất của kết nối ghi tới state.db (single-writer actor, ADR-0024)."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import aiosqlite

from kernel import clock
from kernel.errors import ErrorCode, PaosError

T = TypeVar("T")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_BUSY_TIMEOUT_MS = 200


@dataclass
class _WriteJob:
    fn: Callable[[aiosqlite.Connection], Awaitable[Any]]
    future: asyncio.Future[Any]


class StateStore:
    """Chỉ module này được mở kết nối ghi tới state.db (doc 17 §3: không truy cập
    state.db ngoài kernel/state/). Ghi luôn qua write() -> actor loop -> 1 transaction.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_conn: aiosqlite.Connection | None = None
        self._queue: asyncio.Queue[_WriteJob] = asyncio.Queue()
        self._actor_task: asyncio.Task[None] | None = None

    def _conn(self) -> aiosqlite.Connection:
        """Kết nối ghi hiện tại. Dùng thay `assert` — assert bị strip khi chạy
        `-O`, không phù hợp cho invariant check ở code production (P8, S101)."""
        if self._write_conn is None:
            raise RuntimeError("StateStore.start() chưa được gọi hoặc đã stop()")
        return self._write_conn

    async def start(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        existed_before = self._db_path.exists()

        self._write_conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        try:
            await self._bootstrap(existed_before)
        except sqlite3.DatabaseError as exc:
            # aiosqlite.connect() không tự validate định dạng file — file hỏng thật
            # ("file is not a database", "malformed") chỉ lộ ra ở thao tác đọc/ghi
            # ĐẦU TIÊN bên trong _bootstrap(), sớm hơn _check_integrity() kịp chạy.
            # sqlite3.DatabaseError là lớp CHA của OperationalError — nhánh
            # OperationalError trong _bootstrap() (tranh chấp khoá) bắt trước, nhánh
            # này chỉ còn lại các DatabaseError khác, tức là hỏng file thật (P8: không
            # để sqlite3.DatabaseError thô rò ra ngoài kernel).
            await self._write_conn.close()
            self._write_conn = None
            raise PaosError(
                ErrorCode.INTERNAL,
                f"state.db hỏng, không mở được: {exc}",
                hint="Khôi phục từ .paos/backups/ hoặc chạy `paosctl doctor --fix`",
            ) from exc
        except Exception:
            # Kết nối đã mở thành công nhưng bootstrap lỗi — PHẢI đóng lại, nếu
            # không thread nền của aiosqlite (non-daemon) treo cả tiến trình,
            # kể cả khi test/caller không bao giờ gọi stop() vì start() đã raise.
            await self._write_conn.close()
            self._write_conn = None
            raise

        self._actor_task = asyncio.create_task(self._actor_loop())

    async def _bootstrap(self, existed_before: bool) -> None:
        conn = self._conn()
        # busy_timeout PHẢI đặt trước journal_mode — bản thân journal_mode=WAL cũng
        # là một thao tác cần khóa ghi, có thể bị SQLITE_BUSY nếu tiến trình khác
        # đang giữ khóa (R01). Toàn bộ bootstrap dưới đây nằm trong 1 khối bắt lỗi
        # duy nhất: MỌI OperationalError từ đây tới hết transaction đầu tiên đều là
        # dấu hiệu tranh chấp khóa, không phải lỗi ngẫu nhiên khác.
        await conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await self._acquire_migration_lock()
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            cursor = await conn.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in await cursor.fetchall()}
            pending = [(v, f) for v, f in self._pending_migration_files() if v not in applied]
            await conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            await self._safe_rollback()
            raise PaosError(
                ErrorCode.CONFLICT,
                "Một tiến trình paosd khác có thể đang dùng state.db",
                hint="Đợi tiến trình kia hoàn tất rồi thử lại; kiểm bằng `paosctl doctor`",
            ) from exc
        except Exception:
            await self._safe_rollback()
            raise

        if pending:
            if existed_before:
                await self._backup_before_migrate()
            try:
                await self._acquire_migration_lock()
                await self._apply_migrations(pending)
                await conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                await self._safe_rollback()
                raise PaosError(
                    ErrorCode.CONFLICT,
                    "Một tiến trình paosd khác có thể đang migrate state.db",
                    hint="Đợi tiến trình kia hoàn tất rồi thử lại; kiểm bằng `paosctl doctor`",
                ) from exc
            except Exception:
                await self._safe_rollback()
                raise

        await self._check_integrity()

    async def _acquire_migration_lock(self) -> None:
        """BEGIN IMMEDIATE — không tự bắt lỗi ở đây, để try/except ở start() xử lý thống nhất."""
        await self._conn().execute("BEGIN IMMEDIATE")

    async def _safe_rollback(self) -> None:
        try:
            await self._conn().execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass  # không có transaction nào đang mở — không cần rollback

    async def _apply_migrations(self, pending: list[tuple[int, Path]]) -> None:
        conn = self._conn()
        for version, path in pending:
            statements = [
                s.strip() for s in path.read_text(encoding="utf-8").split(";") if s.strip()
            ]
            for stmt in statements:
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, clock.now().isoformat()),
            )

    async def stop(self) -> None:
        if self._actor_task is not None:
            await self._queue.join()
            self._actor_task.cancel()
            try:
                await self._actor_task
            except asyncio.CancelledError:
                pass
            self._actor_task = None
        if self._write_conn is not None:
            await self._write_conn.close()
            self._write_conn = None

    async def write(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Gửi một đơn vị công việc cho actor. fn KHÔNG được await I/O ngoài
        tiến trình (ADR-0024 điều khoản 3) — chỉ thao tác trên `conn` được cấp.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        await self._queue.put(_WriteJob(fn=fn, future=future))
        return await future

    async def read(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Đọc qua kết nối read-only riêng — chạy song song được, không qua actor."""
        uri = f"{self._db_path.resolve().as_uri()}?mode=ro"
        async with aiosqlite.connect(uri, uri=True, isolation_level=None) as conn:
            return await fn(conn)

    async def next_pid(self) -> int:
        async def _bump(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'pid' RETURNING value"
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("counters thiếu hàng 'pid' — migration 001 chưa chạy đúng")
            return int(row[0])

        return await self.write(_bump)

    async def _actor_loop(self) -> None:
        conn = self._conn()
        while True:
            job = await self._queue.get()
            try:
                await conn.execute("BEGIN")
                result = await job.fn(conn)
                await conn.execute("COMMIT")
                if not job.future.cancelled():
                    job.future.set_result(result)
            except Exception as exc:
                await conn.execute("ROLLBACK")
                if not job.future.cancelled():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def _pending_migration_files(self) -> list[tuple[int, Path]]:
        result: list[tuple[int, Path]] = []
        for f in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            version = int(f.name.split("_", 1)[0])
            result.append((version, f))
        return result

    async def _backup_before_migrate(self) -> None:
        backups_dir = self._db_path.parent / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = clock.now().strftime("%Y%m%d-%H%M%S")
        dest = backups_dir / f"{self._db_path.stem}-{stamp}.db"
        try:
            await self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(self._db_path, dest)
        except OSError as exc:
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Không backup được state.db trước khi migrate: {exc}",
                hint="Kiểm quyền ghi và dung lượng đĩa trống ở thư mục workspace/.paos/backups/",
                context={"dest": str(dest)},
            ) from exc

    async def _check_integrity(self) -> None:
        cursor = await self._conn().execute("PRAGMA integrity_check")
        row = await cursor.fetchone()
        if row is None or row[0] != "ok":
            raise PaosError(
                ErrorCode.INTERNAL,
                f"state.db không toàn vẹn: {row}",
                hint="Khôi phục từ .paos/backups/ hoặc chạy `paosctl doctor --fix`",
                context={"result": str(row)},
            )

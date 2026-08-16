"""PluginProcess — spawn subprocess plugin, giao tiếp JSON-RPC qua stdio, giám
sát crash + tự restart tối đa 3 lần (doc 09 §5, ADR-0005, ADR-0032, doc 19 P-M8-1).

Cùng khuôn `apps/paosd/energy_engine.py`: hàm/method thuần tối đa có thể, phụ
thuộc I/O thật (subprocess) tiêm qua tham số injectable (`spawn_fn`) để test
không cần spawn tiến trình thật. `on_crash` là callback (không phải EventBus
trực tiếp) — cùng lý do `EnergyEngine.__init__(snapshot_fn=...)`: tách khỏi
I/O thật để test kiểm được logic restart/giới hạn số lần mà không cần hạ tầng
Event thật, caller (`kernel/registry/registry.py`) tự nối EventBus.

**Giới hạn tài nguyên (doc 09 §5, CHỦ Ý một phần, ADR-0030 cùng tinh thần):**
`cpu_sec`/`open_files` enforce THẬT qua `resource.setrlimit()` — CHỈ có trên
POSIX (Linux/macOS), Windows KHÔNG có module `resource` (máy dev thật của dự
án là Windows, xem `docs/environment-baseline.md`) — trên Windows, 2 giới hạn
này được ĐỌC/GHI vào manifest nhưng KHÔNG enforce, không giả vờ có (cùng
nguyên tắc ADR-0030 với `psutil.sensors_temperatures()`). `rss_mb` CHƯA enforce
trên NỀN TẢNG NÀO — `RLIMIT_RSS`/`RLIMIT_AS` không đáng tin cậy trên kernel
Linux hiện đại; cần đo chủ động qua `psutil.Process(pid).memory_info()` định
kỳ (backlog, xem docs/backlog.md). `wall_sec` dùng làm CẢ HAI: (1) thời gian
sống tối đa của tiến trình (bị kill nếu vượt), (2) trần chờ phản hồi 1 lượt
gọi RPC (`asyncio.wait_for`) — doc 09 §5 không tách riêng 2 khái niệm này,
diễn giải hợp lý nhất là "không lượt gọi nào được lâu hơn cả tiến trình được
phép sống"."""

from __future__ import annotations

import asyncio
import itertools
import shlex
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from kernel.errors import ErrorCode, PaosError
from kernel.plugin.protocol import decode_line, encode_request

_DEFAULT_CALL_TIMEOUT_S = 300.0
_MAX_RESTARTS = 3

_posix_resource: Any = None  # Windows — module "resource" không tồn tại, xem docstring module
try:
    import resource

    _posix_resource = resource
except ImportError:
    pass


class PluginProcess:
    def __init__(
        self,
        plugin_id: str,
        entry_command: str,
        cwd: Path,
        *,
        cpu_sec: float | None = None,
        rss_mb: float | None = None,
        wall_sec: float | None = None,
        open_files: int | None = None,
        on_crash: Callable[[str, str], Awaitable[None]] | None = None,
        spawn_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._entry_command = entry_command
        self._cwd = cwd
        self._cpu_sec = cpu_sec
        self._rss_mb = rss_mb  # CHƯA enforce, xem docstring module
        self._wall_sec = wall_sec
        self._open_files = open_files
        self._on_crash = on_crash
        self._spawn_fn = spawn_fn or asyncio.create_subprocess_exec
        self._process: asyncio.subprocess.Process | None = None
        self._started_at: float | None = None
        self._lock = asyncio.Lock()
        self._next_id = itertools.count(1)
        self._restart_count = 0
        self._permanently_dead = False
        self._dead_reason: str | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def _preexec_limits(self) -> Callable[[], None] | None:
        """`preexec_fn` cho `create_subprocess_exec` — CHỈ áp dụng trên POSIX
        (`_posix_resource is None` trên Windows, module docstring)."""
        resource_mod = _posix_resource
        if resource_mod is None or (self._cpu_sec is None and self._open_files is None):
            return None
        cpu_sec = self._cpu_sec
        open_files = self._open_files

        def _set_limits() -> None:
            if cpu_sec is not None:
                cpu = int(cpu_sec)
                resource_mod.setrlimit(resource_mod.RLIMIT_CPU, (cpu, cpu))
            if open_files is not None:
                resource_mod.setrlimit(resource_mod.RLIMIT_NOFILE, (open_files, open_files))

        return _set_limits

    async def _spawn(self) -> None:
        # posix=False trên Windows — shlex mặc định (posix=True) coi "\" là
        # ký tự escape, phá hỏng đường dẫn kiểu Windows (vd
        # "C:\Users\...\python.exe") có trong `entry:` (đường dẫn
        # `sys.executable`/model path tuyệt đối rất thường gặp thực tế).
        args = shlex.split(self._entry_command, posix=sys.platform != "win32")
        kwargs: dict[str, Any] = {
            "cwd": str(self._cwd),
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        preexec = self._preexec_limits()
        if preexec is not None and sys.platform != "win32":
            kwargs["preexec_fn"] = preexec
        self._process = await self._spawn_fn(*args, **kwargs)
        self._started_at = time.monotonic()

    async def ensure_started(self) -> None:
        if self._permanently_dead:
            raise PaosError(
                ErrorCode.PROVIDER_DOWN,
                f"Plugin {self._plugin_id} đã crash quá {_MAX_RESTARTS} lần, "
                "không tự khởi động lại nữa",
                retryable=False,
                hint="Kiểm log stderr của plugin, sửa lỗi rồi `paosctl plugin enable` lại",
                context={"plugin_id": self._plugin_id, "reason": self._dead_reason or ""},
            )
        if self.is_running:
            return
        await self._spawn()

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            await self._maybe_restart_if_expired()
            await self.ensure_started()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise PaosError(
                    ErrorCode.INTERNAL,
                    f"Plugin {self._plugin_id} chưa khởi động đúng (thiếu stdin/stdout)",
                    hint="Lỗi nội bộ — báo lại nếu gặp",
                    context={"plugin_id": self._plugin_id},
                )

            request_id = next(self._next_id)
            timeout = self._wall_sec or _DEFAULT_CALL_TIMEOUT_S
            try:
                request_line = encode_request(request_id, method, params)
                process.stdin.write(request_line.encode("utf-8"))
                await process.stdin.drain()
                raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            except (TimeoutError, ConnectionError, BrokenPipeError) as exc:
                await self._handle_crash(f"timeout/mất kết nối lúc gọi '{method}': {exc}")
                raise PaosError(
                    ErrorCode.PROVIDER_TIMEOUT,
                    f"Plugin {self._plugin_id} không phản hồi '{method}' trong {timeout:.0f}s",
                    retryable=True,
                    hint="Kiểm plugin có bị treo không — xem stderr trong plugin.crashed",
                    context={"plugin_id": self._plugin_id, "method": method},
                ) from exc

            if not raw_line:
                await self._handle_crash(f"tiến trình đóng stdout giữa lúc gọi '{method}'")
                raise PaosError(
                    ErrorCode.PROVIDER_DOWN,
                    f"Plugin {self._plugin_id} đóng kết nối khi gọi '{method}'",
                    retryable=True,
                    hint="Xem plugin.crashed để biết stderr gần nhất",
                    context={"plugin_id": self._plugin_id, "method": method},
                )

            try:
                _, result, rpc_error = decode_line(raw_line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                await self._handle_crash(f"phản hồi không phải JSON-RPC hợp lệ: {exc}")
                raise PaosError(
                    ErrorCode.PROVIDER_DOWN,
                    f"Plugin {self._plugin_id} trả phản hồi hỏng cho '{method}'",
                    retryable=True,
                    hint="Plugin phải in ĐÚNG 1 dòng JSON-RPC ra stdout mỗi lượt gọi (ADR-0032)",
                    context={"plugin_id": self._plugin_id, "method": method},
                ) from exc

            if rpc_error is not None:
                try:
                    error_code = ErrorCode(rpc_error.paos_code)
                except ValueError:
                    error_code = ErrorCode.INTERNAL
                raise PaosError(
                    error_code,
                    rpc_error.message,
                    retryable=rpc_error.retryable,
                    hint=rpc_error.hint or "Xem plugin.yaml/README của plugin",
                    context={**rpc_error.context, "plugin_id": self._plugin_id},
                )
            if result is None:
                raise PaosError(
                    ErrorCode.INTERNAL,
                    f"Plugin {self._plugin_id} trả 'result' rỗng cho '{method}'",
                    hint="Lỗi nội bộ — báo lại nếu gặp",
                    context={"plugin_id": self._plugin_id, "method": method},
                )
            return result

    async def _maybe_restart_if_expired(self) -> None:
        if (
            self.is_running
            and self._wall_sec is not None
            and self._started_at is not None
            and time.monotonic() - self._started_at > self._wall_sec
        ):
            await self._handle_crash(f"vượt wall_sec={self._wall_sec:.0f}s, chủ động dừng")

    async def _handle_crash(self, detail: str) -> None:
        await self.stop(force=True)
        if self._on_crash is not None:
            await self._on_crash(self._plugin_id, detail)
        self._restart_count += 1
        if self._restart_count > _MAX_RESTARTS:
            self._permanently_dead = True
            self._dead_reason = detail

    async def stop(self, *, force: bool = False) -> None:
        if self._process is None:
            return
        if self._process.returncode is None:
            if force:
                self._process.kill()
            else:
                self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._process = None
        self._started_at = None

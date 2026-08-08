"""Tiêu chí bổ sung #5 của M0 (doc 18 §7.1): `providers/stub/` phải cho cùng
kết quả cấu trúc — và ở đây còn mạnh hơn, cùng NỘI DUNG byte-for-byte — sau
nhiều lần chạy liên tiếp cùng input.

Đặt ở `tests/apps/` chứ không phải `tests/kernel/` như doc 18 §7.2 gợi ý ban
đầu: bài kiểm này chạy đủ Process + Agent + Provider (qua `apps/paosd/wiring`),
không phải Kernel đơn thuần — gate2 (`tests/kernel/`) xoá `apps/` trước khi
chạy nên đặt ở đó sẽ vỡ ngay. Ghi lại như một điều chỉnh so với kế hoạch gốc
ở báo cáo nghiệm thu M0 (P-M0-6 mục 5).
"""

from pathlib import Path

import aiosqlite
import pytest

from apps.paosd.wiring import build_daemon

_RUNS = 20
_TEXT = "PAOS là một hệ điều hành AI cá nhân chạy local-first."
_EXPECTED_EVENT_TYPES = (
    "kernel.process.created",
    "kernel.process.queued",
    "kernel.process.started",
    "summary.created",
    "kernel.process.completed",
)


async def _run_once(tmp_path: Path, i: int) -> tuple[str, tuple[str, ...], str]:
    """Chạy một job đầy đủ, trả về (state cuối, chuỗi loại event, sha256 artifact)."""
    daemon = await build_daemon(
        tmp_path / f"run-{i}" / ".paos" / "state.db",
        workspace_root=tmp_path / f"run-{i}" / "workspace",
    )
    try:
        process = await daemon.manager.create(
            intent="summarize",
            spec={"text": _TEXT},
            name="determinism-check",
            workflow_ref="agent:summarize.agent@1",
        )
        # create() dispatch event ĐỒNG BỘ (apps/paosd/runner.py) — process đã
        # chạy xong khi await create() ở trên trả về, không cần chờ thêm.
        final = await daemon.manager.get(process.process_id)
        assert final is not None
        trace = await daemon.events.events_for_process(process.process_id)

        async def _read_sha(conn: aiosqlite.Connection) -> str:
            cursor = await conn.execute(
                "SELECT sha256 FROM artifacts WHERE process_id = ?", (process.process_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            sha: str = row[0]
            return sha

        sha256 = await daemon.store.read(_read_sha)
        return final.state.value, tuple(e.type for e in trace), sha256
    finally:
        await daemon.stop()


@pytest.mark.slow
async def test_stub_provider_deterministic_across_20_runs(tmp_path: Path) -> None:
    """Mạnh hơn yêu cầu "cùng cấu trúc" của doc 18 — StubAdapter tất định thuần
    theo input (`providers/stub/adapter.py::_text_generate`), nên artifact phải
    giống hệt byte-for-byte (sha256), không chỉ giống hình dạng."""
    results = [await _run_once(tmp_path, i) for i in range(_RUNS)]

    states = {state for state, _, _ in results}
    assert states == {"SUCCEEDED"}

    event_type_sequences = {event_types for _, event_types, _ in results}
    assert event_type_sequences == {_EXPECTED_EVENT_TYPES}, (
        "Chuỗi loại event phải giống hệt nhau ở cả 20 lần chạy"
    )

    shas = {sha for _, _, sha in results}
    assert len(shas) == 1, f"sha256 artifact phải giống hệt ở mọi lần chạy, thấy: {shas}"

"""Kiểm StateStore — migration, backup, integrity check, single-writer actor (doc 18 §6 nhóm A)."""

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from kernel import clock
from kernel.errors import ErrorCode, PaosError
from kernel.state import db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / ".paos" / "state.db"


async def _table_names(store: db.StateStore) -> set[str]:
    async def _query(conn: aiosqlite.Connection) -> set[str]:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in await cursor.fetchall()}

    return await store.read(_query)


async def test_migration_runs_on_first_start(db_path: Path) -> None:
    store = db.StateStore(db_path)
    await store.start()
    try:
        tables = await _table_names(store)
        expected = {
            "schema_migrations",
            "counters",
            "jobs",
            "processes",
            "process_transitions",
            "tasks",
            "checkpoints",
            "events",
            "event_deliveries",
            "artifacts",
        }
        assert expected <= tables
    finally:
        await store.stop()


async def test_migration_is_idempotent(db_path: Path) -> None:
    n_migration_files = len(list(db._MIGRATIONS_DIR.glob("*.sql")))

    store1 = db.StateStore(db_path)
    await store1.start()
    await store1.stop()

    store2 = db.StateStore(db_path)
    await store2.start()  # không được lỗi, không áp lại migration đã áp
    try:

        async def _count(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute("SELECT COUNT(*) FROM schema_migrations")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

        # So với số file migration thật, không hardcode — tránh vỡ mỗi khi thêm migration mới.
        assert await store2.read(_count) == n_migration_files
    finally:
        await store2.stop()


async def test_backup_created_before_second_migration(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_dir = db_path.parent / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_init.sql").write_text("CREATE TABLE t(x INTEGER);", encoding="utf-8")
    monkeypatch.setattr(db, "_MIGRATIONS_DIR", migrations_dir)

    store1 = db.StateStore(db_path)
    await store1.start()
    await store1.stop()

    (migrations_dir / "002_add_col.sql").write_text(
        "ALTER TABLE t ADD COLUMN y TEXT;", encoding="utf-8"
    )
    store2 = db.StateStore(db_path)
    await store2.start()
    await store2.stop()

    backups = list((db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1


async def test_integrity_check_raises_on_corrupt_db(db_path: Path) -> None:
    """Phá HẲN toàn bộ nội dung sau trang 1 (4096 byte đầu — header + sqlite_master),
    không phá 64 byte ở "chính giữa file": kích thước file đổi theo số migration đã
    áp (P-M2-4 thêm bảng cache_entries khiến "chính giữa" cũ rơi vào vùng chưa dùng,
    không hỏng gì thật — test từng "pass" mà không kiểm được gì). Phá rộng đảm bảo
    trúng dữ liệu thật bất kể có bao nhiêu bảng, dù kết quả là mở file thất bại hẳn
    (sqlite3.DatabaseError, StateStore.start() bọc lại) hay mở được nhưng
    integrity_check phát hiện — cả 2 đường đều phải ra PaosError(INTERNAL)."""
    store = db.StateStore(db_path)
    await store.start()
    await store.stop()

    data = bytearray(db_path.read_bytes())
    page_size = 4096
    data[page_size:] = b"\xff" * (len(data) - page_size)
    db_path.write_bytes(bytes(data))

    store2 = db.StateStore(db_path)
    with pytest.raises(PaosError) as exc_info:
        await store2.start()
    assert exc_info.value.code == ErrorCode.INTERNAL


async def test_concurrent_writes_are_serialized(db_path: Path) -> None:
    store = db.StateStore(db_path)
    await store.start()
    try:

        def _insert_job(n: int):
            async def _fn(conn: aiosqlite.Connection) -> None:
                await conn.execute(
                    "INSERT INTO jobs(job_id, intent, spec_json, created_at, schema_version) "
                    "VALUES (?, 'test', '{}', ?, 1)",
                    (f"job_{n}", clock.now().isoformat()),
                )

            return _fn

        await asyncio.gather(*(store.write(_insert_job(n)) for n in range(20)))

        async def _count(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute("SELECT COUNT(*) FROM jobs")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

        assert await store.read(_count) == 20
    finally:
        await store.stop()


async def test_pid_assignment_is_atomic_and_sequential(db_path: Path) -> None:
    store = db.StateStore(db_path)
    await store.start()
    try:
        pids = await asyncio.gather(*(store.next_pid() for _ in range(20)))
        assert len(set(pids)) == 20
        assert sorted(pids) == list(range(1001, 1021))
    finally:
        await store.stop()


async def test_conflicting_migration_raises_conflict(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_dir = db_path.parent / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_init.sql").write_text("CREATE TABLE t(x INTEGER);", encoding="utf-8")
    monkeypatch.setattr(db, "_MIGRATIONS_DIR", migrations_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    blocker = await aiosqlite.connect(db_path, isolation_level=None)
    await blocker.execute("PRAGMA journal_mode=WAL")
    await blocker.execute("BEGIN IMMEDIATE")
    await blocker.execute("CREATE TABLE dummy(x INTEGER)")
    try:
        store = db.StateStore(db_path)
        with pytest.raises(PaosError) as exc_info:
            await store.start()
        assert exc_info.value.code == ErrorCode.CONFLICT
    finally:
        await blocker.execute("ROLLBACK")
        await blocker.close()

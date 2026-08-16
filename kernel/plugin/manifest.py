"""PluginManifest — đọc + validate `plugin.yaml` (doc 04 §5, hợp đồng dài hạn;
doc 09 §5 sandbox; doc 19 P-M8-1, ADR-0018/ADR-0032).

Cùng khuôn `kernel/registry/registry.py::load_provider_manifest()`: DAO thuần,
đọc file + validate qua `jsonschema` (đã dùng cho Capability, `schemas/
plugin.schema.json` mới thêm ở lát này), không side-effect. `paos_api_compatible()`
tự viết so semver TỐI GIẢN (ADR-0018 §Lý do 3) — KHÔNG thêm dependency
`packaging`/`semver`, chỉ cần so `major.minor.patch` với range dạng
`">=X.Y,<A.B"` (2 mệnh đề nối bằng dấu phẩy, đúng ví dụ DUY NHẤT doc 04 §5 có)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import yaml

from kernel.errors import ErrorCode, PaosError

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "plugin.schema.json"

# doc 04 §6 "Kernel API | prefix /v1" — paos_api mô tả BỀ MẶT API (/v1), KHÔNG
# phải version gói `pyproject.toml` (còn ở 0.0.1 pre-release). Tăng số này khi
# và chỉ khi API /v1 có breaking change thật (đúng bảng tương thích doc 04 §6).
PAOS_API_VERSION = "1.0.0"

_CLAUSE_RE = re.compile(r"^(>=|<=|>|<|==)\s*(\d+)\.(\d+)(?:\.(\d+))?$")


@dataclass(frozen=True)
class PluginProvides:
    agents: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    rubrics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PluginPermissions:
    fs_read: list[str] = field(default_factory=list)
    fs_write: list[str] = field(default_factory=list)
    network_allow: list[str] = field(default_factory=list)
    exec_allow: list[str] = field(default_factory=list)
    spend_max_per_job: float | None = None
    spend_currency: str | None = None


@dataclass(frozen=True)
class PluginResourceLimits:
    cpu_sec: float | None = None
    rss_mb: float | None = None
    wall_sec: float | None = None
    open_files: int | None = None


@dataclass(frozen=True)
class PluginRuntime:
    entry: str  # "python -m paos_video.main" — PluginProcess tự shlex.split()
    limits: PluginResourceLimits


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    paos_api_range: str
    provides: PluginProvides
    permissions: PluginPermissions
    runtime: PluginRuntime
    plugin_dir: Path  # thư mục chứa plugin.yaml — PluginProcess cần làm cwd


def load_plugin_manifest(plugin_dir: Path) -> PluginManifest:
    """Đọc `<plugin_dir>/plugin.yaml`, validate đúng `schemas/plugin.schema.json`
    (cùng cơ chế Capability đã validate input/output qua jsonschema, không tự
    viết validator tay). Raise `PaosError` rõ lý do nếu thiếu file/sai schema —
    KHÔNG âm thầm bỏ qua plugin lỗi (khác `_scan_providers()` vốn chỉ bỏ qua
    thư mục THIẾU FILE, ở đây file CÓ nhưng SAI NỘI DUNG là lỗi cấu hình thật,
    cần báo ngay lúc `Registry.load()`, không phải lúc dùng)."""
    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.is_file():
        raise PaosError(
            ErrorCode.NOT_FOUND,
            f"Thiếu plugin.yaml trong {plugin_dir}",
            hint="Mỗi plugin cần đúng 1 file plugin.yaml ở thư mục gốc (doc 04 §5)",
            context={"plugin_dir": str(plugin_dir)},
        )
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise PaosError(
            ErrorCode.INVALID_INPUT,
            f"plugin.yaml ở {plugin_dir} không khớp schema: {exc.message}",
            hint="Kiểm theo schemas/plugin.schema.json — doc 04 §5",
            context={"plugin_dir": str(plugin_dir)},
        ) from exc

    provides_raw = data.get("provides") or {}
    perms_raw = data.get("permissions") or {}
    fs_raw = perms_raw.get("fs") or {}
    network_raw = perms_raw.get("network") or {}
    spend_raw = perms_raw.get("spend") or {}
    runtime_raw = data["runtime"]
    limits_raw = runtime_raw.get("limits") or {}

    return PluginManifest(
        plugin_id=data["id"],
        name=data["name"],
        version=data["version"],
        paos_api_range=data["paos_api"],
        provides=PluginProvides(
            agents=list(provides_raw.get("agents", [])),
            workflows=list(provides_raw.get("workflows", [])),
            capabilities=list(provides_raw.get("capabilities", [])),
            providers=list(provides_raw.get("providers", [])),
            rubrics=list(provides_raw.get("rubrics", [])),
        ),
        permissions=PluginPermissions(
            fs_read=list(fs_raw.get("read", [])),
            fs_write=list(fs_raw.get("write", [])),
            network_allow=list(network_raw.get("allow", [])),
            exec_allow=list(perms_raw.get("exec", [])),
            spend_max_per_job=spend_raw.get("max_per_job"),
            spend_currency=spend_raw.get("currency"),
        ),
        runtime=PluginRuntime(
            entry=runtime_raw["entry"],
            limits=PluginResourceLimits(
                cpu_sec=limits_raw.get("cpu_sec"),
                rss_mb=limits_raw.get("rss_mb"),
                wall_sec=limits_raw.get("wall_sec"),
                open_files=limits_raw.get("open_files"),
            ),
        ),
        plugin_dir=plugin_dir,
    )


def _parse_version(version: str) -> tuple[int, int, int]:
    major, _, rest = version.partition(".")
    minor, _, patch = rest.partition(".")
    return int(major), int(minor or 0), int(patch or 0)


def _clause_satisfied(current: tuple[int, int, int], op: str, bound: tuple[int, int, int]) -> bool:
    if op == ">=":
        return current >= bound
    if op == "<=":
        return current <= bound
    if op == ">":
        return current > bound
    if op == "<":
        return current < bound
    return current == bound  # "=="


def paos_api_compatible(paos_api_range: str, current: str = PAOS_API_VERSION) -> bool:
    """So `current` (mặc định `PAOS_API_VERSION`) với 1-2 mệnh đề nối dấu phẩy
    (`">=1.0,<2.0"`, đúng ví dụ doc 04 §5) — KHÔNG hỗ trợ pre-release/build
    metadata/wildcard đầy đủ spec PEP 440 (ADR-0018: chưa 2 ca dùng thật cho
    phần đó, `pyproject.toml` chưa thêm `packaging` vì lý do này)."""
    current_tuple = _parse_version(current)
    for raw_clause in paos_api_range.split(","):
        clause = raw_clause.strip()
        match = _CLAUSE_RE.match(clause)
        if match is None:
            raise PaosError(
                ErrorCode.INVALID_INPUT,
                f"paos_api range không hợp lệ: {clause!r}",
                hint='Dùng dạng ">=X.Y" hoặc "<X.Y", nối nhiều mệnh đề bằng dấu phẩy',
                context={"clause": clause},
            )
        op, major, minor, patch = match.groups()
        bound = (int(major), int(minor), int(patch or 0))
        if not _clause_satisfied(current_tuple, op, bound):
            return False
    return True

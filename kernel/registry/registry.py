"""Registry — nạp Capability + Provider + Agent từ file lúc khởi động (doc 02
§3.5, ADR-0004).

Kernel chỉ biết capability_id/version/agent_id qua đây. Không hằng số tên
provider/agent nào trong kernel/ — mọi thứ đọc từ file, hoàn toàn chung (P1/P3).

`load_adapter()` (P-M2-1) nạp ĐỘNG instance adapter qua `importlib` theo
`provider.yaml::adapter` (dạng `module.path:ClassName`) — Kernel không bao
giờ có `import providers.xxx` tĩnh nào (đúng doc 02 §3.5 "Load lúc khởi động").
`load_agent()` (P-M3-4, trả nợ BL-006) nạp ĐỘNG cùng cơ chế cho `agents/<x>/
manifest.yaml::entry` — trước lát này `apps/paosd/runner.py` có 1 dict tĩnh
`_AGENTS` import trực tiếp từng agent, vi phạm đúng "thêm agent không cần sửa
code" mà `load_adapter()` đã đạt được cho provider từ M2-1. KHÁC `load_adapter()`:
`load_agent()` KHÔNG cache instance dùng chung (trả nợ BL-007) — Agent có state
riêng-theo-lượt-gọi trên `self` (ProviderAdapter thì không), nên chỉ cache CLASS
đã `importlib.import_module()` rồi tự khởi tạo instance MỚI mỗi lần gọi. Cả hai
đều trả về `Any`, KHÔNG import `sdk.provider.ProviderAdapter`/`sdk.agent.Agent`
để gõ kiểu — Kernel không được phép biết tới `sdk/` (MNT-06); caller (`apps/`,
được phép import cả 2 tầng) tự chịu trách nhiệm instance khớp Protocol."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from kernel.errors import ErrorCode, PaosError
from kernel.workflow.spec import WorkflowSpec, parse_workflow_spec


@dataclass(frozen=True)
class CapabilitySpec:
    """id/version nằm trong đường dẫn thư mục nạp ra nó, không lặp lại trong file."""

    capability_id: str
    version: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: list[str]
    # cache.json TÙY CHỌN (doc 03 §5.2, P-M2-4) — thiếu file = mặc định KHÔNG cache
    # (an toàn, không cache khi chưa khai báo rõ field nào tham gia key).
    cacheable: bool = False
    cache_key_fields: list[str] = field(default_factory=list)

    def validate_input(self, payload: dict[str, Any]) -> None:
        self._validate(payload, self.input_schema, "input")

    def validate_output(self, payload: dict[str, Any]) -> None:
        self._validate(payload, self.output_schema, "output")

    def _validate(self, payload: dict[str, Any], schema: dict[str, Any], kind: str) -> None:
        try:
            jsonschema.validate(payload, schema)
        except jsonschema.ValidationError as exc:
            raise PaosError(
                ErrorCode.INVALID_INPUT,
                f"{kind} của {self.capability_id}@{self.version} không khớp schema: {exc.message}",
                hint=f"Kiểm payload theo capabilities/{self.capability_id}/"
                f"{self.version}/{kind}.schema.json",
                context={
                    "capability_id": self.capability_id,
                    "version": self.version,
                    "kind": kind,
                },
            ) from exc


@dataclass(frozen=True)
class ProviderManifest:
    """doc 02 §5 — khai báo của một Provider Adapter."""

    provider_id: str
    implements: list[str]  # ["text.generate@1", ...]
    provider_class: str  # local | cloud | hybrid
    privacy: str  # private | shared | public
    cost: dict[str, Any]
    limits: dict[str, Any]
    resources: list[str]
    health_check: dict[str, Any]
    quality_hint: dict[str, Any]
    adapter: str  # "module.path:ClassName" — Registry.load_adapter() nạp động (P-M2-1)
    enabled: bool = True  # false = tắt thủ công, Router loại khỏi ứng viên (doc 06 §2.1, P-M2-3)

    def implements_capability(self, capability_id: str, version: int) -> bool:
        return f"{capability_id}@{version}" in self.implements


def _capability_key(capability_id: str, version: int) -> str:
    return f"{capability_id}@{version}"


@dataclass(frozen=True)
class _AgentEntry:
    """Kết quả quét 1 `agents/<x>/manifest.yaml` — chỉ 2 thứ Registry cần để
    nạp động: đường dẫn class (`entry:`) và thư mục phụ trợ kế bên manifest
    (apps/ tự biết cách dùng thư mục này — Kernel chỉ giữ đường dẫn, không
    biết/không cần biết bên trong là gì, cổng 1 cấm từ khoá tầng AI ở đây).
    KHÔNG phải `sdk.agent.AgentManifest` đầy đủ (Kernel không được import sdk/)."""

    entry: str  # "module.path:ClassName" — giống ProviderManifest.adapter
    extra_dir: Path


def load_provider_manifest(path: Path) -> ProviderManifest:
    """Đọc 1 file provider.yaml (doc 02 §5). Dùng chung bởi Registry._scan_providers()
    và bởi chính Provider Adapter khi tự nạp manifest của mình lúc import (vd StubAdapter)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ProviderManifest(
        provider_id=data["id"],
        implements=data.get("implements", []),
        provider_class=data.get("class", "local"),
        privacy=data.get("privacy", "private"),
        cost=data.get("cost", {}),
        limits=data.get("limits", {}),
        resources=data.get("resources", []),
        health_check=data.get("health_check", {}),
        quality_hint=data.get("quality_hint", {}),
        adapter=data.get("adapter", ""),
        enabled=data.get("enabled", True),
    )


class Registry:
    def __init__(
        self,
        capabilities_dir: Path,
        providers_dir: Path,
        workflows_dir: Path | None = None,
        agents_dir: Path | None = None,
        rubrics_dir: Path | None = None,
    ) -> None:
        """`workflows_dir`/`agents_dir`/`rubrics_dir` tùy chọn (mặc định None =
        chưa cấu hình) — thêm sau capabilities/providers (P-M3-2, P-M3-4, P-M4-2),
        giữ optional để không phá vỡ 20+ chỗ gọi `Registry(caps_dir, providers_dir)`
        đã có từ M0-M2. `rubrics_dir` KHÔNG quét lúc `load()` như capabilities/
        providers/agents — rubric YAML nạp LAZY qua `rubric_path()` (giống
        `get_workflow()`, doc 19 P-M3-2: số lượng rubric nhỏ, parse rẻ) và
        Registry chỉ trả về `Path`, KHÔNG parse (đó là `sdk.rubric.load_rubric()`
        — Kernel không được import `sdk/`, xem MNT-06)."""
        self._capabilities_dir = capabilities_dir
        self._providers_dir = providers_dir
        self._workflows_dir = workflows_dir
        self._agents_dir = agents_dir
        self._rubrics_dir = rubrics_dir
        self._capabilities: dict[str, CapabilitySpec] = {}
        self._providers: list[ProviderManifest] = []
        self._providers_by_id: dict[str, ProviderManifest] = {}
        self._adapter_cache: dict[str, Any] = {}
        self._agents: dict[str, _AgentEntry] = {}
        self._agent_preload: dict[str, Any] = {}
        self._agent_class_cache: dict[str, type] = {}

    def load(self) -> None:
        """Quét capabilities/, providers/, agents/, nạp toàn bộ vào bộ nhớ. Gọi 1 lần lúc
        khởi động (chưa hot-reload — đó là M8 khi có Event `plugin.installed`, doc 02 §3.5)."""
        self._capabilities = dict(self._scan_capabilities())
        self._providers = list(self._scan_providers())
        self._providers_by_id = {p.provider_id: p for p in self._providers}
        self._agents = dict(self._scan_agents())

    def get_capability(self, capability_id: str, version: int) -> CapabilitySpec:
        key = _capability_key(capability_id, version)
        spec = self._capabilities.get(key)
        if spec is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Capability {key} chưa đăng ký",
                hint=f"Kiểm thư mục capabilities/{capability_id}/{version}/ có tồn tại không",
                context={"capability_id": capability_id, "version": version},
            )
        return spec

    def list_capabilities(self) -> list[CapabilitySpec]:
        return list(self._capabilities.values())

    def get_workflow(self, workflow_id: str, version: int) -> WorkflowSpec:
        """Đọc + parse `workflows/<id>/<version>/workflow.yaml` — LAZY, không
        cache (khác capabilities/providers): số lượng workflow nhỏ, parse rẻ,
        và mỗi Process chỉ đọc 1 lần lúc PLANNING (P-M3-2). Thêm cache nếu đo
        được đây là nút thắt thật (P4 — chưa có bằng chứng cần)."""
        if self._workflows_dir is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Registry chưa cấu hình workflows_dir — không tìm được workflow "
                f"{workflow_id}@{version}",
                hint="Truyền workflows_dir cho Registry(...) nếu Process này cần chạy "
                "workflow YAML",
                context={"workflow_id": workflow_id, "version": version},
            )
        path = self._workflows_dir / workflow_id / str(version) / "workflow.yaml"
        if not path.is_file():
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Workflow {workflow_id}@{version} chưa đăng ký",
                hint=f"Kiểm file workflows/{workflow_id}/{version}/workflow.yaml có tồn tại không",
                context={"workflow_id": workflow_id, "version": version},
            )
        return parse_workflow_spec(path.read_text(encoding="utf-8"))

    def rubric_path(self, rubric_id: str, version: int) -> Path:
        """Trả về đường dẫn `rubrics/<id>.v<version>.yaml` (doc 08 §2 ví dụ
        đường dẫn) — CHỈ trả `Path`, không parse (parse là `sdk.rubric.load_rubric()`,
        Kernel không được import `sdk/`, xem docstring `__init__`). LAZY, không
        cache — cùng lý do `get_workflow()`."""
        if self._rubrics_dir is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Registry chưa cấu hình rubrics_dir — không tìm được rubric {rubric_id}@{version}",
                hint="Truyền rubrics_dir cho Registry(...) nếu Process này cần chấm điểm rubric",
                context={"rubric_id": rubric_id, "version": version},
            )
        path = self._rubrics_dir / f"{rubric_id}.v{version}.yaml"
        if not path.is_file():
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Rubric {rubric_id}@{version} chưa đăng ký",
                hint=f"Kiểm file rubrics/{rubric_id}.v{version}.yaml có tồn tại không",
                context={"rubric_id": rubric_id, "version": version},
            )
        return path

    def providers_for(self, capability_id: str, version: int) -> list[ProviderManifest]:
        return [p for p in self._providers if p.implements_capability(capability_id, version)]

    def preload_adapter(self, provider_id: str, adapter: Any) -> None:
        """Đặt sẵn 1 instance adapter cho `provider_id`, bỏ qua nạp động —
        dùng cho test (thay adapter thật bằng adapter giả điều khiển được).
        Production code không cần gọi hàm này, `load_adapter()` tự nạp động."""
        self._adapter_cache[provider_id] = adapter

    def load_adapter(self, provider_id: str) -> Any:
        """Nạp động instance adapter theo `provider.yaml::adapter` (P-M2-1,
        exit criteria doc 13 M2: "provider mới chỉ cần 1 file adapter + 1
        YAML"). Cache theo provider_id — mỗi provider chỉ khởi tạo 1 lần."""
        if provider_id in self._adapter_cache:
            return self._adapter_cache[provider_id]

        manifest = self._providers_by_id.get(provider_id)
        if manifest is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Provider {provider_id} chưa đăng ký",
                hint="Kiểm thư mục providers/ có provider.yaml khai báo id này không",
                context={"provider_id": provider_id},
            )
        if not manifest.adapter:
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Provider {provider_id} không khai báo 'adapter' trong provider.yaml",
                hint="Thêm dòng adapter: module.path:ClassName vào provider.yaml",
                context={"provider_id": provider_id},
            )

        module_path, _, class_name = manifest.adapter.partition(":")
        try:
            module = importlib.import_module(module_path)
            adapter_cls = getattr(module, class_name)
            instance = adapter_cls()
        except (ImportError, AttributeError) as exc:
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Không nạp được adapter '{manifest.adapter}' cho provider {provider_id}: {exc}",
                hint="Kiểm giá trị 'adapter' trong provider.yaml đúng dạng module.path:ClassName",
                context={"provider_id": provider_id, "adapter": manifest.adapter},
            ) from exc

        self._adapter_cache[provider_id] = instance
        return instance

    def preload_agent(self, agent_id: str, version: int, instance: Any, extra_dir: Path) -> None:
        """Đặt sẵn 1 instance Agent CỐ ĐỊNH + thư mục phụ trợ, bỏ qua nạp động —
        dùng cho test (thay agent thật bằng agent giả điều khiển được, test tự
        giữ tham chiếu instance để assert lên state của nó sau khi chạy), cùng
        vai trò `preload_adapter()`. Production code không cần gọi hàm này.
        KHÁC `load_agent()` (trả nợ BL-007): đây là instance DÙNG CHUNG có chủ
        đích — test chỉ gọi 1 lần, không có 2 Process song song cùng dùng chung
        agent giả này."""
        key = _capability_key(agent_id, version)
        self._agent_preload[key] = instance
        self._agents.setdefault(key, _AgentEntry(entry="", extra_dir=extra_dir))

    def load_agent(self, agent_id: str, version: int) -> Any:
        """Nạp động 1 instance Agent MỚI theo `agents/<x>/manifest.yaml::entry`
        (P-M3-4, trả nợ BL-006) mỗi lần được gọi — trả nợ BL-007 (docs/backlog.md):
        Agent gán state riêng-theo-lượt lên `self` (vd `self._ctx`), KHÁC
        `ProviderAdapter` (không có state riêng theo lượt gọi, `invoke()` nhận
        đủ dữ liệu qua tham số) nên `load_adapter()` cache instance dùng chung
        được nhưng `load_agent()` thì không — cache CLASS (nạp `importlib` 1
        lần) rồi tự `agent_cls()` instance mới mỗi lần gọi, tránh 2 Process chạy
        song song (`Runner.worker_loop()`, `asyncio.Semaphore(max_parallel)`)
        cùng agent_id@version chia sẻ state và ghi đè lẫn nhau giữa chừng."""
        key = _capability_key(agent_id, version)
        if key in self._agent_preload:
            return self._agent_preload[key]

        entry_info = self._agents.get(key)
        if entry_info is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Agent {key} chưa đăng ký",
                hint="Kiểm thư mục agents/ có manifest.yaml khai báo id/version này không",
                context={"agent_id": agent_id, "version": version},
            )
        if not entry_info.entry:
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Agent {key} không khai báo 'entry' trong manifest.yaml",
                hint="Thêm dòng entry: module.path:ClassName vào manifest.yaml",
                context={"agent_id": agent_id, "version": version},
            )

        agent_cls = self._agent_class_cache.get(key)
        if agent_cls is None:
            module_path, _, class_name = entry_info.entry.partition(":")
            try:
                module = importlib.import_module(module_path)
                agent_cls = getattr(module, class_name)
            except (ImportError, AttributeError) as exc:
                raise PaosError(
                    ErrorCode.INTERNAL,
                    f"Không nạp được agent '{entry_info.entry}' cho {key}: {exc}",
                    hint="Kiểm giá trị 'entry' trong manifest.yaml đúng dạng module.path:ClassName",
                    context={
                        "agent_id": agent_id,
                        "version": version,
                        "entry": entry_info.entry,
                    },
                ) from exc
            self._agent_class_cache[key] = agent_cls

        return agent_cls()

    def agent_extra_dir(self, agent_id: str, version: int) -> Path:
        """Thư mục `agents/<x>/` chứa `manifest.yaml` — TRẢ VỀ NGUYÊN THƯ MỤC
        GỐC, không tự nối thêm thư mục con nào (Kernel không biết/không cần
        biết agent giữ gì bên trong, cổng 1 cấm từ vựng tầng AI ở kernel/).
        Caller (`apps/`, được phép biết) tự nối đường dẫn con cần dùng — vd
        Runner cần mẫu soạn sẵn theo version thì tự ghép `agent_extra_dir(...)
        / "<tên-thư-mục-con>"`. Tách khỏi `load_agent()` vì instance Agent
        không tự biết đường dẫn thư mục của chính nó (P-M3-4)."""
        key = _capability_key(agent_id, version)
        entry_info = self._agents.get(key)
        if entry_info is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Agent {key} chưa đăng ký",
                hint="Kiểm thư mục agents/ có manifest.yaml khai báo id/version này không",
                context={"agent_id": agent_id, "version": version},
            )
        return entry_info.extra_dir

    def _scan_capabilities(self) -> dict[str, CapabilitySpec]:
        result: dict[str, CapabilitySpec] = {}
        if not self._capabilities_dir.exists():
            return result
        for cap_dir in self._capabilities_dir.iterdir():
            if not cap_dir.is_dir():
                continue
            for version_dir in cap_dir.iterdir():
                if not version_dir.is_dir() or not version_dir.name.isdigit():
                    continue
                spec = self._load_capability(cap_dir.name, int(version_dir.name), version_dir)
                result[_capability_key(spec.capability_id, spec.version)] = spec
        return result

    def _load_capability(
        self, capability_id: str, version: int, version_dir: Path
    ) -> CapabilitySpec:
        cache_meta = self._read_optional_json(version_dir / "cache.json") or {}
        return CapabilitySpec(
            capability_id=capability_id,
            version=version,
            input_schema=self._read_json(version_dir / "input.schema.json"),
            output_schema=self._read_json(version_dir / "output.schema.json"),
            errors=self._read_json(version_dir / "errors.json"),
            cacheable=cache_meta.get("cacheable", False),
            cache_key_fields=cache_meta.get("key_fields", []),
        )

    def _scan_providers(self) -> list[ProviderManifest]:
        result: list[ProviderManifest] = []
        if not self._providers_dir.exists():
            return result
        for provider_dir in self._providers_dir.iterdir():
            manifest_path = provider_dir / "provider.yaml"
            if not manifest_path.is_file():
                continue
            result.append(load_provider_manifest(manifest_path))
        return result

    def _scan_agents(self) -> dict[str, _AgentEntry]:
        result: dict[str, _AgentEntry] = {}
        if self._agents_dir is None or not self._agents_dir.exists():
            return result
        for agent_dir in self._agents_dir.iterdir():
            manifest_path = agent_dir / "manifest.yaml"
            if not manifest_path.is_file():
                continue
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            key = _capability_key(data["id"], int(data["version"]))
            result[key] = _AgentEntry(entry=data.get("entry", ""), extra_dir=agent_dir)
        return result

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.is_file():
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Thiếu file bắt buộc: {path}",
                hint="Mỗi capability cần đủ input.schema.json, output.schema.json, errors.json",
                context={"path": str(path)},
            )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_optional_json(path: Path) -> Any:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

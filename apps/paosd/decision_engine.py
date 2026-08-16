"""DecisionEngine — chọn Workflow từ JobSpec: đặc trưng hóa, ứng viên, chấm điểm
(doc 06 §1.1 bước 1-3, doc 19 P-M6-1).

Ở apps/, KHÔNG ở kernel/ — cùng lý do Router (apps/paosd/router.py, M2-3): đây
là logic nghiệp vụ "chọn cái gì cho Job này", Kernel chỉ biết Process/Task
chung chung (P1/P3). Layering giống hệt Router: Registry để tra workflow,
StateStore để ghi Decision Record + đọc decision_outcomes, EventBus để phát
workflow.selected.

Tách 2 pha `choose_workflow()` / `record_selection()` thay vì 1 hàm duy nhất:
Decision Record cần `process_id` THẬT (doc 03 §2.5), nhưng `ProcessManager.
create()` (Kernel API, hợp đồng dài hạn — không đụng ở lát này) chỉ sinh
process_id SAU KHI nhận `workflow_ref`. Vậy chấm điểm (bước 1-3, không cần
process_id) phải xảy ra TRƯỚC `manager.create()`; ghi Decision Record + phát
event (bước 5) xảy ra SAU, khi process_id đã có — xem `apps/paosd/app.py::
create_job()`.

CHƯA làm ở lát này (ghi chú, không giả vờ làm bằng giá trị hardcode — cùng tiền
lệ Router docstring):
- has_text_layer/image_count/language/duration_hint/hardware_state (doc 06 liệt
  kê trong đặc trưng lý tưởng) — cần OCR/tokenizer/Energy Engine chưa tồn tại.
- Hard rule "input JobSpec khớp input WorkflowSpec khai báo" — 2 khái niệm khác
  hình dạng hôm nay (JobSpec.inputs là DANH SÁCH tệp đính kèm thô, WorkflowSpec.
  inputs là DICT tham số có tên từng bước dùng qua ${inputs.x}) và CHƯA có cơ
  chế resolve giữa 2 bên — bịa một quy tắc khớp tên sẽ là quy tắc giả, không
  phải quy tắc thật.
- LLM tie-break (doc 06 §1.1 bước 3c, "chỉ khi 2 ứng viên chênh < 5%") — cần
  quyết định hạ tầng gọi capability nào, đồng bộ hay không, tốn tiền ra sao;
  ngoài phạm vi lát "feature extraction + candidate + scoring" thuần tất định.
  Luật + thống kê (bước 3a/3b) đã đúng tinh thần "quyết định 90% trường hợp".
- Bước 4 (Parameterization/Preference từ Memory) — `preferences_snapshot` đã
  được caller chụp sẵn lúc tạo JobSpec (doc 03 §2.1), Decision Engine không
  cần đụng vào.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import yaml

from kernel import clock, ids
from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.redact import redact
from kernel.registry.registry import Registry
from kernel.state.db import StateStore

_SCOPE = "workflow_selection"
_POLICY_VERSION = "intents@1"


@dataclass(frozen=True)
class JobFeatures:
    """Đặc trưng THUẦN TẤT ĐỊNH derive từ JobSpec — không LLM, không đọc file
    (doc 06 §1.1 bước 1). `spec` là dict lưu ở `jobs.spec_json` (doc 03 §2.1
    trừ intent/priority — 2 field đó đã có cột riêng)."""

    intent: str
    input_type: str  # "none" nếu spec["inputs"] rỗng, ngược lại type của input đầu tiên
    mime: str | None
    privacy: str | None
    budget_max: float | None
    offline_only: bool
    deadline: str | None


def extract_features(intent: str, spec: dict[str, Any]) -> JobFeatures:
    inputs = spec.get("inputs") or []
    constraints = spec.get("constraints") or {}
    first_input = inputs[0] if inputs else None
    budget = constraints.get("budget") or {}
    return JobFeatures(
        intent=intent,
        input_type=first_input.get("type", "unknown") if first_input else "none",
        mime=first_input.get("mime") if first_input else None,
        privacy=constraints.get("privacy"),
        budget_max=budget.get("max"),
        offline_only=bool(constraints.get("offline_only", False)),
        deadline=constraints.get("deadline"),
    )


def feature_hash(features: JobFeatures) -> str:
    """sha256 của vector đặc trưng đã chuẩn hóa (doc 06 §1.3) — cùng công thức
    tinh thần với `CacheStore.compute_key()`: json ổn định (sort_keys) rồi hash."""
    raw = json.dumps(
        {
            "intent": features.intent,
            "input_type": features.input_type,
            "mime": features.mime,
            "privacy": features.privacy,
            "budget_max": features.budget_max,
            "offline_only": features.offline_only,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowCandidate:
    workflow_id: str
    version: int
    eligible: bool
    reason: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.workflow_id}@{self.version}"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.ref, "eligible": self.eligible}
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class WorkflowSelection:
    """Kết quả bước 1-3 (`choose_workflow()`) — chưa ghi gì xuống DB/Event, vì
    chưa có `process_id` thật (xem docstring module)."""

    chosen: WorkflowCandidate
    candidates: list[WorkflowCandidate]
    feature_hash: str
    intent: str


def _load_intents(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    intents: dict[str, list[dict[str, Any]]] = data.get("intents", {})
    return intents


class DecisionEngine:
    def __init__(
        self, registry: Registry, store: StateStore, events: EventBus, intents_path: Path
    ) -> None:
        self._registry = registry
        self._store = store
        self._events = events
        self._intents_path = intents_path

    async def choose_workflow(self, *, intent: str, spec: dict[str, Any]) -> WorkflowSelection:
        """Bước 1-3 (doc 06 §1.1): đặc trưng hóa, ứng viên, chấm điểm. KHÔNG ghi
        Decision Record — gọi `record_selection()` sau khi có `process_id` thật.
        `PaosError(NOT_FOUND)` nếu intent chưa khai báo trong policies/intents.yaml,
        hoặc mọi ứng viên đều không tồn tại trong Registry (intents.yaml lệch
        thực tế)."""
        features = extract_features(intent, spec)
        f_hash = feature_hash(features)

        declared = _load_intents(self._intents_path).get(intent)
        if not declared:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Chưa có workflow nào khai báo cho intent '{intent}'",
                hint="Thêm mục vào policies/intents.yaml::intents theo intent này",
                context={"intent": intent},
            )

        candidates = self._classify(declared)
        eligible = [c for c in candidates if c.eligible]
        if not eligible:
            reasons = "; ".join(f"{c.ref}={c.reason}" for c in candidates)
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Không có workflow khả dụng cho intent '{intent}' ({reasons})",
                hint="Kiểm policies/intents.yaml khớp thư mục workflows/ hiện có",
                context={"intent": intent},
            )

        chosen = await self._score(f_hash, eligible)
        return WorkflowSelection(
            chosen=chosen, candidates=candidates, feature_hash=f_hash, intent=intent
        )

    async def record_selection(self, process_id: str, selection: WorkflowSelection) -> str:
        """Bước 5 (doc 06 §1.1): ghi Decision Record scope=workflow_selection +
        phát `workflow.selected`. Gọi NGAY SAU `ProcessManager.create()` (đã có
        process_id thật). Trả về `decision_id`."""
        decision_id = ids.new_id("dec")
        rationale = self._rationale(selection.feature_hash, selection.candidates, selection.chosen)
        await self._write_decision(decision_id, process_id, selection, rationale)
        await self._events.publish(
            EventType.WORKFLOW_SELECTED.value,
            source="paosd.decision_engine",
            process_id=process_id,
            payload={"workflow_ref": selection.chosen.ref, "decision_id": decision_id},
        )
        return decision_id

    def _classify(self, declared: list[dict[str, Any]]) -> list[WorkflowCandidate]:
        candidates: list[WorkflowCandidate] = []
        for entry in declared:
            workflow_id = entry["workflow_id"]
            version = int(entry["version"])
            try:
                self._registry.get_workflow(workflow_id, version)
            except PaosError:
                candidates.append(
                    WorkflowCandidate(workflow_id, version, eligible=False, reason="NOT_REGISTERED")
                )
                continue
            candidates.append(WorkflowCandidate(workflow_id, version, eligible=True))
        return candidates

    async def _score(self, f_hash: str, eligible: list[WorkflowCandidate]) -> WorkflowCandidate:
        """Bước 3b (doc 06 §1.1) — tra `decision_outcomes` theo feature_hash. Ứng
        viên có success_rate cao nhất thắng; hòa hoặc chưa có lịch sử (cold
        start) -> rơi về thứ tự khai báo trong intents.yaml (cùng tinh thần
        Router._rationale() "ưu tiên #1 theo thứ tự khai báo" khi chưa có gì
        để so sánh)."""

        async def _query(conn: aiosqlite.Connection) -> dict[str, float]:
            cursor = await conn.execute(
                "SELECT chosen, AVG(succeeded) FROM decision_outcomes "
                "WHERE feature_hash = ? GROUP BY chosen",
                (f_hash,),
            )
            return {row[0]: float(row[1]) for row in await cursor.fetchall()}

        rates = await self._store.read(_query)
        best = eligible[0]
        best_rate = rates.get(best.ref)
        for candidate in eligible[1:]:
            rate = rates.get(candidate.ref)
            if rate is not None and (best_rate is None or rate > best_rate):
                best, best_rate = candidate, rate
        return best

    @staticmethod
    def _rationale(
        f_hash: str, candidates: list[WorkflowCandidate], chosen: WorkflowCandidate
    ) -> str:
        others = [c.ref for c in candidates if c.eligible and c.ref != chosen.ref]
        if others:
            return (
                f"{chosen.ref}: success_rate cao nhất theo feature_hash="
                f"{f_hash[:12]}… so với {others}"
            )
        return f"{chosen.ref}: ứng viên duy nhất khả dụng cho intent này"

    async def _write_decision(
        self, decision_id: str, process_id: str, selection: WorkflowSelection, rationale: str
    ) -> None:
        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO decisions(decision_id, process_id, scope, question, "
                "candidates_json, chosen, rationale, policy_version, inputs_hash, created_at) "
                "VALUES (?, ?, 'workflow_selection', ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id,
                    process_id,
                    f"intent={selection.intent}",
                    redact(
                        json.dumps([c.to_json() for c in selection.candidates], ensure_ascii=False)
                    ),
                    selection.chosen.ref,
                    redact(rationale),
                    _POLICY_VERSION,
                    selection.feature_hash,
                    _now_iso(),
                ),
            )

        await self._store.write(_insert)

    async def record_outcome(self, envelope: EventEnvelope) -> None:
        """Subscriber `kernel.process.completed`/`kernel.process.failed` (doc 06
        §1.3) — ghi 1 dòng `decision_outcomes` nếu Process này từng qua
        `record_selection()`. No-op nếu Process không dùng Decision Engine (caller
        tự truyền workflow_ref thẳng — vẫn hợp lệ, đúng doc 04 §1 workflow_ref
        optional). `quality`/`cost`/`user_edited` để NULL có ghi chú: Cost Engine
        là M7, quality/edit_rate ở mức artifact chưa nối dây tới Decision Record
        cấp Process — bịa số ở đây sẽ làm hỏng scoring, không phải giúp nó."""
        if envelope.process_id is None:
            return
        succeeded = envelope.type == EventType.PROCESS_COMPLETED.value

        async def _find(conn: aiosqlite.Connection) -> tuple[str, str, str] | None:
            cursor = await conn.execute(
                "SELECT decision_id, chosen, inputs_hash FROM decisions "
                "WHERE process_id = ? AND scope = ?",
                (envelope.process_id, _SCOPE),
            )
            return await cursor.fetchone()

        row = await self._store.read(_find)
        if row is None:
            return
        decision_id, chosen, f_hash = row

        async def _duration(conn: aiosqlite.Connection) -> tuple[str | None, str | None] | None:
            cursor = await conn.execute(
                "SELECT started_at, ended_at FROM processes WHERE process_id = ?",
                (envelope.process_id,),
            )
            return await cursor.fetchone()

        proc_row = await self._store.read(_duration)
        duration_ms = _duration_ms(proc_row) if proc_row is not None else None

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO decision_outcomes(decision_id, feature_hash, chosen, succeeded, "
                "quality, cost, duration_ms, user_edited, at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL, ?)",
                (decision_id, f_hash, chosen, int(succeeded), duration_ms, _now_iso()),
            )

        await self._store.write(_insert)


def _duration_ms(row: tuple[str | None, str | None]) -> int | None:
    started_at, ended_at = row
    if started_at is None or ended_at is None:
        return None
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    return int((end - start).total_seconds() * 1000)


def _now_iso() -> str:
    return clock.now().isoformat()

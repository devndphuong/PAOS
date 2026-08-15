"""WorkflowSpec — parse + validate DAG khai báo bằng YAML (doc 04 §4, ADR-0006).

Chỉ là DỮ LIỆU đã diễn giải + kiểm tra cấu trúc — KHÔNG thực thi gì ở đây (gọi
Capability/Agent là việc của tầng `apps/`, kernel/ không được phép biết tới
sdk/agents/, xem .importlinter "kernel-doc-lap"). `to_json_dict()` là bản đóng
băng ghi vào `workspace/projects/<x>/workflow.json` (doc 03 §4) — đóng băng CẤU
TRÚC DAG đã resolve thứ tự, KHÔNG đóng băng giá trị `${...}` (giá trị đó chỉ có
nghĩa lúc chạy, phụ thuộc output thật của từng step)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from kernel.errors import ErrorCode, PaosError
from kernel.workflow import expr

_VALID_KINDS = frozenset({"capability", "agent", "parallel"})
_MIN_STEPS_PATH_SEGMENTS = 2


def _spec_error(message: str, *, hint: str, workflow_id: str = "") -> PaosError:
    return PaosError(
        ErrorCode.INVALID_INPUT,
        message,
        hint=hint,
        context={"workflow_id": workflow_id} if workflow_id else {},
    )


@dataclass(frozen=True)
class RetryPolicy:
    max: int = 0


@dataclass(frozen=True)
class OnFail:
    """doc 04 §4 `on_fail` — vòng lặp tự sửa CÓ GIỚI HẠN (ADR-0006). `goto` phải
    trỏ tới một step_id đã khai báo TRƯỚC step này trong cùng danh sách `steps`
    (self-correction loop đi NGƯỢC lại một điểm đã qua, không phải nhánh mới)."""

    goto: str
    max_loops: int
    carry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    step_id: str
    kind: str  # "capability" | "agent" | "parallel"
    ref: str | None = None  # None chỉ khi kind == "parallel" (container không tự có ref)
    with_: dict[str, Any] = field(default_factory=dict)
    when: str | None = None
    resources: tuple[str, ...] = ()
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    on_fail: OnFail | None = None
    sub_steps: tuple[Step, ...] = ()  # chỉ non-empty khi kind == "parallel"


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    version: int
    description: str
    inputs: dict[str, Any]
    policy: dict[str, Any]
    steps: tuple[Step, ...]
    outputs: dict[str, Any]
    on_error: dict[str, Any]


def _parse_retry(data: dict[str, Any] | None) -> RetryPolicy:
    if not data:
        return RetryPolicy()
    return RetryPolicy(max=int(data.get("max", 0)))


def _parse_on_fail(data: dict[str, Any] | None) -> OnFail | None:
    if not data:
        return None
    if "goto" not in data or "max_loops" not in data:
        raise _spec_error(
            "on_fail thiếu 'goto' hoặc 'max_loops'",
            hint="on_fail bắt buộc có cả goto (step_id) và max_loops (ADR-0006: "
            "mọi vòng lặp phải có giới hạn)",
        )
    return OnFail(
        goto=data["goto"], max_loops=int(data["max_loops"]), carry=dict(data.get("carry", {}))
    )


def _parse_step(data: dict[str, Any], *, in_parallel: bool) -> Step:
    if "id" not in data:
        raise _spec_error("Step thiếu 'id'", hint="Mỗi step trong 'steps' phải có field 'id'")
    kind = data.get("kind")
    if kind not in _VALID_KINDS:
        raise _spec_error(
            f"Step '{data['id']}' có kind '{kind}' không hợp lệ",
            hint=f"kind phải là một trong {sorted(_VALID_KINDS)}",
        )
    if kind == "parallel":
        if in_parallel:
            raise _spec_error(
                f"Step '{data['id']}': không hỗ trợ parallel lồng trong parallel",
                hint="Tách nhánh song song lồng nhau thành 2 step 'parallel' cấp cao nhất, "
                "nối bằng phụ thuộc ${...} — giữ YAML là dữ liệu, đừng làm phức tạp (ADR-0006)",
            )
        sub_raw = data.get("steps", [])
        if not sub_raw:
            raise _spec_error(
                f"Step parallel '{data['id']}' không có 'steps' con", hint="Thêm ít nhất 1 sub-step"
            )
        sub_steps = tuple(_parse_step(s, in_parallel=True) for s in sub_raw)
        return Step(step_id=data["id"], kind=kind, sub_steps=sub_steps)

    if "ref" not in data:
        raise _spec_error(
            f"Step '{data['id']}' thiếu 'ref'", hint="capability/agent step cần 'ref'"
        )
    return Step(
        step_id=data["id"],
        kind=kind,
        ref=data["ref"],
        with_=dict(data.get("with", {})),
        when=data.get("when"),
        resources=tuple(data.get("resources", [])),
        retry=_parse_retry(data.get("retry")),
        on_fail=_parse_on_fail(data.get("on_fail")),
    )


def parse_workflow_spec(text: str) -> WorkflowSpec:
    data = yaml.safe_load(text)
    if "id" not in data or "version" not in data:
        raise _spec_error("Workflow thiếu 'id' hoặc 'version'", hint="Thêm 2 field bắt buộc này")
    steps_raw = data.get("steps", [])
    if not steps_raw:
        raise _spec_error(
            "Workflow không có 'steps'", hint="Thêm ít nhất 1 step", workflow_id=data.get("id", "")
        )
    spec = WorkflowSpec(
        workflow_id=data["id"],
        version=int(data["version"]),
        description=data.get("description", ""),
        inputs=dict(data.get("inputs", {})),
        policy=dict(data.get("policy", {})),
        steps=tuple(_parse_step(s, in_parallel=False) for s in steps_raw),
        outputs=dict(data.get("outputs", {})),
        on_error=dict(data.get("on_error", {})),
    )
    validate_workflow(spec)
    return spec


def _all_referenceable_step_ids(spec: WorkflowSpec) -> set[str]:
    """Top-level step_id nào cũng tham chiếu được qua `steps.<id>...`; sub-step
    trong 1 group parallel KHÔNG có id riêng ở namespace top-level — chỉ tham
    chiếu được qua `steps.<group_id>.<sub_id>...` (doc 04 §4 ví dụ
    `steps.media.images.output`), không cộng thêm vào tập này."""
    return {s.step_id for s in spec.steps}


def _collect_expr_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if expr.is_expr(value) else []
    if isinstance(value, dict):
        result: list[str] = []
        for v in value.values():
            result.extend(_collect_expr_strings(v))
        return result
    if isinstance(value, list):
        result = []
        for v in value:
            result.extend(_collect_expr_strings(v))
        return result
    return []


def _referenced_step_ids(step: Step) -> set[str]:
    """Suy ra step này phụ thuộc những step_id nào — quét mọi biểu thức
    `${...}` trong `when` + `with` (kể cả lồng trong dict/list), lấy segment
    thứ 2 của mọi PathRef bắt đầu bằng 'steps'."""
    raw_exprs = list(_collect_expr_strings(step.with_))
    if step.when is not None and expr.is_expr(step.when):
        raw_exprs.append(step.when)

    deps: set[str] = set()
    for raw in raw_exprs:
        node = expr.parse_expr(expr.unwrap(raw))
        for path_ref in expr.collect_path_refs(node):
            if (
                len(path_ref.segments) >= _MIN_STEPS_PATH_SEGMENTS
                and path_ref.segments[0] == "steps"
            ):
                deps.add(path_ref.segments[1])
    return deps


def step_dependencies(spec: WorkflowSpec) -> dict[str, set[str]]:
    """step_id (top-level, kể cả 'parallel' group) -> tập step_id nó phụ thuộc.
    Với group parallel, phụ thuộc là HỢP của phụ thuộc mọi sub-step (group chỉ
    bắt đầu khi mọi input của mọi nhánh con đã sẵn sàng)."""
    known = _all_referenceable_step_ids(spec)
    result: dict[str, set[str]] = {}
    for step in spec.steps:
        if step.kind == "parallel":
            deps: set[str] = set()
            for sub in step.sub_steps:
                deps |= _referenced_step_ids(sub)
        else:
            deps = _referenced_step_ids(step)
        unknown = deps - known - {step.step_id}
        if unknown:
            raise _spec_error(
                f"Step '{step.step_id}' tham chiếu step_id chưa khai báo: {sorted(unknown)}",
                hint="Kiểm lại tên step trong biểu thức ${steps.<id>...} — phải khớp 'id' "
                "của một step khác trong cùng workflow",
                workflow_id=spec.workflow_id,
            )
        result[step.step_id] = deps - {step.step_id}
    return result


def topological_order(spec: WorkflowSpec) -> list[str]:
    """Kahn's algorithm — cũng chính là cách phát hiện chu trình (DAG thật sự
    phải không có chu trình; on_fail.goto là vòng lặp ĐI NGƯỢC có kiểm soát ở
    tầng thực thi, không phải cạnh của đồ thị phụ thuộc dữ liệu này).

    Tie-break theo THỨ TỰ KHAI BÁO trong YAML (không phải alphabet trên
    step_id) — khi 2 step cùng "sẵn sàng" (không phụ thuộc nhau), tôn trọng
    thứ tự tác giả viết ra thay vì một thứ tự ngẫu nhiên khác ý định (vd
    `review_script` chỉ phụ thuộc `script`, `media` cũng chỉ phụ thuộc
    `script` — tác giả đặt review_script trước media để review trước khi tốn
    tài nguyên sinh media song song; alphabet sẽ đẩy media lên trước, sai ý)."""
    deps = step_dependencies(spec)
    declared_index = {s.step_id: i for i, s in enumerate(spec.steps)}
    in_degree = {step_id: len(d) for step_id, d in deps.items()}
    dependents: dict[str, list[str]] = {step_id: [] for step_id in deps}
    for step_id, d in deps.items():
        for dep in d:
            dependents[dep].append(step_id)

    ready = sorted(
        (step_id for step_id, deg in in_degree.items() if deg == 0), key=declared_index.__getitem__
    )
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for dependent in sorted(dependents[current], key=declared_index.__getitem__):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=declared_index.__getitem__)

    if len(order) != len(deps):
        remaining = sorted(set(deps) - set(order))
        raise _spec_error(
            f"Workflow có chu trình phụ thuộc, liên quan tới: {remaining}",
            hint="Xoá tham chiếu vòng trong ${steps...} — DAG không được có chu trình "
            "(vòng lặp có kiểm soát dùng on_fail.goto, không phải phụ thuộc dữ liệu chéo)",
            workflow_id=spec.workflow_id,
        )
    return order


def validate_workflow(spec: WorkflowSpec) -> None:
    """Kiểm toàn bộ ràng buộc doc 04 §4 + ADR-0006 trước khi Process được phép
    chạy workflow này: step_id duy nhất, DAG không chu trình, mọi tham chiếu
    step_id tồn tại, mọi on_fail.goto trỏ tới step ĐÃ khai báo trước đó."""
    seen: set[str] = set()
    for i, step in enumerate(spec.steps):
        if step.step_id in seen:
            raise _spec_error(
                f"step_id '{step.step_id}' lặp lại",
                hint="Mỗi step (kể cả sub-step trong parallel) phải có id duy nhất trong workflow",
                workflow_id=spec.workflow_id,
            )
        seen.add(step.step_id)
        if step.on_fail is not None:
            earlier_ids = {s.step_id for s in spec.steps[:i]}
            if step.on_fail.goto not in earlier_ids:
                raise _spec_error(
                    f"Step '{step.step_id}': on_fail.goto '{step.on_fail.goto}' phải trỏ tới "
                    "một step_id đã khai báo TRƯỚC nó",
                    hint="self-correction loop đi ngược lại một step đã qua, không phải bước mới",
                    workflow_id=spec.workflow_id,
                )
            if step.on_fail.max_loops < 1:
                raise _spec_error(
                    f"Step '{step.step_id}': on_fail.max_loops phải >= 1",
                    hint="ADR-0006: mọi vòng lặp phải có giới hạn cụ thể",
                    workflow_id=spec.workflow_id,
                )
        for sub in step.sub_steps:
            if sub.step_id in seen:
                raise _spec_error(
                    f"step_id '{sub.step_id}' lặp lại (trong nhóm parallel '{step.step_id}')",
                    hint="Mỗi step (kể cả sub-step trong parallel) phải có id duy nhất "
                    "trong workflow",
                    workflow_id=spec.workflow_id,
                )
            seen.add(sub.step_id)

    topological_order(spec)  # tự raise nếu có chu trình / tham chiếu step_id chưa khai báo


def _step_to_dict(step: Step) -> dict[str, Any]:
    result: dict[str, Any] = {"id": step.step_id, "kind": step.kind}
    if step.kind == "parallel":
        result["steps"] = [_step_to_dict(s) for s in step.sub_steps]
        return result
    result["ref"] = step.ref
    result["with"] = step.with_
    if step.when is not None:
        result["when"] = step.when
    if step.resources:
        result["resources"] = list(step.resources)
    if step.retry.max:
        result["retry"] = {"max": step.retry.max}
    if step.on_fail is not None:
        result["on_fail"] = {
            "goto": step.on_fail.goto,
            "max_loops": step.on_fail.max_loops,
            "carry": step.on_fail.carry,
        }
    return result


def to_json_dict(spec: WorkflowSpec) -> dict[str, Any]:
    """Bản đóng băng ghi vào `workspace/projects/<x>/workflow.json` (doc 03 §4)
    — DAG đã resolve thứ tự, tái lập được y hệt sau này."""
    return {
        "id": spec.workflow_id,
        "version": spec.version,
        "description": spec.description,
        "inputs": spec.inputs,
        "policy": spec.policy,
        "steps": [_step_to_dict(s) for s in spec.steps],
        "outputs": spec.outputs,
        "on_error": spec.on_error,
        "resolved_order": topological_order(spec),
    }

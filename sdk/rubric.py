"""Rubric engine — chấm điểm có cấu trúc cho artifact (doc 08 §2/§3, ADR-0008,
M4 P-M4-1, doc 19).

Độc lập hoàn toàn với kernel/ — cùng lý do đã áp dụng cho sdk/agent.py,
sdk/provider.py (xem docstring 2 file đó): `agents/` bị cấm import kernel/ kể
cả GIÁN TIẾP qua sdk/ (.importlinter contract "agent-chi-dung-sdk" kiểm đường
phụ thuộc bắc cầu, không chỉ import trực tiếp). Vì vậy module này không tái sử
dụng `kernel/workflow/expr.py` dù cùng triết lý (xem bên dưới) — 2 bản độc lập
nhỏ chấp nhận được để giữ ranh giới P1/P3, giống tiền lệ `sdk/provider.py::ErrorCode`.

Module này KHÔNG tự gọi LLM/capability nào — hàm `evaluate()` nhận `call_llm`
đã tiêm sẵn từ caller (Review Agent, `agents/review/`, P-M4-2) để giữ engine
THUẦN LOGIC: test được không cần I/O thật, và tái dùng được cho eval harness
(`tests/eval/`, P-M4-3) mà không cần dựng cả Runner/Router.

ADR-0008 (doc 15): rubric gồm tiêu chí `deterministic` + `llm` (+ `hybrid` lai
cả hai); LUÔN chạy tất định trước, `fail_fast` reject ngay 0 chi phí LLM; LLM
judge phải khác provider đã sinh artifact — ràng buộc "khác provider" được
CƯỠNG CHẾ ở Router (`exclude_provider`, P-M4-2), KHÔNG phải ở đây; engine này
chỉ đảm bảo phần "tất định trước" của ADR-0008 (không gọi `call_llm` cho tiêu
chí bị fail_fast chặn).

Tiêu chí `hybrid` (doc 08 §2 ví dụ `redundancy`: có cả `check` lẫn `question`)
không có công thức phối trộn nào ghi trong doc 08 — quyết định thiết kế ở đây:
điểm cuối = 50% kết quả `check` (0/100) + 50% điểm LLM. `fail_fast` CÓ THỂ
tham chiếu một tiêu chí `hybrid` (không chỉ `deterministic` thuần) — khi đó chỉ
phần `check` được xét để quyết định fail-fast, LLM vẫn không bị gọi.

Biểu thức `check:` (vd `"150 <= word_count <= 200"`, `"has_section('cta')"`,
`"n_gram_overlap(script) < 0.18"`) dùng một parser tự viết nhỏ (đệ quy xuống
dòng) — CÙNG lý do ADR-0006/ADR-0027 đã chọn cho `kernel/workflow/expr.py`: an
toàn là thuộc tính CẤU TRÚC (không đường nào chạm `eval`/`exec`/`compile`),
không phải kỷ luật lọc input. Văn phạm ở đây khác một chút (thêm gọi hàm 1 cấp
`ident(args...)`, bỏ `??`/đường dẫn `.` lồng nhau — rubric không cần đọc `steps.*`).

Ngữ cảnh (`context`) truyền vào biểu thức luôn có sẵn:
  - `word_count` — số từ trong artifact text.
  - `<rubric.applies_to>` — CHÍNH artifact text, đặt tên theo `applies_to` của
    rubric (vd rubric `applies_to: script` → biến tên `script`), khớp đúng
    cách viết `n_gram_overlap(script)` ở ví dụ doc 08 §2.
  - `has_section(name)` — closure đã đóng sẵn artifact text, chỉ nhận tên mục.
  - `n_gram_overlap(text)` — hàm thuần `str -> float`, đo tỉ lệ n-gram lặp lại
    NGAY TRONG text được truyền (tự trùng lặp nội bộ, n=3 cố định).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sdk.agent import AgentError
from sdk.provider import ErrorCode

_VALID_KINDS = frozenset({"llm", "deterministic", "hybrid"})
_NGRAM_N = 3  # cố định — chưa có ca dùng thật nào cần cấu hình khác (P4)
_HYBRID_CHECK_WEIGHT = 0.5

_COMP_OPS = frozenset({"==", "!=", "<=", ">=", "<", ">"})
_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<STRING>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<NUMBER>-?\d+(?:\.\d+)?)
      | (?P<COMP>==|!=|<=|>=|<|>)
      | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<LPAREN>\()
      | (?P<RPAREN>\))
      | (?P<COMMA>,)
    )""",
    re.VERBOSE,
)


def _rubric_error(message: str, *, hint: str) -> AgentError:
    return AgentError(ErrorCode.INVALID_INPUT, message, hint=hint)


# ─── Biểu thức check: tokenizer + parser đệ quy xuống dòng ──────────────────


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


@dataclass(frozen=True)
class _Literal:
    value: Any


@dataclass(frozen=True)
class _Ident:
    name: str


@dataclass(frozen=True)
class _Call:
    name: str
    args: tuple[_Node, ...]


@dataclass(frozen=True)
class _Comparison:
    operands: tuple[_Node, ...]
    ops: tuple[str, ...]


_Node = _Literal | _Ident | _Call | _Comparison


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(source):
        match = _TOKEN_RE.match(source, pos)
        if match is None:
            if source[pos:].strip() == "":
                break
            raise _rubric_error(
                f"Không đọc được biểu thức check tại vị trí {pos}: '{source[pos:]}'",
                hint="Chỉ hỗ trợ so sánh, gọi hàm 1 cấp, chuỗi/số — xem docstring sdk/rubric.py",
            )
        pos = match.end()
        kind = match.lastgroup
        if kind is None:
            continue
        tokens.append(_Token(kind=kind, value=match.group(kind)))
    return tokens


def _unescape_string(raw: str) -> str:
    return raw[1:-1].replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def _parse_operand(tokens: list[_Token], i: int) -> tuple[_Node, int]:
    if i >= len(tokens):
        raise _rubric_error(
            "Biểu thức check kết thúc sớm, còn thiếu toán hạng",
            hint="Kiểm cú pháp check trong rubric YAML",
        )
    tok = tokens[i]
    if tok.kind == "NUMBER":
        value: Any = float(tok.value) if "." in tok.value else int(tok.value)
        return _Literal(value), i + 1
    if tok.kind == "STRING":
        return _Literal(_unescape_string(tok.value)), i + 1
    if tok.kind == "IDENT":
        if i + 1 < len(tokens) and tokens[i + 1].kind == "LPAREN":
            return _parse_call(tokens, i)
        return _Ident(tok.value), i + 1
    raise _rubric_error(
        f"Token không hợp lệ ở vị trí toán hạng: '{tok.value}'",
        hint="Kiểm cú pháp check trong rubric YAML",
    )


def _parse_call(tokens: list[_Token], i: int) -> tuple[_Node, int]:
    name = tokens[i].value
    i += 2  # IDENT '('
    args: list[_Node] = []
    if i < len(tokens) and tokens[i].kind != "RPAREN":
        arg, i = _parse_operand(tokens, i)
        args.append(arg)
        while i < len(tokens) and tokens[i].kind == "COMMA":
            arg, i = _parse_operand(tokens, i + 1)
            args.append(arg)
    if i >= len(tokens) or tokens[i].kind != "RPAREN":
        raise _rubric_error(
            f"Thiếu ')' đóng lời gọi hàm '{name}(...)'", hint="Kiểm cú pháp check trong rubric YAML"
        )
    return _Call(name, tuple(args)), i + 1


def _parse_check(source: str) -> _Node:
    tokens = _tokenize(source)
    if not tokens:
        raise _rubric_error(
            "Biểu thức check rỗng",
            hint="Điền check: '<biểu thức>' cho tiêu chí deterministic/hybrid",
        )
    first, i = _parse_operand(tokens, 0)
    operands = [first]
    ops: list[str] = []
    while i < len(tokens) and tokens[i].kind == "COMP":
        ops.append(tokens[i].value)
        nxt, i = _parse_operand(tokens, i + 1)
        operands.append(nxt)
    if i != len(tokens):
        raise _rubric_error(
            f"Thừa token trong check sau vị trí {i}: '{tokens[i].value}'",
            hint="Kiểm cú pháp check trong rubric YAML",
        )
    if not ops:
        return operands[0]
    return _Comparison(tuple(operands), tuple(ops))


def _compare_pair(left: Any, op: str, right: Any) -> bool:
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    try:
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        return bool(left >= right)
    except TypeError as exc:
        raise _rubric_error(
            f"Không so sánh được {type(left).__name__} với {type(right).__name__} bằng '{op}'",
            hint="Kiểm kiểu dữ liệu 2 vế trong check của rubric YAML",
        ) from exc


def _eval_node(node: _Node, context: dict[str, Any]) -> Any:
    if isinstance(node, _Literal):
        return node.value
    if isinstance(node, _Ident):
        if node.name not in context:
            raise _rubric_error(
                f"Không rõ định danh '{node.name}' trong check",
                hint="Định danh hợp lệ: word_count, applies_to của rubric, "
                "has_section, n_gram_overlap",
            )
        return context[node.name]
    if isinstance(node, _Call):
        func = context.get(node.name)
        if not callable(func):
            raise _rubric_error(
                f"'{node.name}' không phải hàm hợp lệ trong check",
                hint="Hàm hỗ trợ: has_section(name), n_gram_overlap(text)",
            )
        args = [_eval_node(a, context) for a in node.args]
        return func(*args)
    values = [_eval_node(o, context) for o in node.operands]
    return all(_compare_pair(values[i], op, values[i + 1]) for i, op in enumerate(node.ops))


def evaluate_check(source: str, context: dict[str, Any]) -> bool:
    """Đánh giá 1 biểu thức `check:` — luôn trả bool (coerce nếu vế cuối không
    tự nhiên là bool, vd một lời gọi hàm trả về giá trị truthy/falsy)."""
    return bool(_eval_node(_parse_check(source), context))


# ─── Hàm dựng sẵn cho context — has_section / n_gram_overlap ────────────────


def _has_section(text: str, name: str) -> bool:
    needle = name.strip().lower()
    for line in text.splitlines():
        cleaned = line.strip().strip("#*->[]").strip().lower()
        if (
            cleaned == needle
            or cleaned.startswith(needle + ":")
            or cleaned.startswith(needle + " ")
        ):
            return True
    return False


def _n_gram_overlap(text: str, n: int = _NGRAM_N) -> float:
    words = text.split()
    if len(words) < n:
        return 0.0
    grams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    unique = len(set(grams))
    total = len(grams)
    return (total - unique) / total if total else 0.0


def _build_check_context(text: str, applies_to: str) -> dict[str, Any]:
    return {
        "word_count": len(text.split()),
        applies_to: text,
        "has_section": lambda name: _has_section(text, name),
        "n_gram_overlap": _n_gram_overlap,
    }


# ─── Rubric YAML ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RubricCriterion:
    id: str
    weight: float
    kind: str  # "llm" | "deterministic" | "hybrid"
    question: str | None = None
    check: str | None = None
    scale: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricSpec:
    id: str
    version: int
    applies_to: str
    threshold: float
    criteria: list[RubricCriterion]
    fail_fast: list[str] = field(default_factory=list)


def load_rubric(path: Path) -> RubricSpec:
    """doc 08 §2 — rubric YAML có version, gắn với loại artifact (`applies_to`)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    criteria = [
        RubricCriterion(
            id=c["id"],
            weight=float(c["weight"]),
            kind=c["kind"],
            question=c.get("question"),
            check=c.get("check"),
            scale={int(k): v for k, v in c.get("scale", {}).items()},
        )
        for c in data.get("criteria", [])
    ]
    rubric = RubricSpec(
        id=data["id"],
        version=int(data["version"]),
        applies_to=data["applies_to"],
        threshold=float(data["threshold"]),
        criteria=criteria,
        fail_fast=list(data.get("fail_fast", [])),
    )
    _validate_rubric(rubric, path)
    return rubric


def _validate_rubric(rubric: RubricSpec, path: Path) -> None:
    by_id = {c.id: c for c in rubric.criteria}
    for criterion in rubric.criteria:
        if criterion.kind not in _VALID_KINDS:
            raise _rubric_error(
                f"{path}: tiêu chí '{criterion.id}' có kind '{criterion.kind}' không hợp lệ",
                hint=f"kind phải thuộc {sorted(_VALID_KINDS)}",
            )
        if criterion.kind in {"deterministic", "hybrid"} and not criterion.check:
            raise _rubric_error(
                f"{path}: tiêu chí '{criterion.id}' kind={criterion.kind} thiếu 'check'",
                hint="deterministic/hybrid bắt buộc có trường check",
            )
        if criterion.kind in {"llm", "hybrid"} and not criterion.question:
            raise _rubric_error(
                f"{path}: tiêu chí '{criterion.id}' kind={criterion.kind} thiếu 'question'",
                hint="llm/hybrid bắt buộc có trường question",
            )
    for fid in rubric.fail_fast:
        if fid not in by_id:
            raise _rubric_error(
                f"{path}: fail_fast tham chiếu tiêu chí '{fid}' chưa khai báo trong criteria",
                hint="Mọi tên trong fail_fast phải khớp một criteria[].id",
            )
        if by_id[fid].kind == "llm":
            raise _rubric_error(
                f"{path}: fail_fast không thể tham chiếu tiêu chí '{fid}' "
                "(kind=llm, không có check)",
                hint="fail_fast chỉ áp dụng cho tiêu chí deterministic/hybrid (có check)",
            )


# ─── Đánh giá rubric — chấm điểm, gọi LLM judge khi cần ─────────────────────


@dataclass(frozen=True)
class LlmJudgeVerdict:
    """Kết quả 1 lượt LLM judge cho 1 criterion — do `call_llm` (Review Agent,
    P-M4-2) tự dựng prompt + gọi capability + parse JSON trả về hình dạng này."""

    score: float  # 0-100
    issue: str | None = None
    suggestion: str | None = None
    location: str | None = None
    keep_note: str | None = None  # ghi nhận phần đang tốt khi score cao (doc 08 §3 "keep")


CallLlmJudge = Callable[[RubricCriterion, str, dict[str, str]], Awaitable[LlmJudgeVerdict]]


@dataclass(frozen=True)
class CriterionScore:
    criterion_id: str
    score: float
    skipped: bool


@dataclass(frozen=True)
class FailedCriterion:
    criterion_id: str
    score: float
    issue: str | None
    suggestion: str | None
    location: str | None


@dataclass(frozen=True)
class RubricReviewResult:
    rubric_id: str
    rubric_version: int
    score: float
    verdict: str  # "pass" | "reject"
    fail_fast_triggered: str | None
    llm_calls_skipped: int
    breakdown: list[CriterionScore]
    failed: list[FailedCriterion]
    keep: list[str]

    def to_feedback_json(self) -> dict[str, Any]:
        """doc 08 §3 — hình dạng feedback bắt buộc khi reject."""
        return {
            "score": round(self.score),
            "verdict": self.verdict,
            "failed": [
                {
                    "criterion": f.criterion_id,
                    "score": round(f.score),
                    "issue": f.issue,
                    "suggestion": f.suggestion,
                    "location": f.location,
                }
                for f in self.failed
            ],
            "keep": self.keep,
        }


def _weighted_score(scored: list[tuple[RubricCriterion, float]]) -> float:
    total_weight = sum(c.weight for c, _ in scored)
    if total_weight == 0:
        return 0.0
    return sum(c.weight * s for c, s in scored) / total_weight


async def evaluate(
    rubric: RubricSpec,
    text: str,
    *,
    call_llm: CallLlmJudge,
    extra_context: dict[str, str] | None = None,
) -> RubricReviewResult:
    """doc 08 §3 các bước [1]-[4]. `extra_context` (nguồn, preference, feedback
    lần trước — doc 08 §3) chuyển tiếp NGUYÊN VẸN cho `call_llm`, engine không
    đọc nội dung của nó."""
    check_ctx = _build_check_context(text, rubric.applies_to)
    extra = extra_context or {}

    det_results: dict[str, bool] = {
        c.id: evaluate_check(c.check, check_ctx)
        for c in rubric.criteria
        if c.check is not None
    }

    fail_fast_hit = next((fid for fid in rubric.fail_fast if not det_results[fid]), None)
    if fail_fast_hit is not None:
        scored = [
            (c, 100.0 if det_results[c.id] else 0.0) for c in rubric.criteria if c.check is not None
        ]
        skipped_ids = {c.id for c in rubric.criteria} - {c.id for c, _ in scored}
        breakdown = [CriterionScore(c.id, s, skipped=False) for c, s in scored]
        breakdown += [CriterionScore(cid, 0.0, skipped=True) for cid in skipped_ids]
        llm_criteria_count = sum(1 for c in rubric.criteria if c.kind in {"llm", "hybrid"})
        return RubricReviewResult(
            rubric_id=rubric.id,
            rubric_version=rubric.version,
            score=_weighted_score(scored),
            verdict="reject",
            fail_fast_triggered=fail_fast_hit,
            llm_calls_skipped=llm_criteria_count,
            breakdown=breakdown,
            failed=[
                FailedCriterion(fail_fast_hit, 0.0, "Rớt kiểm tra tất định (fail_fast)", None, None)
            ],
            keep=[],
        )

    scored = []
    breakdown = []
    failed: list[FailedCriterion] = []
    keep: list[str] = []
    for criterion in rubric.criteria:
        score, verdict = await _score_criterion(criterion, det_results, text, extra, call_llm)
        scored.append((criterion, score))
        breakdown.append(CriterionScore(criterion.id, score, skipped=False))
        if score < rubric.threshold:
            failed.append(
                FailedCriterion(
                    criterion.id,
                    score,
                    verdict.issue if verdict else None,
                    verdict.suggestion if verdict else None,
                    verdict.location if verdict else None,
                )
            )
        elif verdict and verdict.keep_note:
            keep.append(verdict.keep_note)

    final_score = _weighted_score(scored)
    return RubricReviewResult(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        score=final_score,
        verdict="pass" if final_score >= rubric.threshold else "reject",
        fail_fast_triggered=None,
        llm_calls_skipped=0,
        breakdown=breakdown,
        failed=failed,
        keep=keep,
    )


async def _score_criterion(
    criterion: RubricCriterion,
    det_results: dict[str, bool],
    text: str,
    extra_context: dict[str, str],
    call_llm: CallLlmJudge,
) -> tuple[float, LlmJudgeVerdict | None]:
    if criterion.kind == "deterministic":
        return (100.0 if det_results[criterion.id] else 0.0), None
    verdict = await call_llm(criterion, text, extra_context)
    llm_score = max(0.0, min(100.0, verdict.score))
    if criterion.kind == "llm":
        return llm_score, verdict
    det_score = 100.0 if det_results[criterion.id] else 0.0
    blended = det_score * _HYBRID_CHECK_WEIGHT + llm_score * (1 - _HYBRID_CHECK_WEIGHT)
    return blended, verdict

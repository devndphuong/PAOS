"""Biểu thức `${...}` của Workflow — parser tự viết, KHÔNG `eval`/`exec`/`compile`
(doc 04 §4, ADR-0006, ADR-0027). Văn phạm cố ý tối giản — xem ADR-0027 cho lý do
và 2 phương án đã loại (eval sandbox, thư viện ngoài).

    expression := comparison | coalesce
    comparison := coalesce COMP_OP coalesce      # == != < <= > >=
    coalesce    := operand ("??" operand)*
    operand     := path | literal
    path        := IDENT ("." IDENT)+            # steps.<id>.output.<field...> | inputs.<field...>
    literal     := NUMBER | STRING | "true" | "false" | "null"

`path` chỉ đọc qua `dict.get()` từng chặng — không có cách nào truy cập thuộc
tính Python thật, gọi hàm, hay import. Thiếu field -> None (để `??` có cái mà
rơi vào, xem ADR-0027 mục Hệ quả về đánh đổi với lỗi gõ sai tên field).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from kernel.errors import ErrorCode, PaosError

_COMP_OPS = frozenset({"==", "!=", "<=", ">=", "<", ">"})
_KEYWORD_LITERALS: dict[str, Any] = {"true": True, "false": False, "null": None}

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<STRING>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<NUMBER>-?\d+(?:\.\d+)?)
      | (?P<COALESCE>\?\?)
      | (?P<COMP>==|!=|<=|>=|<|>)
      | (?P<PATH>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)
      | (?P<KEYWORD>true|false|null)
    )""",
    re.VERBOSE,
)
_WRAPPER_RE = re.compile(r"^\$\{(?P<body>.*)\}$", re.DOTALL)


def _expr_error(message: str, source: str) -> PaosError:
    return PaosError(
        ErrorCode.INVALID_INPUT,
        message,
        hint="Xem văn phạm biểu thức ở doc 04 §4 / ADR-0027 — chỉ hỗ trợ so sánh, "
        "?? và truy cập trường dạng steps.<id>.output.<field> hoặc inputs.<field>",
        context={"expr": source},
    )


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


@dataclass(frozen=True)
class PathRef:
    segments: tuple[str, ...]


@dataclass(frozen=True)
class Literal:
    value: Any


@dataclass(frozen=True)
class Coalesce:
    operands: tuple[Node, ...]


@dataclass(frozen=True)
class Comparison:
    left: Node
    op: str
    right: Node


Node = PathRef | Literal | Coalesce | Comparison


def _unescape_string(raw: str) -> str:
    body = raw[1:-1]
    return body.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(source):
        match = _TOKEN_RE.match(source, pos)
        if match is None:
            if source[pos:].strip() == "":
                break
            raise _expr_error(
                f"Không đọc được biểu thức tại vị trí {pos}: '{source[pos:]}'", source
            )
        pos = match.end()
        kind = match.lastgroup
        if kind is None:  # chỉ khoảng trắng
            continue
        tokens.append(_Token(kind=kind, value=match.group(kind)))
    return tokens


def _parse_operand(tokens: list[_Token], i: int, source: str) -> tuple[Node, int]:
    if i >= len(tokens):
        raise _expr_error("Biểu thức kết thúc sớm, còn thiếu toán hạng", source)
    tok = tokens[i]
    if tok.kind == "PATH":
        return PathRef(tuple(tok.value.split("."))), i + 1
    if tok.kind == "NUMBER":
        value: Any = float(tok.value) if "." in tok.value else int(tok.value)
        return Literal(value), i + 1
    if tok.kind == "STRING":
        return Literal(_unescape_string(tok.value)), i + 1
    if tok.kind == "KEYWORD":
        return Literal(_KEYWORD_LITERALS[tok.value]), i + 1
    raise _expr_error(f"Token không hợp lệ ở vị trí toán hạng: '{tok.value}'", source)


def _parse_coalesce(tokens: list[_Token], i: int, source: str) -> tuple[Node, int]:
    first, i = _parse_operand(tokens, i, source)
    operands = [first]
    while i < len(tokens) and tokens[i].kind == "COALESCE":
        nxt, i = _parse_operand(tokens, i + 1, source)
        operands.append(nxt)
    if len(operands) == 1:
        return operands[0], i
    return Coalesce(tuple(operands)), i


def parse_expr(source: str) -> Node:
    tokens = _tokenize(source)
    if not tokens:
        raise _expr_error("Biểu thức rỗng", source)
    node, i = _parse_coalesce(tokens, 0, source)
    if i < len(tokens) and tokens[i].kind == "COMP":
        op = tokens[i].value
        right, i = _parse_coalesce(tokens, i + 1, source)
        node = Comparison(node, op, right)
    if i != len(tokens):
        raise _expr_error(f"Thừa token sau vị trí {i}: '{tokens[i].value}'", source)
    return node


def _resolve_path(segments: tuple[str, ...], context: dict[str, Any]) -> Any:
    cur: Any = context
    for seg in segments:
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _compare(left: Any, op: str, right: Any, source: str) -> bool:
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    if left is None or right is None:
        raise _expr_error(
            f"Không so sánh được None bằng toán tử '{op}' (chỉ == / != chấp nhận None)", source
        )
    try:
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        return bool(left >= right)
    except TypeError as exc:
        raise _expr_error(
            f"Không so sánh được {type(left).__name__} với {type(right).__name__} "
            f"bằng toán tử '{op}'",
            source,
        ) from exc


def _evaluate(node: Node, context: dict[str, Any], source: str) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, PathRef):
        return _resolve_path(node.segments, context)
    if isinstance(node, Coalesce):
        for operand in node.operands:
            value = _evaluate(operand, context, source)
            if value is not None:
                return value
        return None
    left = _evaluate(node.left, context, source)
    right = _evaluate(node.right, context, source)
    return _compare(left, node.op, right, source)


def evaluate_expr(source: str, context: dict[str, Any]) -> Any:
    return _evaluate(parse_expr(source), context, source)


def collect_path_refs(node: Node) -> list[PathRef]:
    """Mọi `PathRef` xuất hiện trong biểu thức — dùng để suy ra step nào phụ
    thuộc step nào (kernel/workflow/spec.py::step_dependencies), KHÔNG đánh giá
    giá trị gì."""
    if isinstance(node, PathRef):
        return [node]
    if isinstance(node, Literal):
        return []
    if isinstance(node, Coalesce):
        result: list[PathRef] = []
        for operand in node.operands:
            result.extend(collect_path_refs(operand))
        return result
    return collect_path_refs(node.left) + collect_path_refs(node.right)


def is_expr(value: Any) -> bool:
    return isinstance(value, str) and _WRAPPER_RE.match(value) is not None


def unwrap(source: str) -> str:
    """Bỏ vỏ `${...}`, trả về phần thân để parse — dùng khi chỉ cần phân tích
    cấu trúc (vd suy ra phụ thuộc step) mà không đánh giá giá trị."""
    match = _WRAPPER_RE.match(source)
    if match is None:
        raise _expr_error("Không phải biểu thức dạng ${...}", source)
    return match.group("body")


def resolve(value: Any, context: dict[str, Any]) -> Any:
    """`${...}` -> đánh giá và trả kết quả; giá trị khác trả nguyên vẹn.
    `${...}` chỉ được dùng làm TOÀN BỘ giá trị field YAML — không nhúng nhiều
    `${}` trong một chuỗi dài hơn (ADR-0027, giữ đúng phạm vi doc 04 §4)."""
    if not isinstance(value, str):
        return value
    match = _WRAPPER_RE.match(value)
    if match is None:
        return value
    return evaluate_expr(match.group("body"), context)


def resolve_deep(value: Any, context: dict[str, Any]) -> Any:
    """Đệ quy vào dict/list — dùng cho `with:` có nhiều field, một số field là
    `${...}`, một số là hằng số (doc 04 §4 ví dụ `with: {file: "${inputs.pdf}"}`)."""
    if isinstance(value, dict):
        return {k: resolve_deep(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_deep(v, context) for v in value]
    return resolve(value, context)

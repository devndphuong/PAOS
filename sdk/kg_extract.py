"""Trích entity từ văn bản artifact cho Knowledge Graph (doc 07 §4, P-M5-3).

Thuần logic, không I/O — cùng triết lý `sdk/preference.py`/`sdk/rubric.py`:
hàm `extract_entities()` là **hàm thuần** (cùng `text` → CÙNG kết quả tuyệt
đối, mọi lúc, mọi máy) vì doc 13 M5 exit criterion "Rebuild KG từ replay
event cho kết quả tương đương" đòi hỏi toàn bộ pipeline trích xuất phải là
hàm thuần của event log — không có state ẩn, không gọi random/thời gian hệ
thống, không gọi ra ngoài.

**Quyết định thiết kế (ADR-0028, xem docs/15-adr-log.md):** dùng DANH SÁCH
THUẬT NGỮ ĐÃ BIẾT (khớp chính xác, không phân biệt hoa/thường), KHÔNG dùng
capability LLM mới (vd `text.extract_entities@1`) và KHÔNG dùng heuristic
"mọi từ viết hoa" tổng quát. Lý do ngắn gọn (chi tiết ở ADR-0028):
1. LLM-based: không tất định qua thời gian/model → vi phạm thẳng yêu cầu
   "rebuild từ replay cho kết quả tương đương" ở trên, trừ khi cache tuyệt
   đối mọi lần gọi (không thực tế cho nội dung tự do).
2. "mọi từ viết hoa": độ chính xác thấp, đặc biệt với tiếng Việt (không có
   quy ước viết hoa danh từ riêng nghiêm ngặt như tiếng Anh) — sẽ tạo rất
   nhiều node rác, làm hỏng chất lượng đồ thị (doc 07 §4.4) ngay từ lần
   dùng thật đầu tiên.
Danh sách thuật ngữ đã biết cho ĐỘ CHÍNH XÁC CAO (match sai gần như không
xảy ra) đổi lấy ĐỘ PHỦ THẤP (bỏ sót thuật ngữ chưa có trong danh sách) —
đánh đổi chấp nhận được cho v1 theo P8 (THẬT THÀ hơn là giả vờ có NLU thật)
và P4 (không xây NLU tổng quát khi chưa có 2 ca dùng thật cần nó). Mở rộng
danh sách là việc lặp lại đơn giản, không phải rủi ro kiến trúc — xem
docs/backlog.md BL mới P-M5-3.

Quan hệ suy ra được từ heuristic này CHỈ có 1 loại: mỗi entity được trích ra
từ một artifact có cạnh `learned_from` trỏ tới node `Source` đại diện cho
chính artifact đó (đúng ví dụ doc 07 §4.3: "(MongoDB) -[learned_from]->
(Source: mongodb.pdf)"). KHÔNG suy luận is_a/alternative_to/depends_on...
giữa các entity cùng xuất hiện trong 1 văn bản — việc đó cần hiểu NGỮ NGHĨA
câu văn (NLU thật), ngoài khả năng thật của một bảng tra cứu tất định."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class NodeType(StrEnum):
    """doc 07 §4.1 — 12 loại node, nguyên văn."""

    CONCEPT = "Concept"
    TECHNOLOGY = "Technology"
    TOOL = "Tool"
    PROJECT = "Project"
    ARTIFACT = "Artifact"
    PERSON = "Person"
    PREFERENCE = "Preference"
    WORKFLOW = "Workflow"
    PROVIDER = "Provider"
    ERROR = "Error"
    TEMPLATE = "Template"
    SOURCE = "Source"


class RelationType(StrEnum):
    """doc 07 §4.2 — 13 loại quan hệ, nguyên văn."""

    IS_A = "is_a"
    PART_OF = "part_of"
    USED_IN = "used_in"
    DEPENDS_ON = "depends_on"
    ALTERNATIVE_TO = "alternative_to"
    REPLACED_BY = "replaced_by"
    PREFERS = "prefers"
    AVOIDS = "avoids"
    CAUSES = "causes"
    FIXES = "fixes"
    PRODUCED_BY = "produced_by"
    LEARNED_FROM = "learned_from"
    SIMILAR_TO = "similar_to"


# Cặp quan hệ MÂU THUẪN nếu cùng xuất hiện giữa CÙNG một cặp (src, dst) —
# doc 07 §4.4 "phát hiện mâu thuẫn". Danh sách tối thiểu, mở rộng khi có ca
# dùng thật cần thêm cặp khác (P4) — xem docstring
# `apps/paosd/knowledge_store.py::KnowledgeStore.create_edge` để biết
# KnowledgeExtractor (P-M5-3) hiện CHƯA có đường trích prefers/avoids thật
# từ văn bản tự do (chỉ tạo cạnh learned_from) — cơ chế này có thật, được
# kiểm bằng test gọi thẳng KnowledgeStore, sẵn sàng cho caller tương lai.
CONTRADICTORY_RELATIONS: dict[RelationType, RelationType] = {
    RelationType.PREFERS: RelationType.AVOIDS,
    RelationType.AVOIDS: RelationType.PREFERS,
}

# Danh sách thuật ngữ đã biết -> loại node (doc 07 §4.1). Khớp KHÔNG phân
# biệt hoa/thường trên TOKEN đơn (tách theo `_TOKEN_RE`) — key là dạng viết
# CHUẨN sẽ dùng làm `label` khi tạo node (giữ case gốc dễ đọc, vd "MongoDB"
# thay vì "mongodb"). Danh sách khởi tạo modest, mở rộng dần theo backlog.
KNOWN_TERMS: dict[str, NodeType] = {
    # Technology — cơ sở dữ liệu / ngôn ngữ / runtime
    "MongoDB": NodeType.TECHNOLOGY,
    "PostgreSQL": NodeType.TECHNOLOGY,
    "MySQL": NodeType.TECHNOLOGY,
    "SQLite": NodeType.TECHNOLOGY,
    "Redis": NodeType.TECHNOLOGY,
    "Python": NodeType.TECHNOLOGY,
    "JavaScript": NodeType.TECHNOLOGY,
    "TypeScript": NodeType.TECHNOLOGY,
    "FastAPI": NodeType.TECHNOLOGY,
    "React": NodeType.TECHNOLOGY,
    # Tool
    "Docker": NodeType.TOOL,
    "Ollama": NodeType.TOOL,
    "FFmpeg": NodeType.TOOL,
    "Git": NodeType.TOOL,
    "VSCode": NodeType.TOOL,
    "SearXNG": NodeType.TOOL,
    # Concept
    "Database": NodeType.CONCEPT,
    "Cache": NodeType.CONCEPT,
    "API": NodeType.CONCEPT,
    "Kernel": NodeType.CONCEPT,
    "Embedding": NodeType.CONCEPT,
    "Webhook": NodeType.CONCEPT,
    # Provider (tên model/nhà cung cấp hay gặp trong nội dung sinh ra)
    "OpenAI": NodeType.PROVIDER,
    "Anthropic": NodeType.PROVIDER,
    "GPT": NodeType.PROVIDER,
    "Claude": NodeType.PROVIDER,
}

# Bảng tra không phân biệt hoa/thường, dựng 1 lần lúc import module.
_KNOWN_TERMS_LOWER: dict[str, tuple[str, NodeType]] = {
    term.lower(): (term, node_type) for term, node_type in KNOWN_TERMS.items()
}

# Token = chuỗi ký tự chữ/số liên tiếp (giữ được cả token kiểu "FFmpeg",
# "GPT-4" tách thành "GPT" + "4" — chấp nhận được, "GPT" vẫn khớp).
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ỹ0-9]+")


@dataclass(frozen=True)
class ExtractedEntity:
    label: str  # dạng viết CHUẨN (key của KNOWN_TERMS), không phải dạng xuất hiện trong text
    type: NodeType


def extract_entities(text: str) -> list[ExtractedEntity]:
    """Hàm THUẦN: quét từng token trong `text`, khớp với `KNOWN_TERMS` không
    phân biệt hoa/thường, trả về danh sách entity DUY NHẤT (khử trùng lặp
    trong CÙNG lần gọi), giữ thứ tự xuất hiện đầu tiên — thứ tự này không
    ảnh hưởng kết quả cuối (caller ghi vào KG không phụ thuộc thứ tự), chỉ
    để output tất định dễ so sánh trong test."""
    seen: set[str] = set()
    result: list[ExtractedEntity] = []
    for match in _TOKEN_RE.finditer(text):
        hit = _KNOWN_TERMS_LOWER.get(match.group(0).lower())
        if hit is None:
            continue
        canonical_label, node_type = hit
        if canonical_label in seen:
            continue
        seen.add(canonical_label)
        result.append(ExtractedEntity(label=canonical_label, type=node_type))
    return result


def label_matches(query: str, label: str, aliases: list[str]) -> bool:
    """Khớp không phân biệt hoa/thường trên `label` CHÍNH hoặc bất kỳ alias
    nào — quyết định khử trùng lặp entity (doc 07 §4.4) của module này:
    case-insensitive label + aliases_json là bước bắt đầu đơn giản, đủ
    dùng ở quy mô 1 người dùng (ADR-0011), KHÔNG dùng fuzzy match/embedding
    similarity cho việc dedup (đó là bài toán khác, rủi ro gộp nhầm 2 entity
    khác nhau cao hơn lợi ích, ngoài phạm vi P-M5-3)."""
    q = query.lower()
    if label.lower() == q:
        return True
    return any(a.lower() == q for a in aliases)


# Confidence khởi đầu cho node/edge MỚI tạo từ trích xuất tất định (khớp
# chính xác theo danh sách đã biết) — cao hơn INITIAL_CANDIDATE_CONFIDENCE
# (0.3) của sdk/preference.py vì đây là khớp CHÍNH XÁC (không phải suy luận
# hành vi im lặng), nhưng CHƯA đạt AUTO_APPLY_THRESHOLD (0.75, sdk/preference.py)
# vì mới xuất hiện 1 lần — cùng tinh thần "cần thêm bằng chứng củng cố"
# trước khi PAOS coi một node/edge là chắc chắn. Củng cố lặp lại dùng lại
# `sdk.preference.apply_delta(..., ObservationKind.SILENT_ACCEPT)` — tái sử
# dụng NGUYÊN VẸN ngưỡng hành động + công thức đã có toàn dự án thay vì bịa
# công thức riêng cho KG (nhất quán, dễ hiểu lại 2 năm sau).
KG_INITIAL_NODE_CONFIDENCE = 0.5
KG_INITIAL_EDGE_CONFIDENCE = 0.5

# PAOS — Personal AI Operating System

> Xây dựng một Hệ điều hành AI cá nhân có khả năng học hỏi, cộng tác, tự động hóa và mở rộng vô hạn, hoạt động chủ yếu trên máy cá nhân, với chi phí gần như bằng 0 sau khi hoàn thành nền tảng.
> — [doc 00 §1](docs/00-vision-and-principles.md)

PAOS không phải một chatbot có vỏ đẹp. Model AI chỉ là một lớp mỏng (Provider Layer) bên trong một kiến trúc hệ điều hành thật: Kernel · Scheduler · Event Bus · Capability · Agent · Workflow. Đổi model không sửa Kernel. Tài sản tích lũy sau nhiều năm không phải là code, mà là **Operational Knowledge** — xem [doc 07](docs/07-memory-and-knowledge.md) và [doc 20](docs/20-vision-beyond-v1.md).

**Trạng thái:** giai đoạn thiết kế / Ngày 0. Chưa có bản chạy được. Xem tiến độ ở [doc 13 — Roadmap](docs/13-roadmap-and-milestones.md).

## 1. Tài liệu là nguồn sự thật

README này là cửa vào, không phải đặc tả. Toàn bộ đặc tả nằm ở `docs/`:

| # | Tài liệu | # | Tài liệu |
|---|---|---|---|
| 00 | [Vision & Principles](docs/00-vision-and-principles.md) | 11 | [NFR & SLO](docs/11-nfr-and-slo.md) |
| 01 | [Product Requirements](docs/01-product-requirements.md) | 12 | [Plugin SDK & Marketplace](docs/12-plugin-sdk-and-marketplace.md) |
| 02 | [Architecture](docs/02-architecture.md) | 13 | [Roadmap & Milestones](docs/13-roadmap-and-milestones.md) |
| 03 | [Domain Model & Storage](docs/03-domain-model-and-storage.md) | 14 | [Risk Register](docs/14-risk-register.md) |
| 04 | [Core Contracts](docs/04-core-contracts.md) | 15 | [ADR Log](docs/15-adr-log.md) |
| 05 | [Event Catalog](docs/05-event-catalog.md) | 16 | [Glossary](docs/16-glossary.md) |
| 06 | [Decision Engine & Routing](docs/06-decision-engine-and-routing.md) | 17 | [Contributing & Coding Standards](docs/17-contributing-and-coding-standards.md) |
| 07 | [Memory & Knowledge](docs/07-memory-and-knowledge.md) | 18 | [Ngày 0 & Playbook Triển khai](docs/18-day0-implementation-playbook.md) |
| 08 | [Quality, Review & Testing](docs/08-quality-review-and-testing.md) | 19 | [Prompt Library](docs/19-prompt-library.md) |
| 09 | [Security, Permission & Safety](docs/09-security-permission-safety.md) | 20 | [Vision Beyond v1](docs/20-vision-beyond-v1.md) |
| 10 | [Observability & Explainability](docs/10-observability-and-explainability.md) | | |

Nguyên tắc: **code lệch tài liệu = bug** ([doc 17 §8](docs/17-contributing-and-coding-standards.md)). Nếu bạn thấy README hoặc bất kỳ doc nào mâu thuẫn với code, doc thắng — sửa code hoặc mở PR sửa doc, đừng để lệch âm thầm.

## 2. Bắt đầu nhanh

Dự án đang ở giai đoạn khung — chưa có gì chạy được để cài đặt. Khi Ngày 0 xong ([doc 18](docs/18-day0-implementation-playbook.md)):

```bash
make install   # cài phụ thuộc dev
make ci        # lint + type + arch + coverage + 6 cổng CI
```

Bốn cổng CI không thể tắt ([doc 17 §2](docs/17-contributing-and-coding-standards.md)): Kernel sạch AI · Kernel độc lập · Agent mù provider · Không secret trong log. Nếu bạn thấy mình muốn tắt tạm một cổng để merge nhanh — đó là dấu hiệu dự án bắt đầu chết, không phải dấu hiệu cổng sai.

## 3. Bốn câu hỏi trước khi thêm bất kỳ tính năng nào

Vision rất rộng (Energy Engine, Time Engine, Knowledge Graph, Marketplace...) — rất dễ thêm mãi cho đến khi không còn gì chạy được ([RSK-04, doc 14](docs/14-risk-register.md)). Trước khi viết một dòng code cho ý tưởng mới, trả lời cả 4 câu:

1. **Điều này có giúp PAOS sống được 10 năm nữa không**, hay chỉ giải quyết một vấn đề của hôm nay? ([doc 00 §1](docs/00-vision-and-principles.md))
2. **Nó triển khai được như một Capability/Provider/Plugin mà 0 dòng sửa Kernel không?** Nếu phải sửa Kernel, vì sao — và đã cân nhắc Router/Policy thay vì Kernel chưa? ([P1/P3/P4, doc 00 §5](docs/00-vision-and-principles.md))
3. **Nó có phục vụ trực tiếp một use case bắt buộc (UC1–UC8, [doc 01](docs/01-product-requirements.md)) hoặc exit criteria của milestone hiện tại ([doc 13](docs/13-roadmap-and-milestones.md)) không?** Nếu không, nó thuộc về `docs/backlog.md`, không thuộc về code hôm nay.
4. **Nếu bỏ ý tưởng này trong 6 tháng, điều gì thực sự mất đi?** Nếu câu trả lời mơ hồ, đó chưa phải nhu cầu thật.

Trả lời "không" ở câu 1, 2 hoặc 3 → ghi vào `docs/backlog.md`, không viết code. Đây là cơ chế duy nhất giữ một dự án 12 tháng của một người không chết vì phình to.

## 4. Đóng góp

Xem [doc 17 — Contributing & Coding Standards](docs/17-contributing-and-coding-standards.md). Kể cả khi bạn làm một mình — *đặc biệt* khi bạn làm một mình, vì "bạn của 2 năm sau" là một người khác.

## 5. Giấy phép & phạm vi

Dự án cá nhân, một người, một máy ([ADR-0011](docs/15-adr-log.md)). Không phải sản phẩm SaaS đa người dùng ở v1. Xem [Anti-goals ở doc 00 §6](docs/00-vision-and-principles.md).

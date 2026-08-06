# 17 — Contributing & Coding Standards

**Trạng thái:** v1.0 · Áp dụng cho cả khi bạn làm một mình — đặc biệt là khi bạn làm một mình, vì "bạn của 2 năm sau" là một người khác.

---

## 1. Quy tắc phụ thuộc (bất di bất dịch)

```
apps      →  sdk  →  kernel
agents    →  sdk  →  kernel
providers →  capabilities  →  kernel
plugins   →  sdk (KHÔNG được import kernel.*)
kernel    →  (không phụ thuộc ai)
```

Được cưỡng chế bằng `import-linter` trong CI. Vi phạm = CI đỏ, không merge được.

## 2. Bốn kiểm tra CI không thể bỏ qua

```bash
# 1. Kernel sạch AI (P1 / ADR-0009)
! grep -riE "openai|anthropic|gpt|claude|ollama|comfyui|llm|prompt" kernel/ --include="*.py"

# 2. Kernel độc lập — xóa hết agent/provider vẫn build + test pass (MNT-06)
rm -rf providers/ agents/ plugins/ && pytest tests/kernel/

# 3. Agent mù provider (P3)
! grep -riE "provider_id|model\s*=|api_key" agents/ --include="*.py"

# 4. Không secret trong log (SEC-01)
pytest tests/security/test_redaction.py
```

Nếu một ngày bạn thấy mình muốn tắt một trong bốn kiểm tra này để merge nhanh — **đó chính là khoảnh khắc dự án bắt đầu chết**. Hãy sửa code, đừng sửa CI.

## 3. Chuẩn code Python

```toml
[tool.ruff]     line-length = 100
[tool.mypy]     strict = true          # bắt buộc cho kernel/ và sdk/
[tool.pytest]   addopts = "--strict-markers -q"
```

- **Type hint đầy đủ** ở `kernel/` và `sdk/`. Nơi khác khuyến khích mạnh.
- **Module Kernel < 500 dòng**, hàm < 60 dòng. Vượt → tách. (MNT-08)
- **Không có `except: pass`.** Mọi exception hoặc được xử lý có ý nghĩa, hoặc được bọc thành `PaosError` có mã chuẩn và ném lên. (P8)
- **Không magic number.** Mọi ngưỡng vào file policy hoặc hằng số có tên.
- **Không chuỗi prompt dài trong code.** Prompt ở file riêng có version.
- **Async đúng cách:** không `time.sleep`, không I/O đồng bộ chặn event loop; việc CPU nặng dùng `run_in_executor` hoặc subprocess.
- **Không truy cập `state.db` ngoài `kernel/state/`.** Mọi thứ khác đi qua API/SDK.

## 4. Cấu trúc một lỗi chuẩn

```python
raise PaosError(
    code="PROVIDER_TIMEOUT",              # thuộc danh sách mã chuẩn (doc 04 §1)
    message="ComfyUI không phản hồi sau 180s",
    retryable=True,
    context={"provider_id": "comfyui.flux", "capability": "image.generate@1"},
    hint="Kiểm tra ComfyUI đang chạy ở localhost:8188, hoặc giảm độ phân giải",
)
```

Mọi lỗi hiển thị cho người dùng **bắt buộc** có `hint` hành động được (UX-01). "Đã xảy ra lỗi" là không đạt yêu cầu.

## 5. Quy ước Git

**Nhánh:** `main` (luôn chạy được) · `feat/*` · `fix/*` · `docs/*` · `adr/*`

**Commit (Conventional Commits):**
```
feat(kernel): thêm resource token cho scheduler
fix(provider): xử lý CUDA OOM trong comfyui adapter
docs(adr): ADR-0015 chọn thư viện vector search
refactor(agent): tách prompt ra file riêng
test(contract): thêm conformance cho audio.tts
```

**Quy tắc:** mỗi commit để `main` ở trạng thái chạy được. Không có commit "WIP" trên `main`. Với dự án một người, đây là thứ cho phép bạn nghỉ 2 tháng rồi quay lại mà không sợ.

## 6. Checklist Pull Request (tự review nếu làm một mình)

- [ ] Có vi phạm P1/P3/P4/P10 không? (nếu có → dừng, thiết kế lại)
- [ ] 4 kiểm tra CI ở §2 xanh
- [ ] Có test unit + contract cho phần mới
- [ ] Có Event tương ứng và đã đăng ký schema
- [ ] Lỗi có mã chuẩn + `hint`
- [ ] Có Decision Record nếu code này ra quyết định
- [ ] Idempotent + resume được nếu chạy > 60s
- [ ] Đã cập nhật `docs/` nếu đụng vào contract
- [ ] Có ADR nếu là quyết định kiến trúc
- [ ] Chạy được offline, hoặc fail rõ ràng có hướng dẫn

## 7. Khi nào phải viết ADR

Viết ADR khi quyết định: **khó đảo ngược** · **ảnh hưởng nhiều tầng** · **loại bỏ một hướng đi khác** · **đụng vào 4 contract dài hạn** · **thêm một phụ thuộc ngoài mới**.

Không cần ADR cho: đổi tên biến, sửa bug, tối ưu cục bộ, thêm test.

**Mẹo:** nếu bạn phải giải thích lựa chọn này cho chính mình 2 năm sau → viết ADR. Chi phí 15 phút, tiết kiệm nhiều ngày.

## 8. Quy ước tài liệu

- `docs/` là **nguồn sự thật**, không phải ghi chú phụ. Code lệch tài liệu = bug.
- PR sửa contract mà không sửa `docs/` → CI cảnh báo (MNT-09).
- Mỗi tài liệu có **Trạng thái** ở đầu: `Draft` / `v1.0` / `Deprecated`.
- Viết cho người đọc lại sau 2 năm, không viết cho người đọc hôm nay.
- Ưu tiên bảng và ví dụ cụ thể hơn văn xuôi dài. Ví dụ cụ thể sống lâu hơn lời giải thích trừu tượng.

## 9. Nhịp làm việc đề xuất (cho một người)

| Nhịp | Việc |
|---|---|
| Mỗi phiên code | Kết thúc ở trạng thái chạy được, commit |
| Mỗi tuần | Chạy eval suite · đọc báo cáo "PAOS đã học được gì" · dọn backlog |
| Mỗi milestone | Rà Risk Register · kiểm exit criteria · cập nhật docs |
| Mỗi quý | Chạy "bài kiểm tra 10 năm": thay toàn bộ provider bằng stub, xem Kernel còn khỏe không |
| Mỗi năm | Đọc lại doc 00. Vision còn đúng không? Nếu đổi → ADR, đừng âm thầm trôi dạt |

## 10. Lời nhắc cuối

Dự án này thất bại theo đúng hai cách, cả hai đều âm thầm:

1. **Xây mãi hạ tầng mà không bao giờ dùng được** → chống bằng: mỗi milestone phải cho ra thứ chạy thật (RSK-01).
2. **Đi tắt để nhanh, làm nhòe ranh giới Kernel** → chống bằng: 4 kiểm tra CI ở §2 (RSK-03).

Mọi thứ khác đều sửa được. Hai điều này thì không.

# Kết quả kiểm thử v3 — 06/11/2026

## Đã xác minh

| Phạm vi | Kết quả | Nguồn minh chứng |
|---|---|---|
| Kiểm thử Python tĩnh + hành vi | 26 đạt | Chạy trực tiếp trong phiên làm việc; local.xml |
| Kiểm thử PostgreSQL + FastAPI | 41/41 đạt tổng cộng (26 kiểm tra trên và 15 tích hợp), 28,62 giây | Log Docker người dùng gửi trong chat; Python 3.11.18, PostgreSQL 15-alpine |
| Cảnh báo kiểm thử | 1 cảnh báo Starlette khuyến nghị httpx2 cho TestClient | Cảnh báo thư viện test, không phải thất bại nghiệp vụ |
| JavaScript và DOM giả lập | Đạt hai nhóm kiểm tra | node tests/test_frontend.cjs |
| Cú pháp JavaScript | Đạt | node --check frontend/app.js |
| Cấu hình Compose | Đạt | docker compose config --quiet |

Mười lăm bài tích hợp gồm: thành công và gửi lại, số dư bằng học phí, thiếu tiền/MSSV sai/đã trả, quyền truy cập và cấm tự nhập amount, OTP sai 5 lần, OTP giao dịch khác, worker hết hạn, deadline OTP, mất phản hồi debit, lỗi release rồi khôi phục, INIT gián đoạn, hủy, hai tình huống đồng thời bắt buộc và nhiều lần verify cùng giao dịch.

PostgreSQL trong bộ 41 bài là thật. API dùng FastAPI TestClient trong tiến trình; email được bắt vào outbox giả lập. Bộ này không thay thế kiểm tra Nginx/HTTP/SMTP toàn hệ thống.

## Kiểm thử toàn hệ thống đã đạt

Đã chạy trực tiếp tests/test_concurrency.py qua Nginx, ba service thật, PostgreSQL và Mailpit đang chạy trong Docker. Cả hai trường hợp đạt:

- Cùng tài khoản A thanh toán đồng thời 11,5 triệu và 9,8 triệu trên số dư 15 triệu: một SUCCESS, một FAILED; số dư cuối 3.500.000 đ.
- Tài khoản B và D cùng trả học phí 8,7 triệu: một yêu cầu 201, một 409; số dư lần lượt 20.000.000 và 11.300.000 đ; học phí PAID một lần.
- Thông báo cuối: ALL CONCURRENCY INTEGRATION TESTS PASSED (against a real running Docker stack).

Đối chiếu HTTP sau kiểm thử: trạng thái ba học phí 523H0120/523H0121/523H0123 là PAID/UNPAID/PAID; có hai email xác nhận thành công trong Mailpit; lịch sử phản ánh đúng hai SUCCESS tổng cộng. Không token và token sai đều nhận 401. Web, Swagger và OpenAPI của cả ba service trả 200.

Đã kiểm tra giao diện bằng trình duyệt thực tế: đăng nhập, thông tin người nộp có phone và tra cứu học phí. Ảnh minh chứng trước khi chạy thanh toán nằm tại screenshots/giao-dien.png. Tình huống khôi phục OTP sau tải lại và idempotency của UI đã được kiểm tra bằng JavaScript gốc với DOM/fetch giả lập; không gộp thành tuyên bố đã kiểm thử thủ công mọi luồng UI.

Minh chứng: ../test-results/http-concurrency.txt, ../test-results/live-smoke.json, ../test-results/postgres-suite-summary.json và ../test-results/local-unit.xml.

## Cách chạy lại

Theo README mục 5. Test runner tạo database ibk_test_* riêng và dọn sau mỗi bài; không sửa database demo. Hai bài HTTP+Mailpit dùng dữ liệu seed ứng dụng và thay đổi số dư/học phí.

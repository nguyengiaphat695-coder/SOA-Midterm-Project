# Thay đổi từ ZIP đã được người dùng cung cấp

Ngày: 06/11/2026. Bản hoàn thiện: v3.

- Giữ Python/FastAPI, PostgreSQL, Docker Compose và ba ranh giới dịch vụ.
- Thêm cleanup bền bằng release_pending; worker thử lại khi mở học phí gặp lỗi.
- Phục hồi INIT dở dang; tự đóng giao dịch/OTP hết hạn; điền thời gian kết thúc.
- Khóa điều phối từng giao dịch qua PostgreSQL; bảo vệ trạng thái cuối trước cập nhật cũ.
- HTTP 5xx nội bộ được giữ để thử lại; mất phản hồi debit không bị coi là chưa ghi nợ.
- Đồng bộ hạn OTP với giữ chỗ; cấp OTP kiểm tra lại số dư/điều khoản.
- Cập nhật UI: phone, deadline/countdown, tiếp tục giao dịch, phân trang, chi tiết kết quả; giữ idempotency key khi mất mạng.
- Sửa UTF-8 trong kiểm thử; thêm kiểm thử lỗi và tích hợp PostgreSQL riêng.
- SQL init chỉ chạy một lần vào đúng database; healthcheck/readiness cho service.
- Thêm cấu hình SMTP qua .env.example, ghim phiên bản thư viện và công bố Swagger/OpenAPI qua gateway.
- Đồng bộ tài liệu với code v3; bỏ bảng trọng số điểm tự đặt của bản cũ.

Kết quả chạy xem TEST_RESULTS.md; không xem việc có mã test là bằng chứng tất cả đã chạy.

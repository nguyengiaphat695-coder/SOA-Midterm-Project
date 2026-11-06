# Kịch bản trình diễn và bảo vệ

## Chuẩn bị

Chạy bản v3 trên dữ liệu seed mới; mở web, Mailpit và ba sơ đồ. Bộ test-runner dùng database riêng nên chạy được mà không tiêu thụ dữ liệu demo. Hai bài HTTP+Mailpit làm thay đổi seed; dùng project riêng hoặc chạy trước rồi chuẩn bị lại dữ liệu demo có chủ đích.

## Trình diễn khoảng 10 phút

1. **Kiến trúc, 1 phút:** chỉ gateway, ba service, ba database và SMTP; giải thích sở hữu dữ liệu, REST và vai trò điều phối của Payment.
2. **Nghiệp vụ/dữ liệu, 1 phút:** Use Case, ERD; người nộp khác sinh viên; toàn bộ học phí; unique ledger và trạng thái học phí.
3. **Luồng thành công, 3 phút:** nguyenvana/123456 → 523H0120 → kiểm tra tên/phone/email → điều khoản → OTP Mailpit → thanh toán → số dư 3,5 triệu, PAID, SUCCESS, email biên nhận.
4. **Luồng lỗi, 1 phút:** tra lại khoản đã trả; dùng phaminhc với học phí 9,8 triệu; nhập OTP sai trong giao dịch mới và quan sát thông báo. Không nhầm các thao tác này với test OTP quá 5 lần/hết hạn có trong bộ test.
5. **Tiếp tục giao dịch, 1 phút:** tạo giao dịch khác, nhận OTP rồi tải lại web; chọn Tiếp tục để xác thực hoặc hủy.
6. **Đồng thời, 2 phút:** trình bày kết quả hai bài bắt buộc, đối chiếu số dư + debit + học phí + lịch sử. Giải thích FOR UPDATE, giữ chỗ và transaction ID.
7. **Phục hồi/API, 1 phút:** chỉ kết quả test mất phản hồi debit/lỗi release; giải thích PROCESSING và release_pending; mở Swagger, REST docs.

## Câu hỏi cần trả lời được

- Vì sao không dùng biến Python để khóa số dư? Có nhiều request/tiến trình; bảo vệ dữ liệu phải ở PostgreSQL.
- Nếu một giao dịch trừ tiền rồi Payment dừng? Trạng thái đã PROCESSING; worker khởi động lại, gửi cùng ID; ledger chặn debit lặp, rồi hoàn tất PAID/SUCCESS.
- Nếu thiếu tiền rồi release bị lỗi? FAILED vẫn có release_pending; worker thử lại, chỉ mở học phí sau khi xác nhận không có debit.
- OTP vì sao không trùng và không dùng cho giao dịch khác? Fingerprint unique khi cấp, HMAC gắn transaction ID, deadline và cờ used.
- Nếu bấm xác nhận nhiều lần? Idempotency key khi tạo; khóa từng payment khi xác thực; SUCCESS trả kết quả cũ.
- Có cần nhiều PostgreSQL server không? Phạm vi bài dùng ba database sở hữu riêng trên một server; service giao tiếp API, không viết DB của nhau.
- Có được gọi là ngân hàng thật không? Đây là mô phỏng học tập, dữ liệu và tiền demo. Biên nhận SMTP có thể gửi lặp qua sự cố; tiền/học phí có kiểm soát chống lặp riêng.

Phải mô tả chính xác kết quả đã chạy trong TEST_RESULTS.md; không trình bày bài bị skip hoặc chưa chạy như đã đạt.

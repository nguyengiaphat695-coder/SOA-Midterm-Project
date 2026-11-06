# Phân tích nghiệp vụ và dữ liệu

## 1. Phạm vi và tác nhân

Phân hệ cho người dùng iBanking đã có tài khoản dùng số dư để thanh toán toàn bộ một khoản học phí cho sinh viên TDTU. Người nộp có thể khác sinh viên. Không có đăng ký tài khoản, nạp tiền, chia nhỏ học phí hay cổng ngân hàng thật trong phạm vi đề.

Tác nhân chính: người dùng iBanking/người nộp tiền. Tác nhân hỗ trợ: hệ thống email SMTP. Sinh viên là đối tượng dữ liệu được tra cứu, không bắt buộc có tài khoản đăng nhập hệ thống này.

## 2. Quy tắc nghiệp vụ

| Mã | Quy tắc |
|---|---|
| BR01 | Đăng nhập bằng username/password của tài khoản có sẵn. |
| BR02 | Tên, điện thoại, email người nộp lấy từ tài khoản đăng nhập, không nhận sửa từ yêu cầu thanh toán. |
| BR03 | Nhập MSSV để lấy sinh viên và khoản học phí; không tự nhập tên hoặc số tiền. |
| BR04 | Chỉ thanh toán toàn bộ khoản học phí tồn tại và chưa PAID. |
| BR05 | Số dư phải lớn hơn hoặc bằng số tiền; kiểm tra lại khi xác nhận và khi ghi nợ. |
| BR06 | Xác nhận cần đủ thông tin và đồng ý điều khoản; backend ghi thời điểm đồng ý. |
| BR07 | OTP gắn với transaction ID, gửi tới email người nộp, mã còn hiệu lực không trùng giao dịch khác. |
| BR08 | OTP tối đa 5 phút, không vượt thời hạn giữ chỗ, sử dụng thành công một lần. |
| BR09 | SUCCESS chỉ sau khi debit và PAID được xác nhận; lịch sử và email phản ánh đúng giao dịch. |
| BR10 | Hai giao dịch cùng tài khoản không được chi vượt số dư. |
| BR11 | Nhiều người cùng trả một khoản học phí chỉ được một giao dịch thành công. |
| BR12 | Gửi lại cùng yêu cầu không được trừ lặp; mất phản hồi không đồng nghĩa giao dịch chưa xảy ra. |

Chính sách bổ sung phục vụ triển khai: giữ chỗ tối đa 8 phút; OTP 6 chữ số, sai 5 lần kết thúc giao dịch; cho hủy trước PROCESSING. Các chính sách này hỗ trợ các yêu cầu trên.

## 3. Đặc tả Use Case

| Use Case | Tiền điều kiện | Luồng chính | Nhánh lỗi | Hậu điều kiện |
|---|---|---|---|---|
| UC01 Đăng nhập | Có tài khoản seed | Nhập username/password → kiểm tra hash → cấp JWT 1 giờ | Sai thông tin: 401; sai cấu trúc: 422 | Có phiên xác thực |
| UC02 Xem tài khoản | JWT hợp lệ | Lấy họ tên, phone, email, tài khoản và số dư | Token hết hạn: 401 | Dữ liệu chỉ đọc hiển thị |
| UC03 Tra cứu học phí | JWT hợp lệ | Nhập MSSV → lấy sinh viên, danh sách học phí | MSSV không tồn tại: 404 | Hiển thị số tiền và trạng thái |
| UC04 Tạo thanh toán | Học phí chưa trả, số dư đủ | Backend xác định người nộp/số tiền → tạo transaction → giữ chỗ | Đã PAID/đang giữ: 409; thiếu tiền: 422; lỗi mạng: giữ INIT để khôi phục | PENDING_CONFIRM và giữ chỗ theo transaction |
| UC05 Xác nhận, nhận OTP | Chủ giao dịch, còn thời hạn | Kiểm tra thông tin/điều khoản → kiểm tra số dư → cấp OTP duy nhất → gửi email | SMTP lỗi: thu hồi mã và cho thử lại; hết hạn: 410 | OTP_SENT, mã băm và hạn dùng được lưu |
| UC06 Xác thực, thanh toán | Chủ giao dịch, OTP còn hạn | Kiểm tra OTP → đánh dấu dùng → PROCESSING → debit → PAID → SUCCESS → biên nhận | OTP sai: 422; sai 5 lần: 429; thiếu tiền: FAILED; lỗi mạng: PROCESSING để retry | Kết quả được ghi vào số dư/học phí/lịch sử |
| UC07 Hủy giao dịch | INIT/PENDING_CONFIRM/OTP_SENT | Ghi CANCELLED → thu hồi OTP → mở lại học phí an toàn | Đã PROCESSING/hoàn tất: 409 | Có timestamp và tác vụ cleanup bền nếu lỗi mạng |
| UC08 Lịch sử/tiếp tục | JWT hợp lệ | Phân trang lịch sử của chính mình → xem chi tiết hoặc mở lại OTP | Truy cập giao dịch người khác: 403 | Người dùng theo dõi đúng kết quả |

## 4. Dữ liệu và ràng buộc

| Database / bảng | Vai trò | Khóa và ràng buộc chính |
|---|---|---|
| user_db.users | Danh tính người dùng, thông tin người nộp | UUID PK; username, phone, email duy nhất; mật khẩu băm |
| user_db.accounts | Tài khoản, số dư khả dụng | FK users; account_number duy nhất; balance >= 0; tối đa một tài khoản mặc định/người dùng |
| user_db.account_debits | Bằng chứng ghi nợ và chống trừ lặp | transaction_id PK; FK account/payer; amount > 0 |
| tuition_db.students | Sinh viên TDTU | student_code PK |
| tuition_db.tuition_fees | Học phí theo sinh viên/học kỳ | FK students; UNIQUE(student_code,semester); amount > 0; trạng thái PAID phải có transaction thanh toán |
| payment_db.transactions | Yêu cầu thanh toán, trạng thái, phục hồi | UUID PK; UNIQUE(payer_id,idempotency_key); tối đa một SUCCESS/khoản học phí; deadlines, timestamps, release_pending |
| payment_db.otps | Xác thực giao dịch | FK transaction; mỗi giao dịch tối đa một bản ghi; fingerprint duy nhất; deadline, used, attempts |

Các tham chiếu giữa ba database là liên kết logic được kiểm tra qua API, không tạo FK chéo database. Payment lưu tên sinh viên, học kỳ, số tiền tại thời điểm tạo để giữ nội dung lịch sử. Số tiền biểu diễn VND nguyên bằng NUMERIC(18,0) và Python int.

## 5. Điều kiện nghiệm thu

Thực hiện đủ BR01–BR12; kiểm tra số dư, debit ledger, học phí và lịch sử sau mỗi tình huống. Cài mới theo README phải tạo được schema/seed, chạy giao diện và nhận được OTP/biên nhận. Các sơ đồ trong `diagrams/` và đặc tả REST là thành phần của hồ sơ thiết kế.

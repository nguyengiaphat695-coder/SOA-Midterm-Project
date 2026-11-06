CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE students (
    student_code VARCHAR(20) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    faculty VARCHAR(100),
    major VARCHAR(100)
);
CREATE TABLE tuition_fees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_code VARCHAR(20) NOT NULL REFERENCES students(student_code),
    semester VARCHAR(30) NOT NULL,
    amount NUMERIC(18,0) NOT NULL CHECK (amount > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'UNPAID'
      CHECK (status IN ('UNPAID','PENDING_PAYMENT','PROCESSING','PAID')),
    reserved_by_transaction_id UUID,
    reservation_expires_at TIMESTAMPTZ,
    paid_by_transaction_id UUID UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(student_code,semester),
    CONSTRAINT paid_consistent CHECK (
      (status = 'PAID' AND paid_by_transaction_id IS NOT NULL)
      OR (status <> 'PAID' AND paid_by_transaction_id IS NULL)
    )
);
CREATE INDEX idx_tuition_student ON tuition_fees(student_code);
CREATE INDEX idx_tuition_status ON tuition_fees(status);
INSERT INTO students(student_code,full_name,faculty,major) VALUES
('523H0120','Lê Văn C','Công nghệ Thông tin','Kỹ thuật Phần mềm'),
('523H0121','Phạm Minh D','Công nghệ Thông tin','Khoa học Máy tính'),
('523H0122','Nguyễn Thị E','Công nghệ Thông tin','Hệ thống Thông tin'),
('523H0123','Trần Văn F','Công nghệ Thông tin','Hệ thống Thông tin');
-- Two different unpaid tuition fees allow concurrent debits from the same account.
INSERT INTO tuition_fees(id,student_code,semester,amount,status,paid_by_transaction_id) VALUES
('c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33','523H0120','HK1/2026-2027',11500000,'UNPAID',NULL),
('d0eebc99-9c0b-4ef8-bb6d-6bb9bd380d44','523H0121','HK1/2026-2027',9800000,'UNPAID',NULL),
('e1eebc99-9c0b-4ef8-bb6d-6bb9bd380e77','523H0122','HK2/2025-2026',7400000,'PAID','11111111-1111-4111-8111-111111111111'),
('e2eebc99-9c0b-4ef8-bb6d-6bb9bd380e88','523H0123','HK1/2026-2027',8700000,'UNPAID',NULL);

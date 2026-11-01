CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payer_id UUID NOT NULL, -- cross-service logical user reference
    account_id UUID NOT NULL, -- cross-service logical account reference
    tuition_fee_id UUID NOT NULL, -- cross-service logical tuition reference
    student_code VARCHAR(20) NOT NULL,
    student_name VARCHAR(100) NOT NULL,
    semester VARCHAR(30) NOT NULL,
    amount NUMERIC(18,0) NOT NULL CHECK (amount > 0),
    idempotency_key VARCHAR(100) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'INIT'
        CHECK(status IN ('INIT','PENDING_CONFIRM','OTP_SENT','PROCESSING','SUCCESS','FAILED','EXPIRED','CANCELLED','REVIEW')),
    failure_reason TEXT,
    processing_started_at TIMESTAMPTZ,
    notification_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(payer_id,idempotency_key)
);
CREATE UNIQUE INDEX uq_success_tuition ON transactions(tuition_fee_id) WHERE status='SUCCESS';
CREATE INDEX idx_tx_payer ON transactions(payer_id,created_at DESC);
CREATE INDEX idx_tx_recovery ON transactions(status,processing_started_at);
CREATE TABLE otps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    code_hmac VARCHAR(64) NOT NULL,
    code_fingerprint VARCHAR(64) UNIQUE, -- released upon use or expiration
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    attempts_count INTEGER NOT NULL DEFAULT 0 CHECK(attempts_count>=0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

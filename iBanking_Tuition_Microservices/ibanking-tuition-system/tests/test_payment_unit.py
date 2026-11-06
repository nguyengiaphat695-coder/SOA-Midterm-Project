"""Behavioral regression tests with explicit simulated database/HTTP boundaries."""
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from conftest import load_service


@pytest.fixture
def payment():
    return load_service('payment-service')


@pytest.fixture
def tx(payment):
    return {'id': str(uuid4()), 'payer_id': str(uuid4()), 'account_id': str(uuid4()),
            'tuition_fee_id': str(uuid4()), 'status': 'PROCESSING', 'amount': 11500000,
            'release_pending': True, 'expires_at': payment.now()+timedelta(minutes=8),
            'otp_expires_at': None, 'notification_sent_at': None}


def simulate_db(monkeypatch, payment):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    conn = Mock()
    conn.cursor.return_value = cursor
    @contextmanager
    def fake_db():
        yield conn
    monkeypatch.setattr(payment, 'db', fake_db)
    return cursor


def setup_saga(monkeypatch, payment, tx):
    def set_status(_, status, reason=None):
        tx['status'] = status
    monkeypatch.setattr(payment, 'set_status', set_status)
    monkeypatch.setattr(payment, 'load_tx', lambda _: tx)


def test_cleanup_network_failure_keeps_durable_pending_flag(payment, tx, monkeypatch):
    tx['status'] = 'FAILED'
    cursor = simulate_db(monkeypatch, payment)
    monkeypatch.setattr(payment, 'upstream', Mock(side_effect=[{'debited':False}, HTTPException(503, 'outage')]))
    payment.cleanup(tx)
    assert tx['release_pending'] is True
    cursor.execute.assert_not_called()
    monkeypatch.setattr(payment, 'upstream', Mock(side_effect=[{'debited':False}, {'status':'UNPAID'}]))
    payment.cleanup(tx)
    assert 'release_pending=FALSE' in cursor.execute.call_args.args[0]


def test_cleanup_never_unlocks_a_charged_fee(payment, tx, monkeypatch):
    tx['status'] = 'FAILED'
    cursor = simulate_db(monkeypatch, payment)
    call = Mock(return_value={'debited':True})
    monkeypatch.setattr(payment, 'upstream', call)
    payment.cleanup(tx)
    assert call.call_count == 1
    assert "status='REVIEW'" in cursor.execute.call_args.args[0]


def test_failed_debit_schedules_cleanup(payment, tx, monkeypatch):
    setup_saga(monkeypatch, payment, tx)
    monkeypatch.setattr(payment, 'upstream', Mock(side_effect=[{'status':'PROCESSING'}, HTTPException(422, 'no funds')]))
    cleanup = Mock()
    monkeypatch.setattr(payment, 'cleanup', cleanup)
    payment.run_saga(tx)
    assert tx['status'] == 'FAILED'
    cleanup.assert_called_once_with(tx)


def test_lost_debit_reply_remains_processing(payment, tx, monkeypatch):
    setup_saga(monkeypatch, payment, tx)
    call = Mock(side_effect=[{'status':'PROCESSING'}, HTTPException(503, 'lost acknowledgement')])
    monkeypatch.setattr(payment, 'upstream', call)
    payment.run_saga(tx)
    assert tx['status'] == 'PROCESSING'
    assert call.call_count == 2


def test_retry_after_paid_recovers_same_payment(payment, tx, monkeypatch):
    setup_saga(monkeypatch, payment, tx)
    call = Mock(side_effect=[{'status':'PAID'}, {'status':'ALREADY_DEBITED'}, {'status':'PAID'}])
    monkeypatch.setattr(payment, 'upstream', call)
    payment.run_saga(tx)
    assert tx['status'] == 'SUCCESS'
    assert call.call_args_list[1].kwargs['json']['transaction_id'] == tx['id']


def test_mark_paid_outage_keeps_processing(payment, tx, monkeypatch):
    setup_saga(monkeypatch, payment, tx)
    monkeypatch.setattr(payment, 'upstream', Mock(side_effect=[{}, {}, HTTPException(503, 'outage')]))
    payment.run_saga(tx)
    assert tx['status'] == 'PROCESSING'


@pytest.mark.parametrize('status', ['INIT', 'PENDING_CONFIRM', 'OTP_SENT'])
def test_expiry_runs_without_user_verification(payment, tx, monkeypatch, status):
    tx.update(status=status, expires_at=payment.now()-timedelta(seconds=1), otp_expires_at=payment.now()-timedelta(seconds=1))
    setup_saga(monkeypatch, payment, tx)
    cleanup = Mock()
    monkeypatch.setattr(payment, 'cleanup', cleanup)
    assert payment.expire(tx)['status'] == 'EXPIRED'
    cleanup.assert_called_once()


def test_processing_does_not_expire_after_debit(payment, tx, monkeypatch):
    tx['expires_at'] = payment.now()-timedelta(days=1)
    finish = Mock()
    monkeypatch.setattr(payment, 'set_status', finish)
    assert payment.expire(tx)['status'] == 'PROCESSING'
    finish.assert_not_called()


def test_otp_is_bound_to_transaction(payment):
    assert payment.otp_hash(uuid4(), '123456') != payment.otp_hash(uuid4(), '123456')


@pytest.mark.parametrize('value', ['12345','abcdef','１２３４５６','1234567'])
def test_rejects_invalid_otp_shape(payment, value):
    with pytest.raises(ValidationError):
        payment.OTPBody(otp=value)


def test_rejects_client_controlled_amount_or_payer(payment):
    with pytest.raises(ValidationError):
        payment.NewPayment(tuitionFeeId=uuid4(), amount=1, payer_id=uuid4())


def test_no_terms_cannot_issue_otp(payment):
    with pytest.raises(HTTPException) as caught:
        payment.confirm_payment(uuid4(), payment.ConfirmBody(acceptTerms=False), {'sub':str(uuid4())})
    assert caught.value.status_code == 422


def test_public_api_requires_login(payment):
    client = TestClient(payment.app)
    assert client.get('/api/v1/users/me/transactions').status_code == 401
    assert client.get('/api/v1/payments/'+str(uuid4())).status_code == 401


def test_worker_includes_expiry_and_cleanup(payment, monkeypatch):
    cursor = simulate_db(monkeypatch, payment)
    cursor.fetchall.return_value = []
    payment.reconcile_once()
    sql = cursor.execute.call_args.args[0]
    assert "'INIT','PROCESSING'" in sql
    assert "'PENDING_CONFIRM','OTP_SENT'" in sql
    assert 'release_pending' in sql and 'notification_sent_at IS NULL' in sql


def test_receipt_uses_stable_message_id(payment, monkeypatch):
    smtp = Mock()
    smtp.__enter__ = Mock(return_value=smtp)
    smtp.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(payment.smtplib, 'SMTP', Mock(return_value=smtp))
    payment.send_email('a@example.test', 'receipt', 'done', 'receipt-123')
    message = smtp.send_message.call_args.args[0]
    assert message['Message-ID'] == '<receipt-123@ibanking.demo>'


def test_upstream_http_500_is_retryable(payment, monkeypatch):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.request.return_value = httpx.Response(500, json={'detail':'db unavailable'})
    monkeypatch.setattr(payment.httpx, 'Client', Mock(return_value=client))
    with pytest.raises(HTTPException) as caught:
        payment.upstream('POST', 'http://account', '/debit')
    assert caught.value.status_code == 503

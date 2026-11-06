"""Real PostgreSQL + actual FastAPI endpoints; SMTP outbox is captured in memory.

Creates and drops ONLY unique ibk_test_* databases. Never changes application demo DBs.
Run with TEST_DATABASE_URL pointing to an expendable PostgreSQL admin connection,
or: docker compose --profile test run --build --rm test-runner
"""
import concurrent.futures
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import os
import re

from conftest import ROOT, load_service

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='Requires real PostgreSQL: TEST_DATABASE_URL not set')
FEE_A = 'c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33'
FEE_B = 'd0eebc99-9c0b-4ef8-bb6d-6bb9bd380d44'
FEE_C = 'e2eebc99-9c0b-4ef8-bb6d-6bb9bd380e88'


@pytest.fixture
def env():
    base = os.environ['TEST_DATABASE_URL']
    admin = psycopg2.connect(base)
    admin.autocommit = True
    names, urls, modules, clients, outbox = {}, {}, {}, {}, {}
    secret = 'test_only_32_char_shared_jwt_secret_2026'
    def query(service, statement, params=()):
        with psycopg2.connect(urls[service]) as conn, conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else []
    try:
        for service, script in [('user','02-user-db.sql'),('tuition','03-tuition-db.sql'),('payment','04-payment-db.sql')]:
            name = 'ibk_test_' + service + '_' + uuid4().hex[:12]
            with admin.cursor() as cur:
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            names[service] = name
            dsn = parse_dsn(base)
            dsn['dbname'] = name
            urls[service] = make_dsn(**dsn)
            query(service, (ROOT/'database/init'/script).read_text(encoding='utf-8'))
            module = load_service(service+'-service')
            module.DB_URL, module.JWT_SECRET, module.INTERNAL_TOKEN = urls[service], secret, secret
            modules[service] = module
            clients[service] = TestClient(module.app)
        payment = modules['payment']
        payment.OTP_SECRET = secret
        def upstream(method, base_url, path, **kwargs):
            service = 'user' if base_url == payment.USER_URL else 'tuition'
            response = clients[service].request(method, path, headers={'X-Service-Token':secret}, **kwargs)
            if response.status_code >= 400:
                raise HTTPException(503 if response.status_code >= 500 else response.status_code, response.json()['detail'])
            return response.json()
        payment.upstream = upstream
        payment.send_email = lambda to, subject, body, message_id: outbox.update({message_id:(to,subject,body)})
        def login(name='nguyenvana'):
            response = clients['user'].post('/api/v1/auth/login', json={'username':name,'password':'123456'})
            assert response.status_code == 200, response.text
            return {'Authorization':'Bearer '+response.json()['accessToken']}
        def create(auth, fee=FEE_A, key=None):
            return clients['payment'].post('/api/v1/payments', headers={**auth,'Idempotency-Key':key or str(uuid4())}, json={'tuitionFeeId':fee})
        def otp(auth, tx_id):
            response = clients['payment'].post(f'/api/v1/payments/{tx_id}/confirm', headers=auth, json={'acceptTerms':True})
            assert response.status_code == 200, response.text
            return re.search(r': ([0-9]{6})\n',outbox['otp-'+tx_id][2]).group(1)
        def verify(auth, tx_id, code):
            return clients['payment'].post(f'/api/v1/payments/{tx_id}/verify-otp',headers=auth,json={'otp':code})
        yield SimpleNamespace(payment=payment, modules=modules, clients=clients, query=query, login=login,
                              create=create, otp=otp, verify=verify, upstream=upstream, outbox=outbox)
    finally:
        for client in clients.values():
            client.close()
        for name in names.values():
            assert name.startswith('ibk_test_')
            with admin.cursor() as cur:
                cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close()


def test_full_payment_and_replay(env):
    auth = env.login()
    key = str(uuid4())
    tx = env.create(auth, key=key).json()
    tx_id = tx['transactionId']
    code = env.otp(auth,tx_id)
    response = env.verify(auth,tx_id,code)
    assert response.status_code == 200 and response.json()['status'] == 'SUCCESS'
    assert env.verify(auth,tx_id,'000000').json()['status'] == 'SUCCESS'
    assert env.create(auth,key=key).json()['transactionId'] == tx_id
    assert env.query('user','SELECT balance FROM accounts WHERE account_number=%s',('1020304050',))[0][0] == 3500000
    assert env.query('user','SELECT count(*) FROM account_debits WHERE transaction_id=%s',(tx_id,))[0][0] == 1
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'PAID'
    status,completed,pending = env.query('payment','SELECT status,completed_at,release_pending FROM transactions WHERE id=%s',(tx_id,))[0]
    assert status == 'SUCCESS' and completed and not pending
    assert 'receipt-'+tx_id in env.outbox


def test_exact_balance_can_pay(env):
    env.query('user',"UPDATE accounts SET balance=11500000 WHERE account_number='1020304050'")
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    assert env.verify(auth,tx_id,env.otp(auth,tx_id)).json()['status'] == 'SUCCESS'
    assert env.query('user',"SELECT balance FROM accounts WHERE account_number='1020304050'")[0][0] == 0


def test_missing_student_paid_fee_and_insufficient_funds(env):
    auth = env.login('phaminhc')
    assert env.clients['tuition'].get('/api/v1/tuitions/NO_SUCH_STUDENT',headers=auth).status_code == 404
    assert env.create(auth).status_code == 422
    paid = env.query('tuition',"SELECT id FROM tuition_fees WHERE status='PAID'")[0][0]
    assert env.create(auth,str(paid)).status_code == 409


def test_ownership_and_client_amount_rejected(env):
    auth = env.login()
    other = env.login('tranthib')
    tx_id = env.create(auth).json()['transactionId']
    assert env.clients['payment'].get('/api/v1/payments/'+tx_id,headers=other).status_code == 403
    assert env.clients['payment'].post('/api/v1/payments/'+tx_id+'/confirm',headers=other,json={'acceptTerms':True}).status_code == 403
    assert env.clients['payment'].post('/api/v1/payments',headers={**auth,'Idempotency-Key':str(uuid4())},json={'tuitionFeeId':FEE_A,'amount':1}).status_code == 422
    assert env.clients['user'].post('/api/v1/auth/login',json={'username':'nguyenvana','password':'wrong'}).status_code == 401
    assert env.clients['user'].get('/api/v1/users/me').status_code == 401


def test_wrong_otp_limit_releases_fee(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    code = env.otp(auth,tx_id)
    wrong = '000000' if code != '000000' else '111111'
    for attempt in range(5):
        assert env.verify(auth,tx_id,wrong).status_code == (429 if attempt == 4 else 422)
    assert env.query('payment','SELECT status FROM transactions WHERE id=%s',(tx_id,))[0][0] == 'FAILED'
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'UNPAID'
    assert env.query('user','SELECT count(*) FROM account_debits')[0][0] == 0


def test_otp_for_another_transaction_fails(env):
    auth = env.login()
    a = env.create(auth).json()['transactionId']
    b = env.create(auth,FEE_B).json()['transactionId']
    a_code, b_code = env.otp(auth,a), env.otp(auth,b)
    assert a_code != b_code
    assert env.verify(auth,b,a_code).status_code == 422


def test_worker_expires_abandoned_otp(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    env.otp(auth,tx_id)
    env.query('payment',"UPDATE transactions SET otp_expires_at=now()-interval '1 second' WHERE id=%s",(tx_id,))
    env.payment.reconcile_once()
    row = env.query('payment','SELECT status,completed_at,release_pending FROM transactions WHERE id=%s',(tx_id,))[0]
    assert row[0] == 'EXPIRED' and row[1] and not row[2]
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'UNPAID'


def test_otp_expiry_never_exceeds_reservation(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    env.query('payment',"UPDATE transactions SET expires_at=now()+interval '20 seconds' WHERE id=%s",(tx_id,))
    code = env.otp(auth,tx_id)
    row = env.query('payment','SELECT expires_at,otp_expires_at FROM transactions WHERE id=%s',(tx_id,))[0]
    assert row[1] <= row[0]
    assert len(code) == 6


def test_lost_debit_acknowledgement_recovers_once(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    code = env.otp(auth,tx_id)
    def uncertain(method,base,path,**kwargs):
        result = env.upstream(method,base,path,**kwargs)
        if path.endswith('/debit'):
            raise HTTPException(503,'Acknowledgement lost after commit')
        return result
    env.payment.upstream = uncertain
    assert env.verify(auth,tx_id,code).json()['status'] == 'PROCESSING'
    env.payment.upstream = env.upstream
    env.payment.recover_one(tx_id)
    assert env.query('payment','SELECT status FROM transactions WHERE id=%s',(tx_id,))[0][0] == 'SUCCESS'
    assert env.query('user','SELECT count(*) FROM account_debits WHERE transaction_id=%s',(tx_id,))[0][0] == 1


def test_failed_release_is_retried_by_worker(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    code = env.otp(auth,tx_id)
    env.query('user',"UPDATE accounts SET balance=0 WHERE account_number='1020304050'")
    def broken_release(method,base,path,**kwargs):
        if path.endswith('/release'):
            raise HTTPException(503,'Tuition temporarily offline')
        return env.upstream(method,base,path,**kwargs)
    env.payment.upstream = broken_release
    assert env.verify(auth,tx_id,code).status_code == 422
    assert env.query('payment','SELECT status,release_pending FROM transactions WHERE id=%s',(tx_id,))[0] == ('FAILED',True)
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'PROCESSING'
    env.payment.upstream = env.upstream
    env.payment.reconcile_once()
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'UNPAID'
    assert env.query('payment','SELECT release_pending FROM transactions WHERE id=%s',(tx_id,))[0][0] is False


def test_recover_interrupted_initial_reservation(env):
    auth = env.login()
    def outage(*args,**kwargs):
        if args[2].endswith('/reserve'):
            raise HTTPException(503,'Down during initiation')
        return env.upstream(*args,**kwargs)
    env.payment.upstream = outage
    assert env.create(auth).status_code == 503
    tx_id = str(env.query('payment','SELECT id FROM transactions')[0][0])
    env.payment.upstream = env.upstream
    env.payment.reconcile_once()
    assert env.query('payment','SELECT status FROM transactions WHERE id=%s',(tx_id,))[0][0] == 'PENDING_CONFIRM'


def test_cancel_invalidates_otp_and_opens_fee(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    code = env.otp(auth,tx_id)
    response = env.clients['payment'].post('/api/v1/payments/'+tx_id+'/cancel',headers=auth)
    assert response.json()['status'] == 'CANCELLED'
    assert env.verify(auth,tx_id,code).status_code == 409
    assert env.query('tuition','SELECT status FROM tuition_fees WHERE id=%s',(FEE_A,))[0][0] == 'UNPAID'


def test_same_account_concurrent_spend(env):
    auth = env.login()
    a,b = [env.create(auth,fee).json()['transactionId'] for fee in (FEE_A,FEE_B)]
    ca,cb = env.otp(auth,a),env.otp(auth,b)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(env.verify,auth,a,ca),pool.submit(env.verify,auth,b,cb)]
        [f.result() for f in futures]
    states = env.query('payment','SELECT status FROM transactions ORDER BY status')
    assert states == [('FAILED',),('SUCCESS',)]
    balance = env.query('user',"SELECT balance FROM accounts WHERE account_number='1020304050'")[0][0]
    assert balance in (3500000,5200000)
    assert env.query('user','SELECT count(*) FROM account_debits')[0][0] == 1
    assert env.query('tuition',"SELECT count(*) FROM tuition_fees WHERE id IN (%s,%s) AND status='PAID'",(FEE_A,FEE_B))[0][0] == 1


def test_two_accounts_concurrently_pay_one_fee(env):
    auths = [env.login('tranthib'),env.login('doquangd')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda auth:env.create(auth,FEE_C),auths))
    assert sorted(r.status_code for r in responses) == [201,409]
    winner = next(i for i,r in enumerate(responses) if r.status_code==201)
    tx_id = responses[winner].json()['transactionId']
    assert env.verify(auths[winner],tx_id,env.otp(auths[winner],tx_id)).json()['status'] == 'SUCCESS'
    env.payment.reconcile_once()
    assert env.query('user','SELECT count(*) FROM account_debits')[0][0] == 1
    assert env.query('payment',"SELECT count(*) FROM transactions WHERE status='SUCCESS'")[0][0] == 1
    assert env.query('payment',"SELECT count(*) FROM transactions WHERE status='FAILED' AND release_pending")[0][0] == 0


def test_same_transaction_parallel_verification(env):
    auth = env.login()
    tx_id = env.create(auth).json()['transactionId']
    code = env.otp(auth,tx_id)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: env.verify(auth,tx_id,code),range(2)))
    assert all(r.status_code==200 and r.json()['status']=='SUCCESS' for r in results)
    assert env.query('user','SELECT count(*) FROM account_debits WHERE transaction_id=%s',(tx_id,))[0][0] == 1
    assert env.query('payment','SELECT used FROM otps WHERE transaction_id=%s',(tx_id,))[0][0] is True

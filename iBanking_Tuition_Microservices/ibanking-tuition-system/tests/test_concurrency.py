"""True HTTP+PostgreSQL+Mailpit integration checks. Requires fresh `docker compose up -d --build`.
Run once on seeded DB, before manually paying the sample fees. Destructive to sample balances.
"""
import concurrent.futures
import os
import re
import time
import uuid
import httpx

BASE=os.getenv('BASE_URL','http://localhost:8080')
MAIL=os.getenv('MAIL_URL','http://localhost:8025')
FEE_A='c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33' # 11.5m
FEE_B='d0eebc99-9c0b-4ef8-bb6d-6bb9bd380d44' # 9.8m
FEE_C='e2eebc99-9c0b-4ef8-bb6d-6bb9bd380e88' # 8.7m

def login(username):
    r=httpx.post(BASE+'/api/v1/auth/login',json={'username':username,'password':'123456'},timeout=15)
    assert r.status_code==200, (username,r.status_code,r.text)
    return {'Authorization':'Bearer '+r.json()['accessToken']}

def make_payment(auth,fee):
    headers={**auth,'Idempotency-Key':str(uuid.uuid4())}
    return httpx.post(BASE+'/api/v1/payments',headers=headers,json={'tuitionFeeId':fee},timeout=15)

def get_otp(tx_id):
    # Mailpit API; only the local sandbox inbox, never a real mailbox or user email.
    for _ in range(30):
        listing=httpx.get(MAIL+'/api/v1/messages',params={'limit':100},timeout=10)
        listing.raise_for_status()
        for x in listing.json().get('messages',[]):
            if 'OTP' not in x.get('Subject','').upper(): continue
            msg=httpx.get(MAIL+'/api/v1/message/'+x['ID'],timeout=10).json()
            contents=msg.get('Text','')
            if tx_id in contents:
                match=re.search(r':\s*(\d{6})\s*\n',contents)
                assert match, 'Email not containing 6 digit OTP: '+contents
                return match.group(1)
        time.sleep(.5)
    raise AssertionError('Không thấy OTP của transaction '+tx_id+' trong Mailpit')

def send_otp(auth,tx_id):
    r=httpx.post(BASE+f'/api/v1/payments/{tx_id}/confirm',headers=auth,json={'acceptTerms':True},timeout=15)
    assert r.status_code==200, (r.status_code,r.text)
    assert r.json()['status']=='OTP_SENT'
    return get_otp(tx_id)

def verify(auth,tx_id,code):
    return httpx.post(BASE+f'/api/v1/payments/{tx_id}/verify-otp',headers=auth,json={'otp':code},timeout=30)

def status(auth,tx_id):
    r=httpx.get(BASE+f'/api/v1/payments/{tx_id}',headers=auth,timeout=15)
    r.raise_for_status()
    return r.json()['status']

def await_final(auth,tx_id):
    for _ in range(30):
        state=status(auth,tx_id)
        if state in ('SUCCESS','FAILED','REVIEW','EXPIRED','CANCELLED'): return state
        time.sleep(1)
    raise AssertionError('Giao dịch chưa kết thúc sau 30s: '+tx_id)

def test_same_account_double_spend():
    print('\nCASE 1: One 15m account, two different fees (11.5m + 9.8m), both OTP approved in parallel')
    auth=login('nguyenvana')
    start=httpx.get(BASE+'/api/v1/users/me',headers=auth).json()['balance']
    assert start==15000000, 'Tests require fresh seed. Run docker compose down -v (deletes demo data).'
    a=make_payment(auth,FEE_A);b=make_payment(auth,FEE_B)
    assert a.status_code==201 and b.status_code==201,(a.text,b.text)
    a_id=a.json()['transactionId'];b_id=b.json()['transactionId']
    a_code=send_otp(auth,a_id);b_code=send_otp(auth,b_id)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        x=executor.submit(verify,auth,a_id,a_code)
        y=executor.submit(verify,auth,b_id,b_code)
        out=(x.result(),y.result())
    print('Verify responses:',[(r.status_code,r.json().get('status',r.json().get('detail'))) for r in out])
    states=[await_final(auth,a_id),await_final(auth,b_id)]
    balance=httpx.get(BASE+'/api/v1/users/me',headers=auth).json()['balance']
    assert sorted(states)==['FAILED','SUCCESS'],states
    assert balance in (15000000-11500000,15000000-9800000),balance
    assert balance>=0
    print('PASS CASE 1:',states,'final balance',balance)

def test_two_accounts_same_tuition():
    print('\nCASE 2: Two account owners concurrently pay the SAME fee, only one reserved/paid')
    b=login('tranthib');d=login('doquangd')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        x=executor.submit(make_payment,b,FEE_C)
        y=executor.submit(make_payment,d,FEE_C)
        out=(x.result(),y.result())
    codes=sorted(r.status_code for r in out)
    assert codes==[201,409],[(r.status_code,r.text) for r in out]
    winner=0 if out[0].status_code==201 else 1
    winning_auth=[b,d][winner];winning_tx=out[winner].json()['transactionId']
    otp=send_otp(winning_auth,winning_tx)
    result=verify(winning_auth,winning_tx,otp)
    assert result.status_code==200,(result.status_code,result.text)
    assert await_final(winning_auth,winning_tx)=='SUCCESS'
    balances=[httpx.get(BASE+'/api/v1/users/me',headers=a).json()['balance'] for a in (b,d)]
    assert sorted(balances)==[11300000,20000000],balances
    paid=httpx.get(BASE+'/api/v1/tuitions/523H0123',headers=b).json()['tuitionFees'][0]
    assert paid['status']=='PAID'
    print('PASS CASE 2:',codes,'balances',balances,'tuition',paid['status'])

if __name__=='__main__':
    test_same_account_double_spend()
    test_two_accounts_same_tuition()
    print('\nALL CONCURRENCY INTEGRATION TESTS PASSED (against a real running Docker stack).')

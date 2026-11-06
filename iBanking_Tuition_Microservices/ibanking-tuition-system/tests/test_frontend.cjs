// Original browser code with simulated DOM/fetch. Run: node tests/test_frontend.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const script = fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
const profile = {id:'payer-1',fullName:'Nguyễn Văn A',phone:'0901234567',email:'user.a@demo.local',balance:15000000};
const tx = {transactionId:'tx-1',tuitionFeeId:'fee-1',payerId:'payer-1',mssv:'523H0120',studentName:'Sinh viên demo',semester:'HK1',amount:11500000,status:'OTP_SENT',createdAt:new Date().toISOString(),expiresAt:new Date(Date.now()+480000).toISOString(),otpExpiresAt:new Date(Date.now()+300000).toISOString()};

function environment(saved={}) {
  const nodes = new Map(), storage = new Map(Object.entries(saved)), calls=[];
  function element(id) {
    if(!nodes.has(id)) nodes.set(id,{textContent:'',innerHTML:'',value:'',checked:false,disabled:false,children:[],
      classList:{add(){},remove(){},toggle(){}},
      replaceChildren(){this.children=[];},appendChild(child){this.children.push(child);},
      get lastChild(){if(!this.cell)this.cell={appendChild(child){this.child=child;}};return this.cell;}});
    return nodes.get(id);
  }
  let failCreate=false;
  const context=vm.createContext({console,crypto,Date,
    document:{getElementById:element,createElement:()=>element('node-'+nodes.size)},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    setInterval:()=>1,clearInterval(){},setTimeout,
    fetch:async(url,options={})=>{
      calls.push({url,options}); let data;
      if(url==='/api/v1/users/me')data=profile;
      else if(url.startsWith('/api/v1/users/me/transactions'))data={items:[tx],total:1};
      else if(url==='/api/v1/payments'&&options.method==='POST') {
        if(failCreate){failCreate=false;throw new Error('lost reply');}
        data={...tx,status:'PENDING_CONFIRM'};
      } else if(url==='/api/v1/payments/tx-1')data=tx;
      else throw new Error('Unexpected URL '+url);
      return {ok:true,status:200,json:async()=>data};
    }});
  vm.runInContext(script,context);
  return {context,storage,nodes,calls,element,failNextCreate:()=>{failCreate=true;}};
}

(async()=>{
  const e=environment({ibanking_token:'jwt',ibanking_tx:'tx-1'});
  await new Promise(resolve=>setTimeout(resolve,10));
  assert(e.element('user-phone').textContent.includes(profile.phone));
  assert.equal(vm.runInContext('currentTx.transactionId',e.context),'tx-1');
  assert(e.element('history-body').children[0].lastChild.child.onclick);
  assert.equal(e.element('verify-btn').disabled,true);
  e.element('otp-input').value='123456';e.element('otp-input').oninput();
  assert.equal(e.element('verify-btn').disabled,false);
  e.element('otp-input').value='12345';e.element('otp-input').oninput();
  assert.equal(e.element('verify-btn').disabled,true);
  console.log('PASS: read-only phone, OTP restoration, history action, six-digit button validation.');

  const retry=environment();
  vm.runInContext(`token='jwt';user=${JSON.stringify(profile)};`,retry.context);
  retry.failNextCreate();
  await vm.runInContext("createPayment('fee-1')",retry.context);
  await vm.runInContext("createPayment('fee-1')",retry.context);
  const posts=retry.calls.filter(c=>c.url==='/api/v1/payments');
  assert.equal(posts.length,2);
  assert.equal(posts[0].options.headers['Idempotency-Key'],posts[1].options.headers['Idempotency-Key']);
  assert(retry.element('confirm-summary').innerHTML.includes(profile.phone));
  assert.equal(retry.element('send-otp-btn').disabled,true);
  console.log('PASS: lost-reply retry reuses its idempotency key; confirmation includes phone and requires terms.');
})().catch(error=>{console.error(error);process.exitCode=1;});

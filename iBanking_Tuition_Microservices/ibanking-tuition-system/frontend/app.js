'use strict';
let token = sessionStorage.getItem('ibanking_token') || '';
let user = null, currentTx = null, page = 1, pageCount = 1, timer = null, pollId = null, epoch = 0;
const el = id => document.getElementById(id);
const money = n => Number(n).toLocaleString('vi-VN') + ' đ';
const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const show = id => el(id).classList.remove('hidden');
const hide = id => el(id).classList.add('hidden');
const statusNames = {INIT:'Đang khởi tạo', PENDING_CONFIRM:'Chờ xác nhận', OTP_SENT:'Chờ OTP', PROCESSING:'Đang xử lý', SUCCESS:'Thành công', FAILED:'Thất bại', EXPIRED:'Hết hạn', CANCELLED:'Đã hủy', REVIEW:'Cần đối soát', UNPAID:'Chưa thanh toán', PENDING_PAYMENT:'Đang giữ chỗ', PAID:'Đã thanh toán'};
const date = value => value ? new Date(value).toLocaleString('vi-VN') : '—';
function alertMsg(message, success=true) { el('alert').textContent=message; el('alert').classList.toggle('error',!success); show('alert'); }
async function api(path, {method='GET', body, auth=true, headers={}}={}) {
  const h={...headers}; if(body!==undefined) h['Content-Type']='application/json'; if(auth&&token) h.Authorization=`Bearer ${token}`;
  let response;
  try { response=await fetch(path,{method,headers:h,body:body===undefined?undefined:JSON.stringify(body)}); }
  catch { throw new Error('Mất kết nối. Hãy xem lịch sử hoặc thử lại cùng yêu cầu trước khi tạo thanh toán khác.'); }
  const data=await response.json().catch(()=>({}));
  if(!response.ok) {
    if(response.status===401&&auth) logout(false);
    const error=new Error(typeof data.detail==='string'?data.detail:`Dữ liệu không hợp lệ hoặc dịch vụ gặp lỗi (HTTP ${response.status}).`);
    error.status=response.status; throw error;
  }
  return data;
}
function clearDialogs() { clearInterval(timer); hide('confirm-modal'); hide('otp-modal'); hide('detail-modal'); }
function remember(tx) { currentTx=tx; sessionStorage.setItem('ibanking_tx',tx.transactionId); }
function forget(tx=currentTx) {
  if(tx?.tuitionFeeId&&user) sessionStorage.removeItem(`ibanking_key_${user.id}_${tx.tuitionFeeId}`);
  currentTx=null; sessionStorage.removeItem('ibanking_tx'); clearInterval(timer); pollId=null;
}
async function refreshProfile() {
  const session=epoch, r=await api('/api/v1/users/me'); if(session!==epoch) return;
  user=r; el('user-name').textContent=r.fullName; el('user-email').textContent=r.email;
  el('user-phone').textContent='Điện thoại: '+r.phone; el('user-balance').textContent='Số dư: '+money(r.balance);
  show('user-box'); show('tuition-panel'); show('history-panel'); hide('login-panel');
}
async function login() {
  el('login-btn').disabled=true;
  try {
    const data=await api('/api/v1/auth/login',{method:'POST',auth:false,body:{username:el('username').value.trim(),password:el('password').value}});
    epoch++; token=data.accessToken; sessionStorage.setItem('ibanking_token',token); page=1;
    await refreshProfile(); await loadHistory(); await restorePending(); alertMsg('Đăng nhập thành công.');
  } catch(error) { alertMsg(error.message,false); }
  finally { el('login-btn').disabled=false; }
}
async function searchTuition() {
  const mssv=el('mssv').value.trim(); if(!mssv) { alertMsg('Vui lòng nhập MSSV.',false); return; }
  el('search-btn').disabled=true; const session=epoch;
  try {
    const data=await api('/api/v1/tuitions/'+encodeURIComponent(mssv)); if(session!==epoch) return;
    el('student-info').innerHTML=`<strong>Sinh viên:</strong> ${escapeHtml(data.student.full_name)} • <strong>MSSV:</strong> ${escapeHtml(data.student.student_code)} • <strong>Khoa:</strong> ${escapeHtml(data.student.faculty)}`;
    show('student-info'); const tbody=el('fees-body'); tbody.replaceChildren();
    for(const fee of data.tuitionFees) {
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${escapeHtml(fee.semester)}</td><td>${money(fee.amount)}</td><td>${escapeHtml(statusNames[fee.status]||fee.status)}</td><td></td>`;
      if(fee.status==='UNPAID') {
        const button=document.createElement('button'); button.className='small'; button.textContent='Thanh toán';
        button.disabled=fee.amount>user.balance; if(button.disabled) button.title='Số dư không đủ';
        button.onclick=()=>createPayment(fee.id,button); tr.lastChild.appendChild(button);
      } else tr.lastChild.textContent=fee.status==='PAID'?'Hoàn tất':'Xem giao dịch đang chờ trong lịch sử';
      tbody.appendChild(tr);
    }
    if(!data.tuitionFees.length) tbody.innerHTML='<tr><td colspan="4">Sinh viên chưa có khoản học phí.</td></tr>';
  } catch(error) { el('fees-body').replaceChildren(); hide('student-info'); alertMsg(error.message,false); }
  finally { el('search-btn').disabled=false; }
}
function openConfirm(tx) {
  clearDialogs(); remember(tx);
  el('confirm-summary').innerHTML=`<span>Người nộp:</span><strong>${escapeHtml(user.fullName)}</strong><span>Điện thoại:</span><strong>${escapeHtml(user.phone)}</strong><span>Email:</span><strong>${escapeHtml(user.email)}</strong><span>Sinh viên:</span><strong>${escapeHtml(tx.studentName)}</strong><span>MSSV:</span><strong>${escapeHtml(tx.mssv)}</strong><span>Học kỳ:</span><strong>${escapeHtml(tx.semester)}</strong><span>Số tiền:</span><strong>${money(tx.amount)}</strong><span>Số dư:</span><strong>${money(user.balance)}</strong><span>Hạn xác nhận:</span><strong>${escapeHtml(date(tx.expiresAt))}</strong>`;
  el('terms-check').checked=false; el('send-otp-btn').disabled=true; show('confirm-modal');
}
async function createPayment(tuitionFeeId,button) {
  if(button) button.disabled=true;
  const keyName=`ibanking_key_${user.id}_${tuitionFeeId}`;
  let key=sessionStorage.getItem(keyName); if(!key) {key=crypto.randomUUID(); sessionStorage.setItem(keyName,key);}
  try {
    await refreshProfile();
    const tx=await api('/api/v1/payments',{method:'POST',headers:{'Idempotency-Key':key},body:{tuitionFeeId}});
    await resumeTx(tx);
  } catch(error) {
    if([400,404,409,410,422].includes(error.status)) sessionStorage.removeItem(keyName);
    alertMsg(error.message,false);
  } finally { if(button) button.disabled=false; await loadHistory(); }
}
function openOtp(tx) {
  clearDialogs(); remember(tx); el('otp-target').textContent=user.email; el('otp-input').value=''; show('otp-modal');
  const tick=()=>{
    const remaining=Math.max(0,Math.ceil((new Date(tx.otpExpiresAt).getTime()-Date.now())/1000));
    el('otp-countdown').textContent=remaining?`Còn ${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,'0')} để xác thực.`:'OTP đã hết hạn. Hãy đóng và tạo giao dịch mới.';
    el('verify-btn').disabled=!remaining||!(/^[0-9]{6}$/.test(el('otp-input').value.trim()));
    if(!remaining) clearInterval(timer);
  };
  tick(); timer=setInterval(tick,1000);
}
async function sendOtp() {
  if(!el('terms-check').checked||!currentTx) return;
  el('send-otp-btn').disabled=true;
  try {
    await api(`/api/v1/payments/${currentTx.transactionId}/confirm`,{method:'POST',body:{acceptTerms:true}});
    const tx=await api('/api/v1/payments/'+currentTx.transactionId); openOtp(tx); alertMsg('OTP đã được gửi tới email người nộp.');
  } catch(error) { alertMsg(error.message,false); el('send-otp-btn').disabled=!el('terms-check').checked; }
}
async function refreshAll() { await refreshProfile(); await loadHistory(); if(el('mssv').value.trim()) await searchTuition(); }
function showDetails(tx) {
  clearDialogs();
  el('detail-summary').innerHTML=`<span>Mã giao dịch:</span><strong>${escapeHtml(tx.transactionId)}</strong><span>Trạng thái:</span><strong>${escapeHtml(statusNames[tx.status])}</strong><span>Sinh viên:</span><strong>${escapeHtml(tx.studentName)} (${escapeHtml(tx.mssv)})</strong><span>Số tiền:</span><strong>${money(tx.amount)}</strong><span>Ngày tạo:</span><strong>${escapeHtml(date(tx.createdAt))}</strong><span>Kết thúc:</span><strong>${escapeHtml(date(tx.completedAt))}</strong><span>Thông báo:</span><strong>${escapeHtml(tx.failureReason||(tx.releasePending&&['FAILED','EXPIRED','CANCELLED'].includes(tx.status)?'Đang mở lại học phí. Vui lòng chờ hệ thống khôi phục.':'—'))}</strong>`;
  show('detail-modal');
}
async function resumeTx(tx) {
  if(tx.status==='PENDING_CONFIRM') openConfirm(tx);
  else if(tx.status==='OTP_SENT') openOtp(tx);
  else if(['INIT','PROCESSING'].includes(tx.status)) {remember(tx); alertMsg('Giao dịch đang xử lý. Vui lòng chờ, không tạo thanh toán khác.'); pollPayment(tx.transactionId);}
  else {forget(tx); showDetails(tx); await refreshAll();}
}
async function viewTx(id) {
  try {await refreshProfile(); await resumeTx(await api('/api/v1/payments/'+id));}
  catch(error) {alertMsg(error.message,false);}
}
async function restorePending() {
  const id=sessionStorage.getItem('ibanking_tx'); if(id) await viewTx(id);
}
async function verifyOtp() {
  if(!currentTx||!(/^[0-9]{6}$/.test(el('otp-input').value.trim()))) return;
  const id=currentTx.transactionId; el('verify-btn').disabled=true;
  try {
    const tx=await api(`/api/v1/payments/${id}/verify-otp`,{method:'POST',body:{otp:el('otp-input').value.trim()}});
    clearDialogs(); await resumeTx(tx);
    if(tx.status==='SUCCESS') alertMsg('Thanh toán thành công. Số dư, học phí và lịch sử đã được cập nhật.');
  } catch(error) {
    alertMsg(error.message,false);
    try {
      const tx=await api('/api/v1/payments/'+id);
      if(tx.status!=='OTP_SENT') {clearDialogs(); await resumeTx(tx);}
    } catch {}
    await loadHistory();
  } finally {el('verify-btn').disabled=!(/^[0-9]{6}$/.test(el('otp-input').value.trim()));}
}
async function pollPayment(id) {
  if(pollId===id) return; pollId=id; const session=epoch;
  for(let attempt=0;attempt<60&&pollId===id&&session===epoch;attempt++) {
    await new Promise(resolve=>setTimeout(resolve,3000)); if(session!==epoch||pollId!==id) break;
    try {
      const tx=await api('/api/v1/payments/'+id);
      if(!['INIT','PROCESSING'].includes(tx.status)) {pollId=null; await resumeTx(tx); await refreshAll(); return;}
      if(attempt%3===0) await loadHistory();
    } catch {}
  }
  if(pollId===id) {pollId=null; alertMsg('Giao dịch vẫn đang xử lý. Bạn có thể theo dõi tại lịch sử.');}
}
async function cancelPayment() {
  if(!currentTx) return; const id=currentTx.transactionId;
  try {const tx=await api(`/api/v1/payments/${id}/cancel`,{method:'POST'}); clearDialogs(); forget(tx); alertMsg('Đã hủy giao dịch.'); await refreshAll();}
  catch(error) {alertMsg(error.message,false); if([409,410].includes(error.status)) {clearDialogs(); forget(); await refreshAll();}}
}
async function loadHistory() {
  if(!token) return; const session=epoch;
  try {
    const hist=await api(`/api/v1/users/me/transactions?page=${page}&size=10`); if(session!==epoch) return;
    pageCount=Math.max(1,Math.ceil(hist.total/10)); el('page-label').textContent=`Trang ${page}/${pageCount} • ${hist.total} giao dịch`;
    el('prev-page').disabled=page<=1; el('next-page').disabled=page>=pageCount;
    const body=el('history-body'); body.replaceChildren();
    if(!hist.items.length) body.innerHTML='<tr><td colspan="6">Chưa có giao dịch.</td></tr>';
    for(const tx of hist.items) {
      const tr=document.createElement('tr');
      tr.innerHTML=`<td>${escapeHtml(date(tx.createdAt))}</td><td title="${escapeHtml(tx.transactionId)}">${escapeHtml(tx.transactionId.slice(0,8))}…</td><td>${escapeHtml(tx.mssv)}</td><td>${money(tx.amount)}</td><td>${escapeHtml(statusNames[tx.status]||tx.status)}</td><td></td>`;
      const button=document.createElement('button'); button.className='small secondary';
      button.textContent=['PENDING_CONFIRM','OTP_SENT'].includes(tx.status)?'Tiếp tục':'Chi tiết'; button.onclick=()=>viewTx(tx.transactionId);
      tr.lastChild.appendChild(button); body.appendChild(tr);
    }
  } catch(error) {alertMsg(error.message,false);}
}
function logout(notify=true) {
  epoch++; token=''; user=null; currentTx=null; pollId=null; clearDialogs(); sessionStorage.removeItem('ibanking_token'); sessionStorage.removeItem('ibanking_tx');
  for(const id of ['user-box','tuition-panel','history-panel','student-info']) hide(id);
  el('fees-body').replaceChildren(); el('history-body').replaceChildren(); show('login-panel');
  if(notify) alertMsg('Đã đăng xuất.');
}
el('login-btn').onclick=login; el('search-btn').onclick=searchTuition; el('history-btn').onclick=refreshAll;
el('logout-btn').onclick=()=>logout(); el('terms-check').onchange=()=>{el('send-otp-btn').disabled=!el('terms-check').checked;};
el('send-otp-btn').onclick=sendOtp; el('verify-btn').onclick=verifyOtp; el('cancel-confirm').onclick=cancelPayment; el('cancel-otp').onclick=cancelPayment;
el('close-detail').onclick=()=>hide('detail-modal'); el('close-otp').onclick=()=>{clearDialogs(); loadHistory();};
el('prev-page').onclick=()=>{if(page>1) {page--; loadHistory();}}; el('next-page').onclick=()=>{if(page<pageCount) {page++; loadHistory();}};
el('otp-input').oninput=()=>{el('verify-btn').disabled=!currentTx||Date.now()>=new Date(currentTx.otpExpiresAt).getTime()||!(/^[0-9]{6}$/.test(el('otp-input').value.trim()));};
el('password').onkeydown=e=>{if(e.key==='Enter') login();}; el('mssv').onkeydown=e=>{if(e.key==='Enter') searchTuition();};
if(token) refreshProfile().then(loadHistory).then(restorePending).catch(()=>logout(false));

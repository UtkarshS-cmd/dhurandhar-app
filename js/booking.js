
import {
  fetchCities, fetchDates, fetchTheaters, fetchShows, fetchSeats,
  createHold, confirmBooking
} from './api.js';

const state = {
  step: 1,
  cityId: null,
  theaterId: null,
  showId: null,
  date: '',
  selectedTime: '',
  seats: [],
  selectedSeatIds: new Set(),
  ticketCount: 2,
  category: 'Silver',
  hold: null,
  loading: false,
  lastFocused: null
};

const $ = (id) => document.getElementById(id);

function setStatus(message='', type='') {
  let el=$('booking-status');
  if(!el){
    el=document.createElement('div');
    el.id='booking-status';
    el.setAttribute('role','status');
    $('modalBox')?.prepend(el);
  }
  el.className=`booking-status ${type}`;
  el.textContent=message;
}

function setButtonLoading(button, loading, label='Working…') {
  if(!button) return;
  if(loading){
    button.dataset.originalText=button.textContent;
    button.textContent=label;
    button.disabled=true;
  } else {
    button.textContent=button.dataset.originalText||button.textContent;
    button.disabled=false;
  }
}

function setErr(rowId, inputId, ok, message) {
  const row=$(rowId), input=$(inputId);
  if(!row || !input)return;
  row.classList.toggle('has-error',!ok);
  input.classList.toggle('error',!ok);
  input.classList.toggle('valid',ok);
  const msg=row.querySelector('.err-msg');
  if(msg && message) msg.textContent=message;
}

function goStep(step){
  state.step=step;
  for(let i=1;i<=5;i++) $(`mpanel${i}`)?.classList.toggle('active',i===step);
  for(let i=1;i<=4;i++){
    const tab=$(`stab${i}`);
    tab?.classList.toggle('active',i===step);
    tab?.classList.toggle('done',i<step);
  }
  $('modalBox').scrollTop=0;
}

async function loadStep2Data(){
  const city=$('f-city'), date=$('f-date');
  try{
    city.innerHTML='<option value="">Loading cities…</option>';
    date.innerHTML='<option value="">Loading dates…</option>';
    const [cities,dates]=await Promise.all([fetchCities(),fetchDates()]);
    city.innerHTML='<option value="">— Choose your city —</option>'+cities.map(c=>`<option value="${c.id}">${c.name}</option>`).join('');
    date.innerHTML='<option value="">— Select date —</option>'+dates.map(d=>`<option value="${d.value}">${d.label}</option>`).join('');
  }catch(error){
    city.innerHTML='<option value="">Backend unavailable</option>';
    date.innerHTML='<option value="">Backend unavailable</option>';
    setStatus(error.message,'error');
  }
}

async function updateTheaters(){
  state.cityId=Number($('f-city').value)||null;
  state.theaterId=null; state.showId=null; state.selectedTime='';
  $('theaterGrid').innerHTML='<div class="async-state">Loading theaters…</div>';
  if(!state.cityId){$('theaterGrid').innerHTML='<div class="async-state">Select a city to see theaters.</div>';return;}
  try{
    const theaters=await fetchTheaters(state.cityId);
    if(!theaters.length){$('theaterGrid').innerHTML='<div class="async-state">No theaters are available in this city.</div>';return;}
    $('theaterGrid').innerHTML='';
    for(const theater of theaters){
      const card=document.createElement('div');
      card.className='theater-card';
      card.tabIndex=0;
      card.dataset.id=theater.id;
      card.innerHTML=`<span class="theater-radio" aria-hidden="true"></span>
        <div><div class="theater-name">${theater.name}</div><div class="theater-addr">${theater.address}</div></div>
        <div class="theater-shows" data-show-list></div>`;
      card.addEventListener('click',()=>selectTheater(theater,card));
      card.addEventListener('keydown',(e)=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectTheater(theater,card);}});
      $('theaterGrid').appendChild(card);
      await loadShowsIntoCard(theater,card);
    }
  }catch(error){$('theaterGrid').innerHTML=`<div class="async-state error">${error.message}</div>`;}
}

async function loadShowsIntoCard(theater,card){
  const date=$('f-date').value;
  const list=card.querySelector('[data-show-list]');
  if(!date){list.textContent='Select a date';return;}
  try{
    const shows=await fetchShows(theater.id,date);
    if(!shows.length){list.textContent='No shows';return;}
    for(const show of shows){
      const b=document.createElement('button');
      b.type='button'; b.className='show-time'; b.textContent=show.time.slice(0,5);
      b.dataset.showId=show.id;
      b.addEventListener('click',(e)=>{e.stopPropagation();selectShow(theater,show,card,b);});
      list.appendChild(b);
    }
  }catch(error){list.textContent='Unable to load shows';}
}

async function refreshTheatersForDate(){
  if(state.cityId) await updateTheaters();
}

function selectTheater(theater,card){
  document.querySelectorAll('.theater-card').forEach(c=>c.classList.remove('selected'));
  card.classList.add('selected');
  state.theaterId=theater.id;
  state.showId=null; state.selectedTime='';
  document.querySelectorAll('.show-time').forEach(b=>b.classList.remove('selected-time'));
}

function selectShow(theater,show,card,button){
  selectTheater(theater,card);
  state.showId=show.id; state.selectedTime=show.time.slice(0,5);
  button.classList.add('selected-time');
}

function validateStep1(){
  const name=$('f-name').value.trim(), email=$('f-email').value.trim();
  const phone=$('f-phone').value.trim(), pwd=$('f-pwd').value, cpwd=$('f-cpwd').value;
  const validPwd=/^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/;
  setErr('fr-name','f-name',name.length>=3,'Please enter your full name (min 3 characters)');
  setErr('fr-email','f-email',/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email),'Please enter a valid email address');
  setErr('fr-phone','f-phone',/^[6-9]\d{9}$/.test(phone.replace(/\D/g,'')),'Please enter a valid 10-digit mobile number');
  setErr('fr-pwd','f-pwd',validPwd.test(pwd),'Use 8+ characters with letters, numbers and a special character');
  setErr('fr-cpwd','f-cpwd',pwd===cpwd&&pwd.length>0,'Passwords do not match');
  if(name.length<3 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !/^[6-9]\d{9}$/.test(phone.replace(/\D/g,'')) || !validPwd.test(pwd) || pwd!==cpwd) return;
  goStep(2);
}

async function loadSeats(){
  if(!state.showId) return;
  const target=['gold-seats','silver-seats','bronze-seats'];
  target.forEach(id=>$(id).innerHTML='<div class="async-state">Loading seats…</div>');
  try{
    state.seats=await fetchSeats(state.showId);
    renderSeats();
    updatePrice();
  }catch(error){
    target.forEach(id=>$(id).innerHTML=`<div class="async-state error">${error.message}</div>`);
  }
}

function renderSeats(){
  const groups={Gold:$('gold-seats'),Silver:$('silver-seats'),Bronze:$('bronze-seats')};
  Object.values(groups).forEach(el=>el.innerHTML='');
  state.seats.forEach(seat=>{
    const button=document.createElement('button');
    button.type='button'; button.className='seat';
    button.dataset.id=seat.id; button.dataset.category=seat.category;
    button.setAttribute('aria-label',`${seat.category} seat ${seat.row}${seat.number}, ₹${seat.price}`);
    button.textContent=`${seat.row}${seat.number}`;
    const selected=state.selectedSeatIds.has(seat.id);
    const unavailable=seat.status!=='AVAILABLE';
    const wrongCategory=seat.category!==state.category;
    button.classList.toggle('taken',unavailable);
    button.classList.toggle('selected-seat',selected);
    button.classList.toggle('not-category',wrongCategory);
    button.disabled=unavailable||wrongCategory;
    if(unavailable) button.setAttribute('aria-disabled','true');
    button.addEventListener('click',()=>toggleSeat(seat.id));
    groups[seat.category]?.appendChild(button);
  });
}

function toggleSeat(seatId){
  const seat=state.seats.find(s=>s.id===seatId);
  if(!seat || seat.status!=='AVAILABLE' || seat.category!==state.category) return;
  if(state.selectedSeatIds.has(seatId)){state.selectedSeatIds.delete(seatId);}
  else{
    if(state.selectedSeatIds.size>=state.ticketCount){
      setStatus(`Select no more than ${state.ticketCount} ticket${state.ticketCount===1?'':'s'}.`,'error');
      return;
    }
    state.selectedSeatIds.add(seatId);
  }
  renderSeats(); updatePrice();
}

function updateSeats(){
  state.ticketCount=Number($('f-tickets').value)||1;
  const selected=[...state.selectedSeatIds];
  if(selected.length>state.ticketCount) state.selectedSeatIds=new Set(selected.slice(0,state.ticketCount));
  renderSeats(); updatePrice();
}

function updatePrice(){
  const selected=state.seats.filter(s=>state.selectedSeatIds.has(s.id));
  const total=selected.reduce((sum,s)=>sum+Number(s.price),0);
  const label=$('live-price');
  if(label) label.textContent=`₹${total.toFixed(2)}`;
  return total;
}

function validateStep2(){
  const city=$('f-city').value,date=$('f-date').value;
  setErr('fr-city','f-city',!!city,'Please select a city');
  setErr('fr-date','f-date',!!date,'Please select a date');
  const err=$('theater-err');
  if(!city||!date||!state.theaterId||!state.showId){err.style.display='block';err.textContent='Please select a theater and show time';return;}
  err.style.display='none';
  state.selectedSeatIds.clear();
  goStep(3); loadSeats();
}

async function validateStep3(){
  const err=$('seat-err');
  if(state.selectedSeatIds.size!==state.ticketCount){
    err.style.display='block';err.textContent=`Please select exactly ${state.ticketCount} seat${state.ticketCount===1?'':'s'}.`;return;
  }
  err.style.display='none';
  const button=document.querySelector('[data-action="booking-step3"]');
  setButtonLoading(button,true,'Holding seats…'); setStatus('Temporarily holding your selected seats…','loading');
  try{
    const hold=await createHold({
      user:{full_name:$('f-name').value.trim(),email:$('f-email').value.trim(),phone:$('f-phone').value.replace(/\D/g,''),password:$('f-pwd').value},
      show_id:state.showId, seat_ids:[...state.selectedSeatIds], payment_method:'PENDING'
    });
    state.hold=hold;
    buildSummary(hold);
    goStep(4); setStatus('');
  }catch(error){
    setStatus(error.message,'error');
    await loadSeats();
    if(error.data?.detail?.seat_ids) {
      const taken=new Set(error.data.detail.seat_ids);
      state.selectedSeatIds=new Set([...state.selectedSeatIds].filter(id=>!taken.has(id)));
      renderSeats();
    }
  }finally{setButtonLoading(button,false);}
}

function buildSummary(hold){
  const selected=state.seats.filter(s=>state.selectedSeatIds.has(s.id));
  $('summaryBox').innerHTML=`
    <div class="summary-row"><span class="s-label">CITY</span><span class="s-val">${$('f-city').selectedOptions[0]?.textContent||''}</span></div>
    <div class="summary-row"><span class="s-label">THEATER</span><span class="s-val">${document.querySelector('.theater-card.selected .theater-name')?.textContent||''}</span></div>
    <div class="summary-row"><span class="s-label">SHOW</span><span class="s-val">${$('f-date').selectedOptions[0]?.textContent||''} · ${state.selectedTime}</span></div>
    <div class="summary-row"><span class="s-label">SEATS</span><span class="s-val">${selected.map(s=>s.row+s.number).join(', ')}</span></div>
    <div class="summary-row"><span class="s-label">TICKETS</span><span class="s-val">${state.ticketCount}</span></div>
    <div class="summary-row total"><span class="s-label">SERVER TOTAL</span><span class="s-val">₹${Number(hold.total_amount).toFixed(2)}</span></div>
    <div class="summary-note">Seats are held temporarily. Final confirmation happens only after the backend confirms the booking.</div>`;
}

async function confirm(){
  if(!$('f-agree').checked){$('agree-err').style.display='block';return;}
  $('agree-err').style.display='none';
  if(!state.hold?.booking_reference)return;
  const reference=state.hold.booking_reference;
  const button=document.querySelector('[data-action="confirm-booking"]');
  // Double-submit guard: the backend treats a repeat confirm of a CONFIRMED
  // booking as a deterministic 409, but the button stays disabled so a
  // double-click can never fire two charges.
  if(button?.disabled)return;
  setButtonLoading(button,true,'Confirming…'); setStatus('Confirming booking with the cinema server…','loading');
  try{
    const result=await confirmBooking(reference,$('f-payment').value);
    $('bookingRef').textContent=result.booking_reference;
    const sub=document.querySelector('#mpanel5 .success-sub');
    sub.textContent=`Your seats ${result.seats.join(', ')} are confirmed. Booking total: ₹${Number(result.total_amount).toFixed(2)}.`;
    goStep(5); setStatus('');
  }catch(error){
    // Never claim success on failure — and never discard the reference on a
    // network error: the server may have committed while the response was
    // lost. Reconcile via GET /api/bookings/{reference} (the authoritative
    // recovery path) before sending the user back to seat selection.
    if(error.status===undefined || error.status>=500){
      setStatus('Connection lost while confirming. Checking booking status…','loading');
      try{
        const {fetchBooking}=await import('./api.js');
        const current=await fetchBooking(reference);
        if(current.status==='CONFIRMED'){
          $('bookingRef').textContent=current.booking_reference;
          const sub=document.querySelector('#mpanel5 .success-sub');
          sub.textContent=`Your seats ${current.seats.join(', ')} are confirmed. Booking total: ₹${Number(current.total_amount).toFixed(2)}.`;
          goStep(5); setStatus(''); return;
        }
        setStatus(`Booking status: ${current.status}. ${error.message}`,'error');
      }catch(recoveryError){
        setStatus(`Could not confirm booking status (${error.message}). Your reference is ${reference} — use it to check status before retrying.`,'error');
        return;
      }finally{setButtonLoading(button,false);}
      state.hold=null;
      goStep(3);
      await loadSeats();
      return;
    }
    setStatus(error.message,'error');
    state.hold=null;
    goStep(3);
    await loadSeats();
  }finally{setButtonLoading(button,false);}
}

function togglePassword(target,button){
  const input=$(target);
  input.type=input.type==='password'?'text':'password';
  button.textContent=input.type==='password'?'👁':'🙈';
}

function reset(){
  state.step=1;state.cityId=null;state.theaterId=null;state.showId=null;state.date='';
  state.selectedTime='';state.seats=[];state.selectedSeatIds.clear();state.hold=null;
  $('booking-status')?.remove();
  ['f-name','f-email','f-phone','f-pwd','f-cpwd'].forEach(id=>{if($(id))$(id).value='';});
  $('f-city').value='';$('f-date').value='';$('f-tickets').value='2';$('f-category').value='Silver';
  $('f-agree').checked=false;
  goStep(1);
}

export function initBooking(){
  const modal=$('bookingModal');
  if(!modal)return;
  loadStep2Data();

  document.addEventListener('click',(e)=>{
    const action=e.target.closest('[data-action]')?.dataset.action;
    if(!action)return;
    switch(action){
      case 'open-booking': state.lastFocused=document.activeElement; modal.classList.add('open'); document.body.classList.add('modal-open'); reset(); modal.classList.add('open'); $('f-name')?.focus(); break;
      case 'close-booking': close(); break;
      case 'booking-step1': validateStep1(); break;
      case 'booking-step2': validateStep2(); break;
      case 'booking-step3': validateStep3(); break;
      case 'confirm-booking': confirm(); break;
      case 'update-theaters': updateTheaters(); break;
      case 'update-seats': updateSeats(); break;
      case 'update-price': state.category=$('f-category').value; state.selectedSeatIds=new Set([...state.selectedSeatIds].filter(id=>state.seats.find(s=>s.id===id)?.category===state.category)); renderSeats(); updatePrice(); break;
      case 'toggle-password': togglePassword(e.target.closest('[data-action]')?.dataset.target,e.target.closest('[data-action]')); break;
      case 'close-menu': break;
    }
  });

  document.addEventListener('click',(e)=>{
    const step=e.target.closest('[data-step]')?.dataset.step;
    if(step) goStep(Number(step));
  });

  $('f-date')?.addEventListener('change',refreshTheatersForDate);
  $('f-category')?.addEventListener('change',()=>{state.category=$('f-category').value;state.selectedSeatIds=new Set([...state.selectedSeatIds].filter(id=>state.seats.find(s=>s.id===id)?.category===state.category));renderSeats();updatePrice();});
  $('f-tickets')?.addEventListener('change',updateSeats);
  $('f-payment')?.addEventListener('change',(e)=>{
    $('fr-upiid').style.display=e.target.value.startsWith('UPI')?'block':'none';
  });

  modal.addEventListener('click',(e)=>{if(e.target===modal)close();});
  document.addEventListener('keydown',(e)=>{
    if(e.key==='Escape' && modal.classList.contains('open')) close();
    if(e.key==='Tab' && modal.classList.contains('open')) trapFocus(e,modal);
  });
}

function close(){
  $('bookingModal').classList.remove('open');
  document.body.classList.remove('modal-open');
  if(state.lastFocused?.focus) state.lastFocused.focus();
  reset();
}

function trapFocus(e,modal){
  const focusables=[...modal.querySelectorAll('button:not([disabled]),input,select,[tabindex]:not([tabindex="-1"])')];
  if(!focusables.length)return;
  const first=focusables[0],last=focusables.at(-1);
  if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}
}

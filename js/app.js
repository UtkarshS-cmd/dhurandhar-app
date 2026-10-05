
import { initNavigation } from './navigation.js';
import { initAnimations } from './animations.js';
import { initMusicPlayer } from './music-player.js';
import { initVideo } from './video.js';
import {
  register, login, fetchMyBookings, fetchReviews, submitReview,
  subscribeNewsletter, submitContact, likeReviewRequest
} from './api.js';
import { initBooking } from './booking.js';

const $=(id)=>document.getElementById(id);
const auth={
  token:localStorage.getItem('dhurandhar_token'),
  user:JSON.parse(localStorage.getItem('dhurandhar_user')||'null')
};

const REVIEWS_PER_PAGE=5;
let allReviews=[];
let reviewPage=0;

function setAuthMessage(id,message,type=''){
  const el=$(id); if(!el)return;
  el.textContent=message; el.className=type==='error'?'auth-err':'auth-ok';
}

function updateNav(){
  const btn=$('nav-auth-btn');
  if(!btn)return;
  if(auth.user){
    btn.textContent=auth.user.full_name.split(/\s+/).map(x=>x[0]).join('').slice(0,2).toUpperCase();
    btn.classList.add('profile-avatar');
    btn.setAttribute('aria-label','Open account menu');
    btn.dataset.action='toggle-profile';
    btn.title=auth.user.full_name;
  }else{
    btn.textContent='Sign In'; btn.classList.remove('profile-avatar');
    btn.setAttribute('aria-label','Sign in');
    btn.dataset.action='open-auth';
  }
  // The review form is only available to signed-in users (same as the reference design).
  const write=$('review-write-wrap'), cta=$('review-login-cta');
  if(write) write.style.display=auth.user?'block':'none';
  if(cta) cta.style.display=auth.user?'none':'block';
}

function openAuth(){ $('auth-modal')?.classList.add('open'); document.body.classList.add('modal-open'); $('al-email')?.focus(); }
function closeAuth(){ $('auth-modal')?.classList.remove('open'); document.body.classList.remove('modal-open'); }
function switchAuthTab(tab){
  document.querySelectorAll('.auth-tab').forEach(t=>t.classList.toggle('active',t.dataset.authTab===tab));
  document.querySelectorAll('.auth-panel').forEach(p=>p.classList.toggle('active',p.id===`auth-${tab}`));
  setAuthMessage('al-err','');setAuthMessage('ar-err','');setAuthMessage('ar-ok','');
}

async function doLogin(){
  const email=$('al-email').value.trim(), password=$('al-pwd').value;
  if(!email||password.length<1){setAuthMessage('al-err','Enter your email and password','error');return;}
  const btn=document.querySelector('[data-action="auth-login"]');
  btn.disabled=true; btn.textContent='Signing in…';
  try{
    const result=await login({email,password});
    auth.token=result.access_token;auth.user=result.user;
    localStorage.setItem('dhurandhar_token',auth.token);localStorage.setItem('dhurandhar_user',JSON.stringify(auth.user));
    updateNav();closeAuth();await loadReviews();
  }catch(error){setAuthMessage('al-err',error.message,'error');}
  finally{btn.disabled=false;btn.textContent='Sign In';}
}

async function doRegister(){
  const name=$('ar-name').value.trim(),email=$('ar-email').value.trim(),phone=$('ar-phone').value.trim(),password=$('ar-pwd').value;
  if(name.length<3||!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)||!/^[6-9]\d{9}$/.test(phone.replace(/\D/g,''))||password.length<8){
    setAuthMessage('ar-err','Enter a valid name, email, 10-digit phone and 8+ character password','error');return;
  }
  const btn=document.querySelector('[data-action="auth-register"]');
  btn.disabled=true;btn.textContent='Creating…';
  try{
    const result=await register({full_name:name,email,phone,password});
    auth.token=result.access_token;auth.user=result.user;
    localStorage.setItem('dhurandhar_token',auth.token);localStorage.setItem('dhurandhar_user',JSON.stringify(auth.user));
    updateNav();setAuthMessage('ar-ok','Account created successfully.','ok');
    setTimeout(closeAuth,500);
  }catch(error){setAuthMessage('ar-err',error.message,'error');}
  finally{btn.disabled=false;btn.textContent='Create Account';}
}

function logout(){
  auth.token=null;auth.user=null;localStorage.removeItem('dhurandhar_token');localStorage.removeItem('dhurandhar_user');updateNav();
}
async function openMyBookings(){
  if(!auth.token){openAuth();return;}
  const modal=$('my-bookings-modal');modal.classList.add('open');document.body.classList.add('modal-open');
  const list=$('my-bookings-list');list.innerHTML='<div class="async-state">Loading bookings…</div>';
  try{
    const bookings=await fetchMyBookings(auth.token);
    if(!bookings.length){list.innerHTML='<div class="async-state">No bookings yet.</div>';return;}
    list.innerHTML=bookings.map(b=>`<div class="booking-ticket">
      <div class="bt-ref">${b.booking_reference}</div>
      <div class="bt-row"><span class="bt-label">STATUS</span><span class="bt-val ${b.status==='CONFIRMED'?'bt-status-confirmed':''}">${b.status}</span></div>
      <div class="bt-row"><span class="bt-label">SEATS</span><span class="bt-val">${b.seats.join(', ')}</span></div>
      <div class="bt-row"><span class="bt-label">TOTAL</span><span class="bt-val">₹${Number(b.total_amount).toFixed(2)}</span></div>
    </div>`).join('');
  }catch(error){list.innerHTML=`<div class="async-state error">${error.message}</div>`;}
}

async function loadReviews(){
  const list=$('reviews-list');if(!list)return;
  try{
    allReviews=await fetchReviews();
    reviewPage=0;
    renderReviews();
  }catch(error){list.innerHTML='<div class="async-state error">Reviews are temporarily unavailable.</div>';}
}

function formatDate(iso){
  const d=new Date(iso);
  return Number.isNaN(d.getTime())?'':d.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
}

function renderReviews(){
  const list=$('reviews-list');if(!list)return;
  const loadMore=$('load-more-reviews');
  if(!allReviews.length){
    list.innerHTML='<div class="async-state">No reviews yet — be the first!</div>';
    if(loadMore) loadMore.style.display='none';
    return;
  }
  const avg=(allReviews.reduce((sum,r)=>sum+Number(r.rating),0)/allReviews.length).toFixed(1);
  let html=`<div class="review-summary">
      <div class="rs-avg">${avg}</div>
      <div class="rs-count">${allReviews.length} USER REVIEW${allReviews.length!==1?'S':''}</div>
    </div>`;
  html+=allReviews.slice(0,(reviewPage+1)*REVIEWS_PER_PAGE).map(r=>`<article class="review-card">
      <div class="review-head">
        <span class="review-who"><strong>${escapeHtml(r.name)}</strong>${r.title?`<span class="review-title">${escapeHtml(r.title)}</span>`:''}</span>
        <span class="review-rating">${Number(r.rating)}<span class="review-outof">/10</span></span>
      </div>
      <div class="review-meta">${formatDate(r.created_at)}</div>
      ${r.spoiler?'<div class="spoiler-badge">⚠ SPOILER WARNING</div>':''}
      <p>${escapeHtml(r.body)}</p>
      <button type="button" class="review-like" data-action="like-review" data-review-id="${r.id}" aria-label="Like this review">❤ ${Number(r.likes||0)}</button>
    </article>`).join('');
  list.innerHTML=html;
  if(loadMore) loadMore.style.display=allReviews.length>(reviewPage+1)*REVIEWS_PER_PAGE?'inline-block':'none';
}

function loadMoreReviews(){reviewPage++;renderReviews();}

async function likeReview(id,button){
  if(!auth.token){openAuth();return;}
  try{
    const data=await likeReviewRequest(id,auth.token);
    button.textContent=`❤ ${data.likes}`;
    button.classList.toggle('liked',!!data.liked);
  }catch{/* likes are non-critical; ignore failures */}
}

async function submitReviewForm(){
  if(!auth.token){openAuth();return;}
  const rating=Number($('r-rating').value),title=$('r-title').value.trim(),body=$('r-body').value.trim();
  if(rating<1||rating>10||title.length<2||body.length<2){$('r-err').textContent='Please provide a rating, title and review.';$('r-err').style.display='block';return;}
  try{
    await submitReview({rating,title,body,spoiler:$('r-spoiler').checked},auth.token);
    $('r-err').style.display='none';$('r-title').value='';$('r-body').value='';$('r-rating').value='';
    await loadReviews();
  }catch(error){$('r-err').textContent=error.message;$('r-err').style.display='block';}
}

async function newsletter(){
  const name=$('nl-name').value.trim(),email=$('nl-email').value.trim(),msg=$('nl-msg');
  if(name.length<2||!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)){msg.textContent='Please enter a valid name and email.';msg.style.color='#e07070';return;}
  try{await subscribeNewsletter({name,email});msg.textContent='You are subscribed.';msg.style.color='#6fc76f';}
  catch(error){msg.textContent=error.message;msg.style.color='#e07070';}
}

async function contact(){
  const payload={name:$('c-name').value.trim(),email:$('c-email').value.trim(),subject:$('c-subject').value,message:$('c-message').value.trim()};
  const msg=$('c-msg');
  if(payload.name.length<2||!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email)||payload.message.length<5){msg.textContent='Please complete all contact fields.';msg.style.color='#e07070';return;}
  try{await submitContact(payload);msg.textContent='Message received. Thank you.';msg.style.color='#6fc76f';}
  catch(error){msg.textContent=error.message;msg.style.color='#e07070';}
}

function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function initActions(){
  // Replaces the former inline onerror attributes on gallery images so the
  // Content-Security-Policy can keep script-src 'self' (no inline handlers).
  // Error events do not bubble, hence the capture phase.
  document.addEventListener('error',(event)=>{
    const el=event.target;
    if(el && el.tagName==='IMG'){
      const item=el.closest('.g-item');
      if(item) item.style.display='none';
    }
  },true);
  document.addEventListener('click',(event)=>{
    const target=event.target.closest('[data-action],[data-auth-tab]');
    if(!target)return;
    if(target.dataset.authTab){switchAuthTab(target.dataset.authTab);return;}
    switch(target.dataset.action){
      case 'open-auth':openAuth();break;
      case 'close-auth':closeAuth();break;
      case 'auth-login':doLogin();break;
      case 'auth-register':doRegister();break;
      case 'toggle-profile':{
        let menu=document.getElementById('profile-menu');
        if(!menu){
          menu=document.createElement('div');menu.id='profile-menu';menu.className='profile-dropdown open';
          menu.innerHTML=`<div class="pd-header"><div class="pd-name">${escapeHtml(auth.user?.full_name||'')}</div><div class="pd-email">${escapeHtml(auth.user?.email||'')}</div></div>
          <button class="pd-item" data-action="open-my-bookings">My Bookings</button><div class="pd-sep"></div><button class="pd-item danger" data-action="logout">Sign Out</button>`;
          target.parentElement.style.position='relative';target.parentElement.appendChild(menu);
        }else menu.classList.toggle('open');
        break;
      }
      case 'open-my-bookings':openMyBookings();break;
      case 'close-my-bookings':$('my-bookings-modal').classList.remove('open');document.body.classList.remove('modal-open');break;
      case 'logout':logout();document.getElementById('profile-menu')?.remove();break;
      case 'submit-review':submitReviewForm();break;
      case 'submit-newsletter':newsletter();break;
      case 'submit-contact':contact();break;
      case 'load-more-reviews':loadMoreReviews();break;
      case 'like-review':likeReview(target.dataset.reviewId,target);break;
    }
  });

  $('auth-modal')?.addEventListener('click',e=>{if(e.target.id==='auth-modal')closeAuth();});
  $('my-bookings-modal')?.addEventListener('click',e=>{if(e.target.id==='my-bookings-modal'){e.currentTarget.classList.remove('open');document.body.classList.remove('modal-open');}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Enter' && document.activeElement===$('al-pwd'))doLogin();
    if(e.key==='Escape'){
      closeAuth();
      $('my-bookings-modal')?.classList.remove('open');
      document.body.classList.remove('modal-open');
    }
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  initNavigation();initAnimations();initMusicPlayer();initVideo();initBooking();initActions();
  updateNav();loadReviews();
});

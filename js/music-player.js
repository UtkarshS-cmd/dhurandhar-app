
const TRACKS = [
  {file:'assets/audio/title-track.mp3', title:'Title Track — Dhurandhar', artist:'Shashwat Sachdev'},
  {file:'assets/audio/ez-ez.mp3', title:'Ez Ez', artist:'Dhurandhar OST'},
  {file:'assets/audio/ishq-jalakar.mp3', title:'Ishq Jalakar', artist:'Dhurandhar OST'},
  {file:'assets/audio/lutt-le-gaya.mp3', title:'Lutt Le Gaya', artist:'Dhurandhar OST'},
  {file:'assets/audio/move-yeh-ishq-ishq.mp3', title:'Move — Yeh Ishq Ishq', artist:'Dhurandhar OST'},
  {file:'assets/audio/naal-nachna.mp3', title:'Naal Nachna', artist:'Dhurandhar OST'},
  {file:'assets/audio/shararat.mp3', title:'Shararat', artist:'Dhurandhar OST'},
  {file:'assets/audio/teri-ni-kararan.mp3', title:'Teri Ni Kararan', artist:'Dhurandhar OST'}
];

// Hand-off events shared with js/video.js (mirrors dhurandhar (8).html):
// playing a track mutes the film's sound; switching the film's sound on
// pauses the soundtrack.
const MUSIC_PLAY_EVENT = 'dhurandhar:music-play';
const VIDEO_SOUND_EVENT = 'dhurandhar:video-sound-on';

const fmt = (seconds) => {
  if (!Number.isFinite(seconds)) return '0:00';
  const m=Math.floor(seconds/60), s=Math.floor(seconds%60);
  return `${m}:${String(s).padStart(2,'0')}`;
};

export function initMusicPlayer() {
  const audio=document.getElementById('audio-el');
  const list=document.getElementById('tracklist');
  const play=document.getElementById('btn-play');
  if (!audio || !list || !play) return;

  let index=0, shuffle=false, repeat=false, playing=false;
  // 52 randomised bars animated by the barPulse keyframes (same as the reference design).
  const bars=[];
  const viz=document.getElementById('visualizer');
  for(let i=0;i<52;i++){
    const bar=document.createElement('div');
    bar.className='bar';
    const min=4+Math.random()*8;
    const max=16+Math.random()*38;
    bar.style.cssText=`--min:${min}px;--max:${max}px;height:${min}px;animation:barPulse ${0.5+Math.random()*0.9}s ${Math.random()*0.5}s ease-in-out infinite;animation-play-state:paused;`;
    viz.appendChild(bar); bars.push(bar);
  }

  TRACKS.forEach((track,i)=>{
    const row=document.createElement('button');
    row.type='button';
    row.className=`track${i===0?' active':''}`;
    row.dataset.idx=i;
    row.innerHTML=`<span class="track-num-wrap"><span class="t-num">${String(i+1).padStart(2,'0')}</span>
      <span class="playing-anim"><span class="mb" style="height:7px;animation:mbounce .7s ease-in-out infinite;animation-play-state:paused"></span><span class="mb" style="height:12px;animation:mbounce .7s .18s ease-in-out infinite;animation-play-state:paused"></span><span class="mb" style="height:5px;animation:mbounce .7s .35s ease-in-out infinite;animation-play-state:paused"></span></span></span>
      <span class="t-info"><span class="t-name">${track.title}</span><span class="t-artist">${track.artist}</span></span>
      <span class="t-dur" id="dur-${i}">—:——</span>`;
    row.addEventListener('click',()=>load(i,true));
    list.appendChild(row);
  });

  function render(){
    document.querySelectorAll('#tracklist .track').forEach((el,i)=>el.classList.toggle('active',i===index));
    document.getElementById('np-title').textContent=TRACKS[index].title;
    document.getElementById('np-artist').textContent=TRACKS[index].artist;
  }

  function setAnimating(on){
    const run=on?'running':'paused';
    bars.forEach(b=>{b.style.animationPlayState=run;});
    list.querySelectorAll('.mb').forEach(b=>{b.style.animationPlayState=run;});
  }

  function setPlaying(next){
    playing=next;
    play.textContent=playing?'⏸':'▶';
    play.classList.toggle('playing',playing);
    play.setAttribute('aria-label',playing?'Pause music':'Play music');
    setAnimating(playing);
  }

  async function playAudio(){
    try { await audio.play(); setPlaying(true); }
    catch { setPlaying(false); }
  }
  function pauseAudio(){audio.pause();setPlaying(false);}
  function load(i,auto=false){
    index=(i+TRACKS.length)%TRACKS.length;
    audio.src=TRACKS[index].file;
    audio.load();
    render();
    document.getElementById('prog-fill').style.width='0%';
    document.getElementById('cur-time').textContent='0:00';
    document.getElementById('tot-time').textContent='0:00';
    if(auto) playAudio();
  }

  play.addEventListener('click',()=>playing?pauseAudio():playAudio());
  document.getElementById('btn-next')?.addEventListener('click',()=>{
    const next=shuffle ? Math.floor(Math.random()*TRACKS.length) : index+1;
    load(next,playing);
  });
  document.getElementById('btn-prev')?.addEventListener('click',()=>{
    if(audio.currentTime>3){audio.currentTime=0;return;}
    load(index-1,playing);
  });
  document.getElementById('btn-repeat')?.addEventListener('click',function(){
    repeat=!repeat; this.classList.toggle('active-control',repeat); this.setAttribute('aria-pressed',String(repeat));
  });
  document.getElementById('btn-shuffle')?.addEventListener('click',function(){
    shuffle=!shuffle; this.classList.toggle('active-control',shuffle); this.setAttribute('aria-pressed',String(shuffle));
  });
  document.getElementById('prog-track')?.addEventListener('pointerdown',(e)=>{
    if(!audio.duration)return;
    const r=e.currentTarget.getBoundingClientRect();
    audio.currentTime=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*audio.duration;
  });
  document.getElementById('vol-range')?.addEventListener('input',(e)=>audio.volume=Number(e.target.value));

  audio.addEventListener('timeupdate',()=>{
    if(!audio.duration)return;
    document.getElementById('prog-fill').style.width=`${(audio.currentTime/audio.duration)*100}%`;
    document.getElementById('cur-time').textContent=fmt(audio.currentTime);
  });
  audio.addEventListener('loadedmetadata',()=>{
    document.getElementById('tot-time').textContent=fmt(audio.duration);
    document.getElementById(`dur-${index}`).textContent=fmt(audio.duration);
  });
  audio.addEventListener('play',()=>{setPlaying(true);window.dispatchEvent(new CustomEvent(MUSIC_PLAY_EVENT));});
  audio.addEventListener('pause',()=>setPlaying(false));
  audio.addEventListener('ended',()=>{
    if(repeat){audio.currentTime=0;playAudio();}
    else load(shuffle?Math.floor(Math.random()*TRACKS.length):index+1,true);
  });

  // Turning the film's sound on pauses the soundtrack (reference hand-off).
  window.addEventListener(VIDEO_SOUND_EVENT,()=>{if(playing)pauseAudio();});

  // Preload track one exactly like the reference build (src + volume 0.85).
  load(0);
  audio.volume=0.85;
}


// Hero background video, ported from dhurandhar (8).html:
//  * the clip always autoplays muted + looping behind the hero content
//    (native autoplay/muted/loop attributes), whatever the motion settings;
//  * the round button toggles the film's sound (volume 0.75 on first unmute)
//    and pauses the soundtrack while video sound is on;
//  * starting a track mutes the video again — the same hand-off the
//    single-file reference build performs.
const VIDEO_VOLUME = 0.75;
const MUSIC_PLAY_EVENT = 'dhurandhar:music-play';
const VIDEO_SOUND_EVENT = 'dhurandhar:video-sound-on';

const attemptPlay = (video) => {
  const playback = video.play();
  if (playback && typeof playback.catch === 'function') playback.catch(() => {});
};

export function initVideo() {
  const video = document.getElementById('bg-video');
  const button = document.getElementById('video-mute-btn');
  if (!video || !button) return;

  let muted = true;

  const render = () => {
    button.textContent = muted ? '🔇' : '🔊';
    button.setAttribute('aria-label', muted ? 'Unmute hero video' : 'Mute hero video');
    button.setAttribute('aria-pressed', String(!muted));
    button.style.borderColor = muted ? 'rgba(201,168,76,0.4)' : 'rgba(201,168,76,0.9)';
    button.style.boxShadow = muted ? 'none' : '0 0 20px rgba(201,168,76,0.4)';
  };

  const setMuted = (next) => {
    muted = next;
    video.muted = muted;
    if (!muted) video.volume = VIDEO_VOLUME;
    render();
  };

  // Autoplay policies require a muted start — the reference build starts muted too.
  setMuted(true);
  attemptPlay(video);

  button.addEventListener('click', (event) => {
    event.stopPropagation();
    setMuted(!muted);
    // Turning the film's sound on pauses the soundtrack (reference behaviour).
    if (!muted) window.dispatchEvent(new CustomEvent(VIDEO_SOUND_EVENT));
  });

  // Starting a track hands the sound over to the music player: mute the video.
  window.addEventListener(MUSIC_PLAY_EVENT, () => { if (!muted) setMuted(true); });

  // Keep the loop rolling after tab switches or buffering hiccups.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) attemptPlay(video); });
  video.addEventListener('loadeddata', () => { if (video.paused) attemptPlay(video); });
}

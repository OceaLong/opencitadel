let muted = false;

export function setRoomSoundsMuted(value: boolean) {
  muted = value;
}

export function isRoomSoundsMuted() {
  return muted;
}

function playTone(frequency: number, duration: number, volume = 0.08) {
  if (muted || typeof window === "undefined") return;
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = frequency;
    gain.gain.value = volume;
    osc.start();
    osc.stop(ctx.currentTime + duration);
    osc.onended = () => void ctx.close();
  } catch {
    /* ignore */
  }
}

export function playDiceSound() {
  playTone(220, 0.08);
  setTimeout(() => playTone(330, 0.06), 60);
}

export function playTodSound() {
  playTone(440, 0.1);
  setTimeout(() => playTone(550, 0.08, 0.06), 90);
}

export function playReactionSound() {
  playTone(660, 0.05, 0.05);
}

export function vibrate(pattern: number | number[] = 30) {
  if (muted || typeof navigator === "undefined" || !navigator.vibrate) return;
  try {
    navigator.vibrate(pattern);
  } catch {
    /* ignore */
  }
}

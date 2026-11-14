// The playback volume slider (both apps): 0…1 as the ear hears it → the linear gain that is played.
// Same formula as Qt's QtAudio.convertVolume(Logarithmic → Linear) the desktop player uses, so a
// slider position sounds the same in both apps (web/tests/audioedit.test.ts checks Qt's numbers).

export const DEFAULT_VOLUME = 0.7;
export const VOLUME_KEY = "umc-audio-volume"; // localStorage: the slider, 0…100

export function gainFor(slider: number): number {
  const v = Math.min(1, Math.max(0, slider));
  if (v <= 0) return 0;
  if (v >= 1) return 1;
  return Math.min(1, -Math.log(1 - v) / Math.log(100));
}

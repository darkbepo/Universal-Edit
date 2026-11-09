// "Make it fit under N MB" helpers (port of the desktop app's sizing.py).
import { ConversionError } from "../core/types";

const MB = 1024 * 1024;
export const TWO_PASS_ENCODERS = new Set(["libx264", "libvpx"]);
const OVERHEAD = 0.97; // leave ~3 % for the container

export function videoKbpsFor(targetMb: number, seconds: number, audioKbps: number): number {
  if (seconds <= 0) throw new ConversionError("Couldn't read the video's length, so the target size can't be used.");
  const kbps = Math.floor(((targetMb * MB * 8) / 1000 / seconds) * OVERHEAD - audioKbps);
  if (kbps < 50) {
    const need = ((50 + audioKbps) * 1000 * seconds) / 8 / MB / OVERHEAD;
    throw new ConversionError(`${targetMb} MB is too small for ${seconds.toFixed(0)} seconds of video — it needs at least about ${need.toFixed(1)} MB. Trim it, remove the audio, or choose a bigger size.`);
  }
  return kbps;
}

export function audioKbpsFor(targetMb: number, seconds: number): number {
  if (seconds <= 0) throw new ConversionError("Couldn't read the audio's length, so the target size can't be used.");
  const kbps = Math.floor(((targetMb * MB * 8) / 1000 / seconds) * OVERHEAD);
  if (kbps < 24) throw new ConversionError(`${targetMb} MB is too small for ${seconds.toFixed(0)} seconds of audio. Trim it or choose a bigger size.`);
  return Math.min(kbps, 320);
}

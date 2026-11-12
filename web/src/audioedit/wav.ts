// WAV read / write for the audio editor (port of src/umc/audioedit/wav.py). Reads PCM
// 8/16/24/32-bit, float 32/64 and WAVE_FORMAT_EXTENSIBLE; writes 32-bit float (what the editor
// hands to FFmpeg) or 16-bit PCM. Samples are planar: one Float32Array per channel.

export class WavError extends Error {}

const PCM = 1, FLOAT = 3, EXTENSIBLE = 0xfffe;
const tag4 = (v: DataView, at: number) => String.fromCharCode(v.getUint8(at), v.getUint8(at + 1), v.getUint8(at + 2), v.getUint8(at + 3));

interface WavHeader { tag: number; channels: number; rate: number; bits: number; body: [number, number] }

/** Sample rate and channel count, without decoding the samples. */
export function wavFormat(bytes: Uint8Array): { rate: number; channels: number } {
  const { rate, channels } = header(bytes);
  return { rate, channels };
}

export function readWav(bytes: Uint8Array): { rate: number; samples: Float32Array[] } {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const { tag, channels, rate, bits, body } = header(bytes);
  const width = bits >> 3;
  const frames = width ? Math.floor(body[1] / (width * channels)) : 0;
  const out = Array.from({ length: channels }, () => new Float32Array(frames));
  const read = sampleReader(tag, bits);
  if (!read) throw new WavError("This isn't a WAV file the editor can read.");
  let at = body[0];
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      out[c][i] = read(v, at);
      at += width;
    }
  }
  return { rate, samples: out };
}

function header(bytes: Uint8Array): WavHeader {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const bad = () => new WavError("This isn't a WAV file the editor can read.");
  if (bytes.length < 12 || !["RIFF", "RF64"].includes(tag4(v, 0)) || tag4(v, 8) !== "WAVE") throw bad();
  let pos = 12;
  let fmt: DataView | null = null;
  let body: [number, number] | null = null;
  while (pos + 8 <= bytes.length) {
    const id = tag4(v, pos);
    let size = v.getUint32(pos + 4, true);
    const start = pos + 8;
    if (id === "fmt ") fmt = new DataView(bytes.buffer, bytes.byteOffset + start, Math.min(size, bytes.length - start));
    else if (id === "data") {
      if (size === 0xffffffff || start + size > bytes.length) size = bytes.length - start; // streamed / RF64
      body = [start, size];
      break;
    }
    pos = start + size + (size & 1);
  }
  if (!fmt || !body || fmt.byteLength < 16) throw bad();
  let tag = fmt.getUint16(0, true);
  const channels = fmt.getUint16(2, true);
  const rate = fmt.getUint32(4, true);
  const bits = fmt.getUint16(14, true);
  if (tag === EXTENSIBLE && fmt.byteLength >= 26) tag = fmt.getUint16(24, true);
  if (channels < 1 || rate < 1) throw bad();
  return { tag, channels, rate, bits, body };
}

function sampleReader(tag: number, bits: number): ((v: DataView, at: number) => number) | null {
  if (tag === FLOAT && bits === 32) return (v, at) => v.getFloat32(at, true);
  if (tag === FLOAT && bits === 64) return (v, at) => v.getFloat64(at, true);
  if (tag !== PCM) return null;
  if (bits === 8) return (v, at) => (v.getUint8(at) - 128) / 128;
  if (bits === 16) return (v, at) => v.getInt16(at, true) / 32768;
  if (bits === 24) return (v, at) => {
    let x = v.getUint8(at) | (v.getUint8(at + 1) << 8) | (v.getUint8(at + 2) << 16);
    if (x >= 1 << 23) x -= 1 << 24;
    return x / (1 << 23);
  };
  if (bits === 32) return (v, at) => v.getInt32(at, true) / 2147483648;
  return null;
}

/** WAV bytes: 32 = float, 16 = PCM (clipped to -1…1). */
export function writeWav(rate: number, samples: Float32Array[], bits: 32 | 16 = 32): Uint8Array {
  const channels = samples.length;
  const frames = samples[0]?.length ?? 0;
  const width = bits >> 3;
  const dataSize = frames * channels * width;
  const out = new Uint8Array(44 + dataSize);
  const v = new DataView(out.buffer);
  const str = (at: number, s: string) => { for (let i = 0; i < 4; i++) out[at + i] = s.charCodeAt(i); };
  str(0, "RIFF"); v.setUint32(4, 36 + dataSize, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, bits === 32 ? FLOAT : PCM, true); v.setUint16(22, channels, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * channels * width, true);
  v.setUint16(32, channels * width, true); v.setUint16(34, bits, true);
  str(36, "data"); v.setUint32(40, dataSize, true);
  let at = 44;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const x = samples[c][i];
      if (bits === 32) v.setFloat32(at, x, true);
      else v.setInt16(at, Math.max(-32768, Math.min(32767, Math.round(x * 32767))), true);
      at += width;
    }
  }
  return out;
}

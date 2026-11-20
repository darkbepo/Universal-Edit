// The DOM-free parts of the video editor's source opening (ui/videoeditor/sources.ts).
import { describe, expect, it, vi } from "vitest";
import { Abandoned, kindOf, SourceBank, sourceFromProbe } from "../src/ui/videoeditor/sources";
import { JobCancelled, type ConversionClient } from "../src/worker/client";
import { SOURCE_TARGET, videoEditSourceConverter } from "../src/converters/videoedit";

describe("sourceFromProbe", () => {
  it("rounds fps to 3 decimals and keeps the name and size", () => {
    const s = sourceFromProbe("s1", "clip.mp4", 12345, "video", { duration: 2, width: 320, height: 240, fps: 29.97002997, hasAudio: true });
    expect(s).toEqual({ id: "s1", path: "clip.mp4", kind: "video", duration: 2, width: 320, height: 240, fps: 29.97, has_audio: true, size: 12345, relpath: null });
  });
});

describe("kindOf", () => {
  it.each([
    ["MOV", "video"], ["mp4", "video"], ["avi", "video"], ["wmv", "video"], ["flv", "video"],
    ["heic", "image"], ["tiff", "image"], ["psd", "image"], ["cr2", "image"], ["png", "image"], ["jpeg", "image"],
    ["mp3", "audio"], ["wma", "audio"], ["aiff", "audio"],
    ["txt", null], ["umcvideo", null], ["", null],
  ])("%s → %s", (ext, kind) => expect(kindOf(ext)).toBe(kind));
});

describe("abandoning the bank (leaving the page)", () => {
  /** A stand-in for the shared worker client: one copy job we finish or fail by hand. */
  const fakeClient = () => {
    const c = {
      cancels: 0,
      progress: null as ((f: number, m: string) => void) | null,
      resolve: null as ((out: { name: string; data: Uint8Array; type: string }[]) => void) | null,
      reject: null as ((e: Error) => void) | null,
      run(_t: unknown, _f: unknown, _o: unknown, onProgress: (f: number, m: string) => void) {
        c.progress = onProgress;
        return new Promise((res, rej) => { c.resolve = res; c.reject = rej; });
      },
      cancel() {
        c.cancels++;
        c.reject?.(new JobCancelled("Cancelled."));
      },
    };
    return c;
  };
  const avi = () => new File([new Uint8Array(100)], "clip.avi");
  const started = async (c: ReturnType<typeof fakeClient>) => {
    await vi.waitFor(() => expect(c.resolve).not.toBeNull());
  };

  it("cancels a copy the worker is running", async () => {
    const c = fakeClient();
    const bank = new SourceBank(c as unknown as ConversionClient);
    const opening = bank.open(avi(), "s1", () => {});
    await started(c);
    c.progress!(0.1, "Converting…"); // the worker runs this job
    bank.abandon();
    expect(c.cancels).toBe(1);
    await expect(opening).rejects.toBeInstanceOf(Abandoned);
    expect(bank.has("s1")).toBe(false);
  });

  it("stops a copy still waiting for the worker as soon as it starts, never another job", async () => {
    const c = fakeClient();
    const bank = new SourceBank(c as unknown as ConversionClient);
    const seen: string[] = [];
    const opening = bank.open(avi(), "s1", (_f, m) => seen.push(m));
    await started(c);
    bank.abandon();
    expect(c.cancels).toBe(0); // the worker may be running someone else's job
    c.progress!(0, "Converting…");
    expect(c.cancels).toBe(1);
    expect(seen).toEqual([]); // a left page shows nothing
    await expect(opening).rejects.toBeInstanceOf(Abandoned);
  });

  it("drops a copy that finished anyway", async () => {
    const c = fakeClient();
    c.cancel = () => { c.cancels++; }; // too late to stop it
    const bank = new SourceBank(c as unknown as ConversionClient);
    const opening = bank.open(avi(), "s1", () => {});
    await started(c);
    c.progress!(0.5, "Converting…");
    bank.abandon();
    c.resolve!([{ name: "source.mp4", data: new Uint8Array(10), type: "video/mp4" }]);
    await expect(opening).rejects.toBeInstanceOf(Abandoned);
    expect(bank.has("s1")).toBe(false);
  });

  it("opens nothing more once abandoned", async () => {
    const c = fakeClient();
    const bank = new SourceBank(c as unknown as ConversionClient);
    bank.abandon();
    await expect(bank.open(avi(), "s1", () => {})).rejects.toBeInstanceOf(Abandoned);
    expect(c.resolve).toBeNull();
  });
});

describe("the hidden source converter", () => {
  it("offers nothing on the convert page", () => {
    expect(SOURCE_TARGET.converterId).toBe("video-edit-source");
    expect(videoEditSourceConverter.id).toBe("video-edit-source");
    expect(videoEditSourceConverter.targets([])).toEqual([]);
  });
});

// The DOM-free parts of the video editor's source opening (ui/videoeditor/sources.ts).
import { describe, expect, it } from "vitest";
import { kindOf, sourceFromProbe } from "../src/ui/videoeditor/sources";
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

describe("the hidden source converter", () => {
  it("offers nothing on the convert page", () => {
    expect(SOURCE_TARGET.converterId).toBe("video-edit-source");
    expect(videoEditSourceConverter.id).toBe("video-edit-source");
    expect(videoEditSourceConverter.targets([])).toEqual([]);
  });
});

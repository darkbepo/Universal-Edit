// The audio editor: Convert | Edit for audio files, selecting with the mouse, editing, saving —
// real decoding (FFmpeg in the worker) and encoding in headless Chromium.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;

interface AudioHook {
  doc: { rate: number; channels: number; frames: number; duration: number; dirty: boolean; selection: [number, number]; setSelection(a: number, b?: number): void } | null;
  fpp: number;
  playing: boolean;
  volume: number;
  playGain: number | null;
  stop(): void;
  draw(): void;
  update(): void;
}
declare global {
  interface Window {
    __audio?: AudioHook;
  }
}

async function openEditor(page: Page, files: string[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".audio-editor")).toBeVisible();
  await expect(page.locator(".audio-info")).toContainText("kHz", { timeout: 120_000 });
}

const info = (page: Page) => page.evaluate(() => {
  const d = window.__audio!.doc!;
  return { rate: d.rate, channels: d.channels, duration: d.duration, dirty: d.dirty, selection: d.selection };
});

test("Edit opens audio files (joined) in the editor and draws the waveform", async ({ page }) => {
  await openEditor(page, [fx("tone.wav"), fx("beep.mp3")]);
  const d = await info(page);
  expect(d.channels).toBe(2); // the mono MP3 is fitted to the first file
  expect(d.duration).toBeGreaterThan(3.9);
  expect(d.duration).toBeLessThan(4.1);
  await expect(page.locator(".audio-info")).toHaveText(/44\.1 kHz · Stereo · 0:04\.\d{3}/);
  await expect(page.getByText("tone.wav + 1 more")).toBeVisible();
  // the waveform is drawn: the middle of the first lane isn't the background
  const drawn = await page.evaluate(() => {
    window.__audio!.draw();
    const c = document.querySelector(".audio-canvas") as HTMLCanvasElement;
    const g = c.getContext("2d")!;
    const mid = g.getImageData(c.width >> 1, Math.round(c.height * 0.3), 1, 1).data;
    const corner = g.getImageData(c.width - 2, c.height - 2, 1, 1).data;
    return [Array.from(mid), Array.from(corner)];
  });
  expect(drawn[0]).not.toEqual(drawn[1]);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".audio-editor")).toBeHidden();
  await expect(page.locator(".pill[data-target]").first()).toBeVisible();
});

test("drag to select, cut, undo, effects and save as MP3", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const box = (await page.locator(".audio-canvas").boundingBox())!;
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 3, y);
  await page.mouse.down();
  await page.mouse.move(box.x + (box.width * 2) / 3, y, { steps: 5 });
  await page.mouse.up();
  let d = await info(page);
  expect((d.selection[1] - d.selection[0]) / d.rate).toBeGreaterThan(0.9);
  await expect(page.getByRole("textbox", { name: "Length" })).toHaveValue(/^0:01\./);
  await page.getByRole("button", { name: "Cut", exact: true }).click();
  d = await info(page);
  expect(d.duration).toBeLessThan(2.1);
  expect(d.dirty).toBe(true);
  await expect(page.locator(".audio-info")).toContainText("Unsaved changes");
  await page.keyboard.press("Control+z");
  expect((await info(page)).duration).toBeGreaterThan(2.9);
  // select the first second with the keyboard-free hook, fade it and trim to it
  await page.evaluate(() => window.__audio!.doc!.setSelection(0, 44100));
  for (const name of ["Fade in", "Trim"]) await page.getByRole("button", { name, exact: true }).click();
  expect((await info(page)).duration).toBeCloseTo(1, 2);
  await page.getByRole("button", { name: "Amplify…" }).click();
  await page.getByLabel("Change the volume by (dB)").fill("-6");
  await page.getByRole("button", { name: "OK", exact: true }).click();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  // save
  await page.getByRole("button", { name: "Save", exact: true }).click(); // first Save asks
  await page.getByLabel("Format").selectOption("mp3");
  await expect(page.getByLabel("Quality (bitrate)")).toBeVisible();
  await page.getByLabel("Quality (bitrate)").selectOption("128");
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await page.locator("dialog.save").getByRole("button", { name: "Save", exact: true }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("tone edited.mp3");
  const bytes = readFileSync(await file.path());
  expect(bytes.length).toBeGreaterThan(5000); // ~1 s at 128 kbps ≈ 16 KB
  expect([bytes[0], bytes[1], bytes[2]]).toEqual([0x49, 0x44, 0x33]); // "ID3"
  await expect(page.locator(".audio-editor .msg")).toHaveText("Saved “tone edited.mp3”.");
  expect((await info(page)).dirty).toBe(false);
});

test("typing Start / End / Length sets the selection", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const start = page.getByRole("textbox", { name: "Start" });
  const end = page.getByRole("textbox", { name: "End" });
  const length = page.getByRole("textbox", { name: "Length" });
  await expect(start).toHaveValue("0:00.000");
  await end.fill("2.5");
  await end.press("Enter");
  let d = await info(page);
  expect(d.selection).toEqual([0, 2.5 * d.rate]);
  await expect(end).toHaveValue("0:02.500");
  await expect(length).toHaveValue("0:02.500");
  await start.fill("0:01");
  await start.press("Tab"); // leaving the box applies it too
  await length.fill("0,5");
  await length.press("Enter");
  d = await info(page);
  expect(d.selection).toEqual([d.rate, 1.5 * d.rate]);
  await end.fill("9:00");
  await end.press("Enter");
  await expect(end).toHaveValue("0:03.000"); // the real end, not what was typed
  await length.fill("0,5");
  await length.press("Enter");
  // nonsense: a hint, the box marked, nothing changed
  await start.fill("abc");
  await start.press("Enter");
  await expect(page.locator(".audio-editor .msg")).toContainText("1:05.250");
  await expect(start).toHaveAttribute("aria-invalid", "true");
  // Esc puts the time back; Delete / Space while typing don't edit the sound
  await start.press("Escape");
  await expect(start).toHaveValue("0:01.000");
  await start.click();
  await start.press("Delete");
  await start.press("Space");
  await start.press("Control+t");
  d = await info(page);
  expect(d.dirty).toBe(false);
  expect(d.selection).toEqual([d.rate, 1.5 * d.rate]);
});

test("Speed / pitch changes the selection's tempo, and undo puts it back", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]); // 3 s
  await page.evaluate(() => window.__audio!.doc!.setSelection(0, 44100));
  await page.getByRole("button", { name: "Speed / pitch…" }).click();
  const dlg = page.locator("dialog.speed");
  await dlg.getByLabel("Change").selectOption("tempo");
  await dlg.getByLabel("Amount").fill("50");
  await expect(dlg.locator(".speed-result")).toHaveText("0:01.000 → 0:02.000");
  await dlg.getByRole("button", { name: "OK" }).click();
  await expect.poll(async () => (await info(page)).duration, { timeout: 120_000 }).toBeCloseTo(4, 3);
  const d = await info(page);
  expect(d.selection).toEqual([0, 2 * d.rate]);
  expect(d.dirty).toBe(true);
  await expect(page.locator(".audio-editor .msg")).toHaveText("");
  await page.keyboard.press("Control+z");
  expect((await info(page)).duration).toBeCloseTo(3, 3);
  // pitch keeps the length exactly
  await page.getByRole("button", { name: "Speed / pitch…" }).click();
  await dlg.getByLabel("Change").selectOption("pitch");
  await dlg.getByLabel("Amount").fill("12");
  await dlg.getByRole("button", { name: "OK" }).click();
  await expect.poll(async () => (await info(page)).dirty, { timeout: 120_000 }).toBe(true);
  expect((await info(page)).duration).toBeCloseTo(3, 3);
});

test("Speed / pitch can be cancelled, and the next change still works", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const dlg = page.locator("dialog.speed");
  const change = async (mode: string, amount: string) => {
    await page.getByRole("button", { name: "Speed / pitch…" }).click();
    await dlg.getByLabel("Change").selectOption(mode);
    await dlg.getByLabel("Amount").fill(amount);
    await dlg.getByRole("button", { name: "OK" }).click();
  };
  // a minute of sound, so the change is still running when Cancel is clicked
  const frames = await page.evaluate(() => {
    const d = window.__audio!.doc!;
    d.selectAll();
    const clip = d.copy()!;
    for (let i = 0; i < 19; i++) { d.setSelection(d.frames); d.paste(clip); }
    d.setSelection(0);
    return d.frames;
  });
  await change("pitch", "3");
  const cancel = page.locator(".audio-editor .editor-status").getByRole("button", { name: "Cancel" });
  await cancel.click({ timeout: 60_000 });
  await expect(page.locator(".audio-editor .msg")).toHaveText("Cancelled: nothing was changed.");
  await expect(cancel).toBeHidden();
  expect(await page.evaluate(() => window.__audio!.doc!.undoLabel())).toBe("Paste");
  expect(await page.evaluate(() => window.__audio!.doc!.frames)).toBe(frames);
  // an empty amount keeps the last value instead of the smallest one
  await page.getByRole("button", { name: "Speed / pitch…" }).click();
  await dlg.getByLabel("Change").selectOption("tempo");
  await dlg.getByLabel("Amount").fill("");
  await expect(dlg.locator(".speed-result")).toHaveText("1:00.000 → 1:00.000");
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await change("tempo", "200");
  await expect.poll(async () => (await info(page)).duration, { timeout: 120_000 }).toBeCloseTo(30, 3);
});

test("Learn noise, then Reduce noise turns the hiss down; undo puts it back", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]); // 3 s
  const hissRms = () => page.evaluate(() => {
    const d = window.__audio!.doc! as unknown as { rate: number; samples: Float32Array[] };
    let sum = 0;
    for (const ch of d.samples) for (let i = 0; i < d.rate; i++) sum += ch[i] * ch[i];
    return Math.sqrt(sum / (d.rate * d.samples.length));
  });
  // the first second becomes hiss only
  await page.evaluate(() => {
    const d = window.__audio!.doc! as unknown as { rate: number; samples: Float32Array[] };
    let seed = 1;
    for (const ch of d.samples) for (let i = 0; i < d.rate; i++) {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      ch[i] = (seed / 2147483648 - 0.5) * 0.1;
    }
    window.__audio!.draw();
  });
  const before = await hissRms();
  const reduce = page.getByRole("button", { name: "Reduce noise…" });
  await expect(reduce).toBeDisabled();
  await page.evaluate(() => { window.__audio!.doc!.setSelection(0, 44100); window.__audio!.update(); });
  await page.getByRole("button", { name: "Learn noise", exact: true }).click();
  await expect(page.locator(".audio-editor .msg")).toContainText("Noise learned");
  await page.evaluate(() => window.__audio!.doc!.setSelection(0));
  await reduce.click();
  const dlg = page.locator("dialog.noise");
  await dlg.getByLabel("Reduce by").fill("24");
  await dlg.getByRole("button", { name: "OK" }).click();
  await expect.poll(async () => (await info(page)).dirty, { timeout: 120_000 }).toBe(true);
  expect(await hissRms()).toBeLessThan(before / 4);
  expect((await info(page)).duration).toBeCloseTo(3, 3);
  await page.keyboard.press("Control+z");
  expect(await hissRms()).toBeCloseTo(before, 4);
});

test("the Spectrogram switch draws pitches in the lanes and is remembered", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const button = page.getByRole("button", { name: "Spectrogram" });
  await expect(button).toHaveAttribute("aria-pressed", "false");
  const lanePixel = () => page.evaluate(() => {
    const c = document.querySelector<HTMLCanvasElement>(".audio-editor canvas")!;
    const dpr = devicePixelRatio || 1;
    const [r, g, b] = c.getContext("2d")!.getImageData(Math.round(c.width / 2), Math.round(c.height - 4 * dpr), 1, 1).data;
    return [r, g, b];
  });
  const wave = await lanePixel();
  await button.click();
  await expect(button).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => (window.__audio as unknown as { spectrogram: boolean }).spectrogram)).toBe(true);
  const spectro = await lanePixel();
  expect(spectro).not.toEqual(wave); // the dark spectrogram colours, not the lane background
  expect(spectro[0] + spectro[1] + spectro[2]).toBeLessThan(250);
  expect(await page.evaluate(() => localStorage.getItem("umc-audio-spectrogram"))).toBe("1");
  await openEditor(page, [fx("tone.wav")]); // a new visit keeps it on
  await expect(page.getByRole("button", { name: "Spectrogram" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Spectrogram" }).click();
  expect(await page.evaluate(() => localStorage.getItem("umc-audio-spectrogram"))).toBe("0");
});

type RecordHook = { recording: boolean; doc: { frames: number; selection: [number, number]; undoLabel(): string } | null };
const rec = (page: Page) => page.evaluate(() => {
  const a = window.__audio as unknown as RecordHook;
  const d = a.doc;
  return { recording: a.recording, frames: d?.frames ?? 0, selection: d?.selection ?? [0, 0] };
});

test("Record puts a take from the microphone at the cursor; undo takes it out", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const before = await rec(page);
  const button = page.getByRole("button", { name: "● Record" });
  await button.click();
  await expect(page.getByRole("button", { name: "■ Stop recording" })).toBeVisible();
  await expect(page.locator(".audio-editor .msg")).toContainText("Recording…");
  await expect.poll(async () => (await rec(page)).recording).toBe(true);
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "■ Stop recording" }).click();
  await expect(page.getByRole("button", { name: "● Record" })).toBeVisible();
  const after = await rec(page);
  expect(after.recording).toBe(false);
  const added = after.frames - before.frames;
  expect(added).toBeGreaterThan(44_100 * 0.8); // about a second at the sound's own rate
  expect(after.selection[1] - after.selection[0]).toBe(added); // the take is selected
  expect(await page.evaluate(() => (window.__audio as unknown as RecordHook).doc!.undoLabel())).toBe("Record");
  await page.keyboard.press("Control+z");
  expect((await rec(page)).frames).toBe(before.frames);
});

test("recording stops by itself when the sound is full, and leaving keeps a take", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const before = (await rec(page)).frames;
  await page.evaluate(() => { (window.__audio as unknown as { recordRoom: number }).recordRoom = 4410; });
  await page.getByRole("button", { name: "● Record" }).click();
  await expect(page.getByRole("button", { name: "● Record" })).toBeVisible({ timeout: 10_000 }); // stopped by itself
  await expect(page.locator(".audio-editor .msg")).toContainText("as long as the audio editor allows");
  expect((await rec(page)).frames - before).toBe(4410);
  // record again and leave while recording: the take is kept, and leaving asks about it
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await page.evaluate(() => { (window.__audio as unknown as { recordRoom: number | null }).recordRoom = null; });
  await page.getByRole("button", { name: "● Record" }).click();
  await expect.poll(async () => (await rec(page)).recording).toBe(true);
  await page.waitForTimeout(600);
  let asked = "";
  page.once("dialog", (d) => { asked = d.message(); void d.dismiss(); });
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect.poll(() => asked).not.toBe("");
  const after = await rec(page);
  expect(after.recording).toBe(false);
  expect(after.frames).toBeGreaterThan(before);
  await expect(page.locator(".audio-editor")).toBeVisible();
});

test("recording in an empty editor makes a new sound", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=audio]").click();
  await expect(page.locator(".audio-editor")).toBeVisible();
  await page.keyboard.press("r");
  await expect(page.getByRole("button", { name: "■ Stop recording" })).toBeVisible();
  await page.waitForTimeout(800);
  await page.keyboard.press("r");
  await expect(page.getByRole("button", { name: "● Record" })).toBeVisible();
  expect((await rec(page)).frames).toBeGreaterThan(10_000);
  await expect(page.locator(".audio-info")).toContainText("Stereo");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
});

type TrackHook = { tracks: { name: string; doc: { frames: number; channels: number; rate: number } }[]; activeTrack: number; mixing: boolean; dirty: boolean };
const trackInfo = (page: Page) => page.evaluate(() => {
  const a = window.__audio as unknown as TrackHook;
  return { names: a.tracks.map((t) => t.name), active: a.activeTrack, mixing: a.mixing, dirty: a.dirty, channels: a.tracks.map((t) => t.doc.channels), rate: a.tracks.map((t) => t.doc.rate) };
});

test("tracks: add a file as a track, switch, set volume / pan, save the mix", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  await expect(page.locator(".audio-tracks")).toBeVisible();
  expect(await trackInfo(page)).toMatchObject({ names: ["tone"], mixing: false, dirty: false });
  await page.locator(".audio-editor input[type=file][accept='audio/*']").setInputFiles(fx("beep.mp3"));
  await expect.poll(async () => (await trackInfo(page)).names, { timeout: 60_000 }).toEqual(["tone", "beep"]);
  let t = await trackInfo(page);
  expect(t).toMatchObject({ active: 1, mixing: true, dirty: true, channels: [2, 1] });
  expect(t.rate[1]).toBe(t.rate[0]); // read at the first track's rate
  await expect(page.locator(".audio-info")).toContainText("mix of 2 tracks");
  // edits go to the selected track only
  await page.evaluate(() => window.__audio!.doc!.setSelection(0, 4410));
  await page.evaluate(() => window.__audio!.update());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.locator(".audio-track-name", { hasText: "tone" }).click();
  t = await trackInfo(page);
  expect(t.active).toBe(0);
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeDisabled();
  // mixing settings
  await page.getByLabel("beep volume").fill("-6");
  await page.getByLabel("beep left / right").fill("-100");
  await expect(page.locator(".audio-track").nth(1)).toContainText("-6 dB");
  await expect(page.locator(".audio-track").nth(1)).toContainText("L 100");
  await page.getByRole("button", { name: "Mute tone" }).click();
  await expect(page.getByRole("button", { name: "Mute tone" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Mute tone" }).click();
  // save the mix as WAV
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByLabel("Format").selectOption("wav");
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await page.locator("dialog.save").getByRole("button", { name: "Save", exact: true }).click();
  const bytes = readFileSync(await (await download).path());
  expect(String.fromCharCode(...bytes.subarray(0, 4))).toBe("RIFF");
  await expect(page.locator(".audio-editor .msg")).toContainText("Saved “tone edited.wav”");
  expect((await trackInfo(page)).dirty).toBe(false);
  // remove a track (asks first: it has sound)
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Remove beep" }).click();
  expect(await trackInfo(page)).toMatchObject({ names: ["tone"], mixing: false });
});

test("tracks: an empty track to record into", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  await page.getByRole("button", { name: "+ Empty track" }).click();
  expect(await trackInfo(page)).toMatchObject({ names: ["tone", "Track 2"], active: 1 });
  expect((await rec(page)).frames).toBe(0);
  await page.getByRole("button", { name: "● Record" }).click();
  await expect.poll(async () => (await rec(page)).recording).toBe(true);
  await page.waitForTimeout(700);
  await page.getByRole("button", { name: "■ Stop recording" }).click();
  await expect(page.getByRole("button", { name: "● Record" })).toBeVisible();
  expect((await rec(page)).frames).toBeGreaterThan(10_000);
  expect(await page.evaluate(() => (window.__audio as unknown as TrackHook).tracks[0].doc.frames)).toBeGreaterThan(100_000); // the first track untouched
});

test("the audio tile opens an empty editor; Edit is off for audio mixed with images", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=audio]").click();
  await expect(page.locator(".audio-editor")).toBeVisible();
  await expect(page.locator(".audio-placeholder")).toContainText("Open a sound");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await page.locator('input[type="file"]').first().setInputFiles([fx("tone.wav"), fx("img0.png")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
});

test("left click marks without moving playback; middle click moves it", async ({ page }) => {
  await openEditor(page, [fx("tone.wav"), fx("tone.wav"), fx("tone.wav")]); // 9 s: time to click
  await page.getByRole("slider", { name: "Volume", exact: true }).fill("0");
  await page.getByRole("button", { name: "▶ Play" }).click();
  await expect.poll(() => page.evaluate(() => window.__audio!.playing)).toBe(true);
  const box = (await page.locator(".audio-canvas").boundingBox())!;
  const y = box.y + box.height / 2;
  const at = (f: number) => box.x + box.width * f;
  const frameAt = (f: number) => page.evaluate((px) => window.__audio!.frameAt(px), box.width * f);
  // where the current playback started (a jump starts a new one), and whether it is paused
  const player = () => page.evaluate(() => (window.__audio as unknown as { player: { origin: number; paused: boolean } | null }).player);
  await page.mouse.click(at(0.8), y);
  expect((await info(page)).selection[0]).toBe(await frameAt(0.8));
  expect(await player()).toMatchObject({ origin: 0 }); // still the same playback
  await page.mouse.move(at(0.3), y);
  await page.mouse.down();
  await page.mouse.move(at(0.4), y);
  await page.mouse.up();
  expect(await player()).toMatchObject({ origin: 0 });
  const selection = (await info(page)).selection;
  await page.mouse.click(at(0.6), y, { button: "middle" });
  expect(await player()).toMatchObject({ origin: await frameAt(0.6), paused: false });
  expect((await info(page)).selection).toEqual(selection);
  await page.evaluate(() => window.__audio!.togglePlay()); // pause, then move: stays paused
  await page.mouse.click(at(0.2), y, { button: "middle" });
  expect(await player()).toMatchObject({ origin: await frameAt(0.2), paused: true });
  expect(await page.evaluate(() => window.__audio!.position())).toBe(await frameAt(0.2));
  await page.evaluate(() => window.__audio!.stop());
});

test("the volume slider sets the playback volume and is remembered", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const slider = page.getByRole("slider", { name: "Volume", exact: true });
  await expect(slider).toHaveValue("70");
  await slider.fill("0"); // silent while it plays
  await expect(page.locator(".audio-volume-value")).toHaveText("0 %");
  await page.getByRole("button", { name: "▶ Play" }).click(); // a real click: autoplay rules
  await expect.poll(() => page.evaluate(() => window.__audio!.playing)).toBe(true);
  expect(await page.evaluate(() => window.__audio!.playGain)).toBe(0);
  await slider.fill("50"); // changes the sound that is playing (a short glide)
  expect(await page.evaluate(() => window.__audio!.volume)).toBeCloseTo(0.5);
  await expect.poll(() => page.evaluate(() => window.__audio!.playGain), { timeout: 5000 }).toBeCloseTo(0.150515, 3);
  await page.evaluate(() => window.__audio!.stop());
  await slider.fill("35");
  await page.reload();
  await page.locator('input[type="file"]').first().setInputFiles([fx("tone.wav")]);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByRole("slider", { name: "Volume", exact: true })).toHaveValue("35");
  expect(await page.evaluate(() => window.__audio!.volume)).toBeCloseTo(0.35);
});

test("saving keeps the song's title and artist", async ({ page }) => {
  await openEditor(page, [fx("song.mp3")]);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByLabel("Format").selectOption("flac");
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await page.locator("dialog.save").getByRole("button", { name: "Save", exact: true }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("song edited.flac");
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format_tags", "-of", "json", await file.path()], { encoding: "utf8" }));
  const tags = Object.fromEntries(Object.entries(probe.format.tags ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
  expect(tags.title).toBe("Song");
  expect(tags.artist).toBe("Band");
});

test("saving keeps the cover picture", async ({ page }, info) => {
  const dir = info.outputPath();
  mkdirSync(dir, { recursive: true });
  const cover = `${dir}/cover.jpg`;
  const song = `${dir}/covered.mp3`;
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=32x32", "-frames:v", "1", cover]);
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", "-i", cover, "-map", "0:a", "-map", "1",
    "-c:a", "libmp3lame", "-c:v", "copy", "-disposition:v:0", "attached_pic", "-metadata", "title=Covered", "-id3v2_version", "3", song]);
  await openEditor(page, [song]);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByLabel("Format").selectOption("m4a");
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await page.locator("dialog.save").getByRole("button", { name: "Save", exact: true }).click();
  const file = await download;
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format_tags=title:stream=codec_type:stream_disposition=attached_pic", "-of", "json", await file.path()], { encoding: "utf8" }));
  expect(probe.format.tags.title).toBe("Covered");
  expect(probe.streams.some((s: { codec_type: string; disposition?: { attached_pic?: number } }) => s.codec_type === "video" && s.disposition?.attached_pic === 1)).toBe(true);
});

test("the play head is followed only while it is on screen", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const r = await page.evaluate(() => {
    const a = window.__audio! as unknown as { zoom(f: number): void; setStart(s: number): void; start: number; visibleFrames(): number; follow(b: number | null, f: number): void };
    a.zoom(8);
    a.setStart(0);
    const n = a.visibleFrames();
    a.follow(null, n * 1.5); // playback starts off screen: the view goes there
    const jumped = a.start > n;
    a.setStart(0); // scrolled away from the play head: the view stays
    a.follow(n * 1.5, n * 1.6);
    const stayed = a.start === 0;
    a.follow(n * 0.9, n * 1.1); // on screen, past the edge: turns the page
    return { jumped, stayed, turned: a.start > n };
  });
  expect(r).toEqual({ jumped: true, stayed: true, turned: true });
});

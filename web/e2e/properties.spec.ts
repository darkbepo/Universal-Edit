// Properties mode: Convert | Edit | Properties shows "About this file" for the added files.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { type Download, expect, type Page, test } from "@playwright/test";
import { FIXTURES, findPython } from "./global-setup";

async function add(page: Page, names: string[]) {
  await page.locator('input[type="file"]').first().setInputFiles(names.map((n) => `${FIXTURES}${n}`));
  await expect(page.locator(".queue li")).toHaveCount(names.length);
}

/** {label: value} of one card, once the reading is done. */
async function card(page: Page, title: string): Promise<Record<string, string>> {
  const c = page.locator(`.prop-card[data-title="${title}"]`);
  await expect(c).toBeVisible({ timeout: 60_000 });
  const labels = await c.locator("dt").allTextContents();
  const values = await c.locator("dd").allTextContents();
  return Object.fromEntries(labels.map((l, i) => [l, values[i]]));
}

const propertiesButton = (page: Page) => page.getByRole("button", { name: "Properties", exact: true });

test("a photo: camera, settings and location; Convert brings the formats back", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}camera.jpg`), "python3 missing: no sample photo");
  await page.goto("./");
  await expect(propertiesButton(page)).toBeDisabled();
  await add(page, ["camera.jpg"]);
  await propertiesButton(page).click();
  await expect(propertiesButton(page)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".properties-pane")).toBeVisible();
  await expect(page.locator(".convert-pane")).toBeHidden();
  await expect(page.getByRole("button", { name: "Convert", exact: true })).toBeHidden();
  const file = await card(page, "File");
  expect(file.Name).toBe("camera.jpg");
  expect(file.Type).toBe("JPG image");
  expect(await card(page, "Picture")).toMatchObject({ Dimensions: "64 × 48 px", Resolution: "300 dpi", Orientation: "Turned 90° right" });
  expect(await card(page, "Camera")).toEqual({
    Camera: "Canon EOS R6", Lens: "RF 50mm F1.8 STM", Taken: "2026-11-12 14:03", Exposure: "1/120 s",
    Aperture: "f/1.8", ISO: "200", "Focal length": "50 mm", Flash: "Didn't fire",
  });
  expect(await card(page, "Details")).toEqual({ Title: "Harbour at dusk", Author: "Ada Lovelace", Copyright: "CC BY 4.0" });
  expect(await card(page, "Location")).toEqual({ Coordinates: "55.67610, 12.56997", Altitude: "12 m" });
  await page.getByRole("button", { name: "Convert mode" }).click();
  await expect(page.locator(".properties-pane")).toBeHidden();
  await expect(page.locator(".convert-pane")).toBeVisible();
  await expect(page.getByRole("button", { name: "Convert", exact: true })).toBeVisible();
});

test("several files: the picker chooses which one; clearing goes back to Convert", async ({ page }) => {
  await page.goto("./");
  await add(page, ["three.pdf", "img0.png"]);
  await propertiesButton(page).click();
  expect(await card(page, "Document")).toMatchObject({ Pages: "3", "Page size": "A4 (210 × 297 mm)" });
  await expect(page.locator(".status")).toContainText("2 files");
  await page.locator(".prop-file").selectOption({ label: "img0.png" });
  await expect(page.locator('.prop-card[data-title="Document"]')).toBeHidden();
  expect(await card(page, "Picture")).toMatchObject({ Dimensions: "321 × 241 px", Colour: "Colour" });
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByRole("button", { name: "Convert mode" })).toHaveAttribute("aria-pressed", "true");
  await expect(propertiesButton(page)).toBeDisabled();
  await expect(page.locator(".editor-hub")).toBeVisible();
});

test("video and music: codecs, size and tags", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}clip.mp4`) || !existsSync(`${FIXTURES}song.mp3`), "ffmpeg missing: no samples");
  await page.goto("./");
  await add(page, ["clip.mp4", "song.mp3"]);
  await propertiesButton(page).click();
  expect(await card(page, "Video")).toMatchObject({ Dimensions: "320 × 240 px", "Frame rate": "30 fps", Codec: "H.264" });
  expect(await card(page, "Audio")).toMatchObject({ Codec: "AAC" });
  await page.locator(".prop-file").selectOption({ label: "song.mp3" });
  await expect(page.locator('.prop-card[data-title="Video"]')).toBeHidden();
  expect(await card(page, "Audio")).toMatchObject({ Codec: "MP3", Channels: "Stereo", "Sample rate": "44.1 kHz" });
  expect(await card(page, "Tags")).toEqual({ Title: "Song", Artist: "Band", Year: "2019" });
});

test("a turned video says how far it's turned, clockwise like the desktop app", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}turned.mp4`), "ffmpeg missing: no sample");
  await page.goto("./");
  await add(page, ["turned.mp4"]);
  await propertiesButton(page).click();
  expect(await card(page, "Video")).toMatchObject({ Rotation: "270°", Dimensions: "320 × 240 px" });
});

test("fonts, Word documents and 3D models", async ({ page }) => {
  await page.goto("./");
  await add(page, ["DejaVuSans.woff2", "report.docx", "box.stl"]);
  await propertiesButton(page).click();
  expect(await card(page, "Font")).toMatchObject({ Family: "DejaVu Sans", Style: "Book", Outlines: "TrueType" });
  await page.locator(".prop-file").selectOption({ label: "report.docx" });
  await expect(page.locator('.prop-card[data-title="Font"]')).toBeHidden();
  await expect(page.locator('.prop-card[data-title="File"]')).toContainText("DOCX document");
  await page.locator(".prop-file").selectOption({ label: "box.stl" });
  expect(await card(page, "3D model")).toMatchObject({ Watertight: "Yes" });
});

test.describe("change photo details", () => {
  test.skip(!existsSync(`${FIXTURES}camera.jpg`), "python3 missing: no sample photo");

  test("change, download, and the queue keeps the changed picture", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg"]);
    await propertiesButton(page).click();
    const form = page.locator(".ph-form");
    await expect(form).toBeVisible();
    await expect(page.locator("#ph-title")).toHaveValue("Harbour at dusk");
    await expect(page.locator("#ph-taken")).toHaveValue("2026-11-12 14:03");
    await expect(page.locator("#ph-rotation")).toHaveValue("6");
    const save = form.getByRole("button", { name: "Download with changes" });
    await expect(save).toBeDisabled();
    await page.locator("#ph-title").fill("Pier");
    await page.locator("#ph-author").fill("Grace Hopper");
    await page.locator("#ph-rotation").selectOption("1");
    await page.locator("#ph-remove-location").check();
    const download = page.waitForEvent("download");
    await save.click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("camera.jpg");
    const saved = await file.path();
    const { readDetails } = await import("../src/properties/photo");
    const { readFileSync } = await import("node:fs");
    const d = readDetails(new Uint8Array(readFileSync(saved)));
    expect([d.title, d.author, d.orientation, d.has_location]).toEqual(["Pier", "Grace Hopper", 1, false]);
    await expect(form).toContainText("Downloaded “camera.jpg”");
    // "About this file" now shows the changed picture
    await expect(page.locator('.prop-card[data-title="Details"]')).toContainText("Pier");
    await expect(page.locator('.prop-card[data-title="Location"]')).toHaveCount(0);
    // the desktop app reads the same details from it
    const { execFileSync } = await import("node:child_process");
    const out = execFileSync("python3", ["-c",
      "import sys, json; sys.path.insert(0, sys.argv[1]); from dataclasses import asdict; " +
      "from umc.properties.photo import read_details; print(json.dumps(asdict(read_details(open(sys.argv[2], 'rb').read()))))",
      fileURLToPath(new URL("../../src", import.meta.url)), saved]).toString();
    expect(JSON.parse(out)).toEqual(d);
  });

  test("several pictures: apply to all, several values stay, a ZIP download", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg", "img0.png"]);
    await propertiesButton(page).click();
    await expect(page.locator(".ph-form")).toBeVisible();
    await page.locator("#ph-apply-all").check();
    await expect(page.locator("#ph-title")).toHaveValue("");
    await expect(page.locator("#ph-title")).toHaveAttribute("placeholder", "Several values");
    await page.locator("#ph-copyright").fill("CC0");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download changed pictures (.zip)" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("pictures (changed details).zip");
    const { unzipSync } = await import("fflate");
    const { readFileSync } = await import("node:fs");
    const { readDetails } = await import("../src/properties/photo");
    const zip = unzipSync(new Uint8Array(readFileSync(await file.path())));
    expect(Object.keys(zip).sort()).toEqual(["camera.jpg", "img0.png"]);
    expect(readDetails(zip["camera.jpg"])).toMatchObject({ copyright: "CC0", title: "Harbour at dusk" });
    expect(readDetails(zip["img0.png"])).toMatchObject({ copyright: "CC0", title: "" });
  });

  test("a bad date is refused in plain words", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg"]);
    await propertiesButton(page).click();
    await page.locator("#ph-taken").fill("12/11/2026");
    await expect(page.locator("#ph-taken")).toHaveClass(/invalid/);
    await page.getByRole("button", { name: "Download with changes" }).click();
    await expect(page.locator(".ph-form .ph-note")).toContainText("year-month-day");
  });

  test("no form for pictures it can't change", async ({ page }) => {
    await page.goto("./");
    await add(page, ["design.psd"]);
    await propertiesButton(page).click();
    await expect(page.locator('.prop-card[data-title="File"]')).toBeVisible();
    await expect(page.locator(".ph-form")).toBeHidden();
  });
});

test.describe("change music tags", () => {
  test.skip(!existsSync(`${FIXTURES}song.mp3`) || !existsSync(`${FIXTURES}song.flac`), "ffmpeg missing: no sample songs");

  /** What the desktop app reads from ``path`` (null when no Python with mutagen is found). */
  const desktopReads = async (path: string): Promise<Record<string, unknown> | null> => {
    const python = findPython("import mutagen");
    if (!python) return null;
    const { execFileSync } = await import("node:child_process");
    const out = execFileSync(python, ["-c",
      "import sys, json; sys.path.insert(0, sys.argv[1]); from dataclasses import asdict; from pathlib import Path; " +
      "from umc.properties.music import read_details; print(json.dumps(asdict(read_details(Path(sys.argv[2])))))",
      fileURLToPath(new URL("../../src", import.meta.url)), path]).toString();
    return JSON.parse(out);
  };

  test("change, add a cover, download; the desktop reads the same tags", async ({ page }) => {
    await page.goto("./");
    await add(page, ["song.mp3"]);
    await propertiesButton(page).click();
    const form = page.locator(".mf-form");
    await expect(form).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".ph-form")).toBeHidden();
    await expect(page.locator("#mf-title")).toHaveValue("Song");
    await expect(page.locator("#mf-artist")).toHaveValue("Band");
    await expect(page.locator("#mf-year")).toHaveValue("2019");
    await expect(form.locator(".mf-cover-status")).toHaveText("No cover picture");
    const save = form.getByRole("button", { name: "Download with changes" });
    await expect(save).toBeDisabled();
    await page.locator("#mf-title").fill("Blue in Green");
    await page.locator("#mf-track").fill("03 / 9");
    await page.locator("#mf-cover-file").setInputFiles(`${FIXTURES}img0.png`);
    await expect(form.locator(".mf-cover-status")).toHaveText("New cover: img0.png");
    const download = page.waitForEvent("download");
    await save.click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("song.mp3");
    await expect(form).toContainText("Downloaded “song.mp3”", { timeout: 60_000 });
    // the queue holds the changed song: the form shows its tags now
    await expect(page.locator("#mf-title")).toHaveValue("Blue in Green");
    await expect(page.locator("#mf-track")).toHaveValue("3/9");
    await expect(form.locator(".mf-cover-status")).toHaveText("Has a cover picture");
    const d = await desktopReads(await file.path());
    if (d) expect(d).toEqual({ title: "Blue in Green", artist: "Band", album: "", album_artist: "", track: "3/9", disc: "", year: "2019", genre: "", has_cover: true });
  });

  test("several songs: apply to all, several values stay, a ZIP download", async ({ page }) => {
    await page.goto("./");
    await add(page, ["song.mp3", "song.flac"]);
    await propertiesButton(page).click();
    await expect(page.locator(".mf-form")).toBeVisible({ timeout: 60_000 });
    await page.locator("#mf-apply-all").check();
    await expect(page.locator("#mf-title")).toHaveValue("");
    await expect(page.locator("#mf-title")).toHaveAttribute("placeholder", "Several values");
    await expect(page.locator("#mf-artist")).toHaveValue("Band");
    await page.locator("#mf-album").fill("Kind of Blue");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download changed songs (.zip)" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("songs (changed details).zip");
    const { unzipSync } = await import("fflate");
    const { readFileSync, writeFileSync, mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const zip = unzipSync(new Uint8Array(readFileSync(await file.path())));
    expect(Object.keys(zip).sort()).toEqual(["song.flac", "song.mp3"]);
    const dir = mkdtempSync(`${tmpdir()}/umc-music-`);
    for (const [name, data] of Object.entries(zip)) {
      writeFileSync(`${dir}/${name}`, data);
      const d = await desktopReads(`${dir}/${name}`);
      if (d) expect(d).toMatchObject({ album: "Kind of Blue", artist: "Band", title: name.endsWith(".mp3") ? "Song" : "Other" });
    }
  });

  test("a bad track number is refused in plain words", async ({ page }) => {
    await page.goto("./");
    await add(page, ["song.flac"]);
    await propertiesButton(page).click();
    await expect(page.locator(".mf-form")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator("#mf-track")).toHaveValue("2");
    await page.locator("#mf-track").fill("13/12");
    await expect(page.locator("#mf-track")).toHaveClass(/invalid/);
    await page.getByRole("button", { name: "Download with changes" }).click();
    await expect(page.locator(".mf-note")).toContainText("bigger than the number of tracks");
  });

  test("photos and songs each get their own card", async ({ page }) => {
    test.skip(!existsSync(`${FIXTURES}camera.jpg`), "python3 missing: no sample photo");
    await page.goto("./");
    await add(page, ["camera.jpg", "song.mp3", "song.flac"]);
    await propertiesButton(page).click();
    await expect(page.locator(".ph-form")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".mf-form")).toBeHidden();
    await page.locator(".prop-file").selectOption({ index: 1 });
    await expect(page.locator(".mf-form")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".ph-form")).toBeHidden();
    await expect(page.locator("#mf-title")).toHaveValue("Song");
    await expect(page.locator(".mf-form")).toContainText("Apply to all 2 songs"); // the photo isn't counted
    await expect(page.locator(".mf-limit")).toContainText("lyrics");
    await page.locator(".prop-file").selectOption({ index: 0 });
    await expect(page.locator(".ph-form")).toBeVisible();
    await expect(page.locator(".mf-form")).toBeHidden();
  });

  test("no form for songs it can't change", async ({ page }) => {
    test.skip(!existsSync(`${FIXTURES}tone.wav`), "ffmpeg missing");
    await page.goto("./");
    await add(page, ["tone.wav"]);
    await propertiesButton(page).click();
    await expect(page.locator('.prop-card[data-title="File"]')).toBeVisible();
    await expect(page.locator(".mf-form")).toBeHidden();
  });
});

test.describe("change video details", () => {
  test.skip(!existsSync(`${FIXTURES}clip.mp4`), "ffmpeg missing: no sample video");
  test.use({ timezoneId: "Europe/Copenhagen" }); // the typed date is local time: UTC+2 in June

  /** What the desktop app reads from ``path`` (null when no Python is found). */
  const desktopReads = async (path: string): Promise<Record<string, unknown> | null> => {
    const python = findPython("import PIL");
    if (!python) return null;
    const { execFileSync } = await import("node:child_process");
    try {
      const out = execFileSync(python, ["-c",
        "import sys, json; sys.path.insert(0, sys.argv[1]); from dataclasses import asdict; from pathlib import Path; " +
        "from umc.properties.video import read_details; print(json.dumps(asdict(read_details(Path(sys.argv[2])))))",
        fileURLToPath(new URL("../../src", import.meta.url)), path]).toString();
      return JSON.parse(out);
    } catch {
      return null; // no ffprobe for the desktop reader
    }
  };

  /** The download under its own name (the desktop reader goes by the extension). */
  const named = async (file: Download): Promise<string> => {
    const { mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const path = `${mkdtempSync(`${tmpdir()}/umc-video-`)}/${file.suggestedFilename()}`;
    await file.saveAs(path);
    return path;
  };

  test("change title, date and rotation, download; the desktop reads the same", async ({ page }) => {
    await page.goto("./");
    await add(page, ["clip.mp4"]);
    await propertiesButton(page).click();
    const form = page.locator(".vf-form");
    await expect(form).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".ph-form")).toBeHidden();
    await expect(page.locator(".mf-form")).toBeHidden();
    const save = form.getByRole("button", { name: "Download with changes" });
    await expect(save).toBeDisabled();
    await page.locator("#vf-title").fill("Beach day");
    await page.locator("#vf-description").fill("Two lines\nof notes");
    await page.locator("#vf-date").fill("2026-06-01 12:00");
    await page.locator("#vf-rotation").selectOption("90");
    const download = page.waitForEvent("download");
    await save.click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("clip.mp4");
    await expect(form).toContainText("Downloaded “clip.mp4”", { timeout: 60_000 });
    // the queue holds the changed video: the form shows its details now
    await expect(page.locator("#vf-title")).toHaveValue("Beach day");
    await expect(page.locator("#vf-description")).toHaveValue("Two lines\nof notes");
    await expect(page.locator("#vf-date")).toHaveValue("2026-06-01 12:00");
    await expect(page.locator("#vf-rotation")).toHaveValue("90");
    expect(await card(page, "Video")).toMatchObject({ Rotation: "90°" });
    const d = await desktopReads(await named(file));
    if (d) expect(d).toMatchObject({ title: "Beach day", description: "Two lines\nof notes", date: "2026-06-01T10:00:00Z", rotation: 90 });
  });

  test("tracks: languages and the default sound; two videos give a ZIP", async ({ page }) => {
    test.skip(!existsSync(`${FIXTURES}tracks.mkv`), "no sample with tracks");
    await page.goto("./");
    await add(page, ["tracks.mkv"]);
    await propertiesButton(page).click();
    await expect(page.locator(".vf-form")).toBeVisible({ timeout: 60_000 });
    await expect(page.locator("#vf-title")).toHaveValue("Holiday");
    await expect(page.locator("#vf-rotation")).toBeHidden(); // MKV has no rotation tag
    const ids = await page.locator(".vf-tracks select[id^='vf-language-']").evaluateAll((els) => els.map((e) => e.id));
    expect(ids).toHaveLength(3);
    await expect(page.locator(`#${ids[0]}`)).toHaveValue("eng");
    await expect(page.locator(`#${ids[1]}`)).toHaveValue("");
    await expect(page.locator(`#${ids[2]}`)).toHaveValue("dan");
    await page.locator(`#${ids[1]}`).selectOption("fre");
    const second = ids[1].replace("vf-language-", "");
    await page.locator("#vf-default-audio").selectOption(second);
    await page.locator("#vf-default-subtitle").selectOption(ids[2].replace("vf-language-", ""));
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download with changes" }).click();
    const file = await download;
    await expect(page.locator(".vf-note")).toContainText("Downloaded", { timeout: 60_000 });
    await expect(page.locator(`#${ids[1]}`)).toHaveValue("fre");
    await expect(page.locator("#vf-default-audio")).toHaveValue(second);
    const d = await desktopReads(await named(file));
    if (d) {
      const tracks = d.tracks as { language: string; default: boolean; kind: string }[];
      expect(tracks.map((t) => [t.kind, t.language, t.default])).toEqual([["audio", "eng", false], ["audio", "fre", true], ["subtitle", "dan", true]]);
    }
  });

  test("several videos: apply to all, a ZIP download", async ({ page }) => {
    test.skip(!existsSync(`${FIXTURES}tracks.mkv`), "no second sample");
    await page.goto("./");
    await add(page, ["clip.mp4", "tracks.mkv"]);
    await propertiesButton(page).click();
    await expect(page.locator(".vf-form")).toBeVisible({ timeout: 60_000 });
    await page.locator("#vf-apply-all").check();
    await expect(page.locator("#vf-title")).toHaveAttribute("placeholder", "Several values");
    await expect(page.locator("#vf-rotation")).toBeHidden(); // not every video can be turned
    await expect(page.locator(".vf-tracks")).toBeHidden();
    await page.locator("#vf-title").fill("Same for both");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download changed videos (.zip)" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("videos (changed details).zip");
    const { unzipSync } = await import("fflate");
    const { readFileSync, writeFileSync, mkdtempSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const zip = unzipSync(new Uint8Array(readFileSync(await file.path())));
    expect(Object.keys(zip).sort()).toEqual(["clip.mp4", "tracks.mkv"]);
    const dir = mkdtempSync(`${tmpdir()}/umc-video-`);
    for (const [name, data] of Object.entries(zip)) {
      writeFileSync(`${dir}/${name}`, data);
      const d = await desktopReads(`${dir}/${name}`);
      if (d) expect(d).toMatchObject({ title: "Same for both" });
    }
  });

  test("a bad date is refused in plain words", async ({ page }) => {
    await page.goto("./");
    await add(page, ["clip.mp4"]);
    await propertiesButton(page).click();
    await expect(page.locator(".vf-form")).toBeVisible({ timeout: 60_000 });
    await page.locator("#vf-date").fill("next tuesday");
    await expect(page.locator("#vf-date")).toHaveClass(/invalid/);
    await page.getByRole("button", { name: "Download with changes" }).click();
    await expect(page.locator(".vf-note")).toContainText("Write the date like");
  });

  test("no form for videos it can't change", async ({ page }) => {
    test.skip(!existsSync(`${FIXTURES}clip.avi`), "no AVI sample");
    await page.goto("./");
    await add(page, ["clip.avi"]);
    await propertiesButton(page).click();
    await expect(page.locator('.prop-card[data-title="File"]')).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".vf-form")).toBeHidden();
  });
});

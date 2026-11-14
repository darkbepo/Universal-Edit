// File-type badges orbiting the empty drop zone; a real drop bursts them before the files show.
import { expect, test } from "@playwright/test";

/** Drops a file on the page; returns whether the burst started (read in the same tick). */
const dropFile = ([name, junk]: [string, boolean]) => {
  const dt = new DataTransfer();
  // junk = binary of no known kind (text of an unknown kind is accepted as plain text)
  const bytes = junk ? new Uint8Array([0, 1, 2, 0, 0xff]) : new Uint8Array([0x89, 0x50, 0x4e, 0x47]);
  dt.items.add(new File([bytes], name, { type: junk ? "" : "image/png" }));
  for (const type of ["dragover", "drop"]) document.body.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
  return document.querySelector<HTMLElement>(".drop canvas.orbit")?.dataset.burst === "true";
};

test("badges move on the empty page; a drop bursts them, then the file is listed", async ({ page }) => {
  await page.goto("./");
  const orbit = page.locator(".drop canvas.orbit");
  await expect(orbit).toBeVisible();
  await expect(orbit).toHaveAttribute("data-moving", "true");
  await expect(orbit).toHaveAttribute("aria-hidden", "true");
  const before = Date.now();
  expect(await page.evaluate(dropFile, ["dropped.png", false] as [string, boolean])).toBe(true);
  await expect(page.locator(".queue li")).toHaveCount(1);
  expect(Date.now() - before).toBeGreaterThanOrEqual(550); // waited for the burst
  await expect(orbit).toBeHidden();
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(orbit).toBeVisible();
});

test("with reduce motion the badges stand still and a drop adds at once", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  const orbit = page.locator(".drop canvas.orbit");
  await expect(orbit).toHaveAttribute("data-moving", "false");
  expect(await page.evaluate(dropFile, ["dropped.png", false] as [string, boolean])).toBe(false); // no burst, no waiting
  await expect(page.locator(".queue li")).toHaveCount(1);
});

test("a drop that adds nothing brings the badges back", async ({ page }) => {
  await page.goto("./");
  const orbit = page.locator(".drop canvas.orbit");
  expect(await page.evaluate(dropFile, ["notes.xyz", true] as [string, boolean])).toBe(true);
  await expect(orbit).not.toHaveAttribute("data-burst", "true");
  await expect(page.locator(".queue li")).toHaveCount(0);
  await expect(orbit).toBeVisible();
  await expect(orbit).toHaveAttribute("data-moving", "true");
});

test("no animation behind an editor opened from a tile", async ({ page }) => {
  await page.goto("./");
  await page.locator(".editor-tile[data-editor=model]").click();
  await expect(page.locator(".drop canvas.orbit")).toHaveAttribute("data-moving", "false");
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".drop canvas.orbit")).toHaveAttribute("data-moving", "true");
});

test("clicking the box still opens the file picker through the badges", async ({ page }) => {
  await page.goto("./");
  const chooser = page.waitForEvent("filechooser");
  await page.locator(".drop").click({ position: { x: 60, y: 60 } });
  await chooser;
});

// File-type badges orbiting the empty drop zone; a real drop bursts them before the files show.
import { expect, test } from "@playwright/test";

const dropFile = (name: string) => {
  const dt = new DataTransfer();
  dt.items.add(new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type: "image/png" }));
  for (const type of ["dragover", "drop"]) document.body.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
};

test("badges move on the empty page; a drop bursts them, then the file is listed", async ({ page }) => {
  await page.goto("./");
  const orbit = page.locator(".drop canvas.orbit");
  await expect(orbit).toBeVisible();
  await expect(orbit).toHaveAttribute("data-moving", "true");
  await expect(orbit).toHaveAttribute("aria-hidden", "true");
  const before = Date.now();
  await page.evaluate(dropFile, "dropped.png");
  await expect(page.locator(".queue li")).toHaveCount(1);
  expect(Date.now() - before).toBeGreaterThanOrEqual(350); // waited for the burst
  await expect(orbit).toBeHidden();
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(orbit).toBeVisible();
});

test("with reduce motion the badges stand still and a drop adds at once", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  const orbit = page.locator(".drop canvas.orbit");
  await expect(orbit).toHaveAttribute("data-moving", "false");
  await page.evaluate(dropFile, "dropped.png");
  await expect(page.locator(".queue li")).toHaveCount(1, { timeout: 300 });
});

test("clicking the box still opens the file picker through the badges", async ({ page }) => {
  await page.goto("./");
  const chooser = page.waitForEvent("filechooser");
  await page.locator(".drop").click({ position: { x: 60, y: 60 } });
  await chooser;
});

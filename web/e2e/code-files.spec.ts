import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// Code files are accepted: they queue, can be packed, and Edit opens the code editor.
test("a CSS file is accepted and packs into a ZIP", async ({ page }) => {
  await page.goto("./");
  await page.locator('input[type="file"]').first().setInputFiles([
    { name: "style.css", mimeType: "text/css", buffer: Buffer.from("a { color: red; }\n") },
  ]);
  await expect(page.locator(".queue li")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeEnabled();
  const download = page.waitForEvent("download");
  await page.locator('.pill[data-target="pack:zip"]').click();
  await page.getByRole("button", { name: "Convert", exact: true }).click();
  await expect(page.locator(".status")).toHaveClass(/success/, { timeout: 60_000 });
  const data = readFileSync(await (await download).path());
  expect(data.subarray(0, 2).toString()).toBe("PK");
});

test("a binary file of an unknown kind is still refused", async ({ page }) => {
  await page.goto("./");
  await page.locator('input[type="file"]').first().setInputFiles([
    { name: "blob.dat", mimeType: "application/octet-stream", buffer: Buffer.from([0, 1, 2, 0, 255]) },
  ]);
  await expect(page.locator(".queue li")).toHaveCount(0);
});

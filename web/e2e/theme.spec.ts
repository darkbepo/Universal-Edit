// The sun / moon button in the top bar: System → Light → Dark, remembered across visits.
import { expect, test, type Page } from "@playwright/test";

const background = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const LIGHT_BG = "rgb(243, 244, 247)"; // --bg in styles.css / LIGHT["bg"] in theme.py
const DARK_BG = "rgb(15, 16, 20)";

test("theme button cycles System → Light → Dark and is remembered after a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("./");
  const button = page.getByRole("button", { name: /^Theme:/ });
  await expect(button).toHaveAccessibleName("Theme: Same as the computer");
  expect(await background(page)).toBe(LIGHT_BG);
  await page.emulateMedia({ colorScheme: "dark" }); // following the computer
  await expect.poll(() => background(page)).toBe(DARK_BG);

  await button.click();
  await expect(button).toHaveAccessibleName("Theme: Light");
  await expect.poll(() => background(page)).toBe(LIGHT_BG); // light although the computer is dark
  await button.click();
  await expect(button).toHaveAccessibleName("Theme: Dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect.poll(() => background(page)).toBe(DARK_BG);

  await page.reload();
  // applied by the inline script in index.html, before the app's own code runs
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  await expect(page.getByRole("button", { name: "Theme: Dark" })).toBeVisible();
  expect(await background(page)).toBe(DARK_BG);

  await page.getByRole("button", { name: "Theme: Dark" }).click();
  await expect(page.getByRole("button", { name: "Theme: Same as the computer" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("umc-theme"))).toBeNull();
});

test("theme button fits the phone top bar", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("./");
  const button = page.getByRole("button", { name: /^Theme:/ });
  await expect(button).toBeVisible();
  const box = (await button.boundingBox())!;
  const mode = (await page.locator(".topbar .mode").boundingBox())!;
  const logo = (await page.locator(".topbar .logo").boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(logo.x); // nothing overlaps
  expect(logo.x + logo.width).toBeLessThanOrEqual(mode.x);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

// System / Light / Dark — same choices, labels and icons as the desktop app (src/umc/gui/theme.py).
// "light" / "dark" set <html data-theme>, which styles.css reads; "system" removes it so the
// prefers-color-scheme media query decides. index.html applies the saved choice with a tiny
// inline script before the first paint (keep its storage key and values in step with this file).
import { icon } from "./icons";
import type { IconName } from "./icon_data";

export type ThemeChoice = "system" | "light" | "dark";
export const CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];
export const LABELS: Record<ThemeChoice, string> = { system: "Same as the computer", light: "Light", dark: "Dark" };
export const ICONS: Record<ThemeChoice, IconName> = { system: "sun-moon", light: "sun", dark: "moon" };
export const STORAGE_KEY = "umc-theme";

const valid = (v: unknown): v is ThemeChoice => typeof v === "string" && (CHOICES as readonly string[]).includes(v);

/** System → Light → Dark → System. */
export function nextChoice(current: string): ThemeChoice {
  const i = CHOICES.indexOf(current as ThemeChoice);
  return CHOICES[(i + 1) % CHOICES.length];
}

/** The remembered choice; "system" when nothing (or something broken) is stored, or storage is blocked. */
export function savedChoice(storage: Pick<Storage, "getItem"> | null = safeStorage()): ThemeChoice {
  try {
    const v = storage?.getItem(STORAGE_KEY);
    return valid(v) ? v : "system";
  } catch {
    return "system";
  }
}

export function currentChoice(root: HTMLElement = document.documentElement): ThemeChoice {
  const v = root.dataset.theme;
  return valid(v) ? v : "system";
}

export function applyChoice(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

/** Apply and remember (best effort: a private window may refuse storage). */
export function setChoice(choice: ThemeChoice, storage: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()): void {
  applyChoice(choice);
  try {
    if (choice === "system") storage?.removeItem(STORAGE_KEY);
    else storage?.setItem(STORAGE_KEY, choice);
  } catch {
    /* not remembered, still applied */
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** The sun / moon button in the top bar: one click moves on to the next choice. */
export function themeButton(): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn theme-btn";
  const show = () => {
    const choice = currentChoice();
    const following = nextChoice(choice);
    button.replaceChildren(icon(ICONS[choice], 18));
    button.title = `Theme: ${LABELS[choice]} — click for ${LABELS[following].toLowerCase()}`;
    button.setAttribute("aria-label", `Theme: ${LABELS[choice]}`);
    button.dataset.choice = choice;
  };
  button.addEventListener("click", () => {
    setChoice(nextChoice(currentChoice()));
    show();
  });
  show();
  return button;
}

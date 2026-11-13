// "← Back to Convert" for the editors' top bars. On phones only "← Back" shows (styles.css hides
// .back-long below 560 px), so the bar's other buttons fit on one row.
export function backLabel(button: HTMLElement): HTMLElement {
  const long = document.createElement("span");
  long.className = "back-long";
  long.textContent = " to Convert";
  button.replaceChildren("← Back", long);
  return button;
}

/** True on a phone-sized window (the width where styles.css switches to its phone layout). */
export const isPhone = (): boolean => typeof matchMedia === "function" && matchMedia("(max-width: 560px)").matches;

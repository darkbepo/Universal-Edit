// @vitest-environment jsdom
// Task 8: the formatting bar, the table menu and find & replace (ports of the desktop's
// test_gui_doceditor.py format / find bar tests).
import { TextSelection } from "prosemirror-state";
import { undo } from "prosemirror-history";
import { describe, expect, test, vi } from "vitest";
import type { OpenedDocument } from "../src/doceditor/types";
import * as cmds from "../src/ui/doceditor/commands";
import { FindBar, scanMatches } from "../src/ui/doceditor/findbar";
import { FormatBar, browserAsker, type Asker } from "../src/ui/doceditor/formatbar";
import { DocView } from "../src/ui/doceditor/view";

const opened = (html: string): OpenedDocument => ({
  html,
  images: new Map(),
  sourceFormat: "html",
  warnings: [],
  frontMatter: "",
  title: "",
  template: null,
});

function viewOf(html: string): DocView {
  const v = new DocView(document.createElement("div"));
  v.load(opened(html));
  return v;
}
const select = (v: DocView, from: number, to = from) =>
  v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, from, to)));
const out = (v: DocView) => v.content().html;
const selected = (v: DocView) => {
  const { from, to } = v.view.state.selection;
  return v.view.state.doc.textBetween(from, to);
};
const undoDepth = (v: DocView) => {
  let n = 0;
  let s = v.view.state;
  while (undo(s, (tr) => (s = s.apply(tr)))) n++;
  return n;
};

/** An asker answering from fixed values. */
function asker(answers: Partial<{ text: string | null; color: string | null; table: [number, number] | null }> = {}): Asker {
  return {
    text: async () => answers.text ?? null,
    color: async () => answers.color ?? null,
    file: async () => null,
    tableSize: async () => answers.table ?? null,
  };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("FormatBar", () => {
  test("has the desktop's controls", () => {
    const bar = new FormatBar(viewOf("<p>a</p>"), asker());
    for (const key of ["bold", "italic", "underline", "strike", "left", "center", "right", "justify", "bullet", "number"]) {
      expect(bar.buttons[key], key).toBeInstanceOf(HTMLButtonElement);
    }
    expect([...bar.styleBox.options].filter((o) => !o.hidden).map((o) => o.text)).toEqual(["Normal", "Heading 1", "Heading 2", "Heading 3", "Quote", "Code"]);
    const sizes = [...bar.sizeBox.options].filter((o) => !o.hidden).map((o) => Number(o.value));
    expect(sizes[0]).toBe(8);
    expect(sizes[sizes.length - 1]).toBe(72);
    expect([...bar.tableMenu.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "Insert row above",
      "Insert row below",
      "Insert column left",
      "Insert column right",
      "Delete row",
      "Delete column",
      "Delete table",
      "Merge selected cells",
      "Split cell",
    ]);
    const tips = [...bar.el.querySelectorAll("button")].map((b) => b.title);
    for (const tip of ["Text colour", "Highlight", "Insert link", "Indent", "Outdent", "Insert picture", "Insert table", "Page break", "Horizontal line", "Clear formatting"]) {
      expect(tips, tip).toContain(tip);
    }
  });

  test("follows the cursor (bold, alignment, style, lists)", () => {
    const v = viewOf("<p><b>hello</b> world</p>");
    const bar = new FormatBar(v, asker());
    select(v, 2);
    expect(bar.buttons.bold.getAttribute("aria-pressed")).toBe("true");
    select(v, 9);
    expect(bar.buttons.bold.getAttribute("aria-pressed")).toBe("false");
    v.run(cmds.setAlign("center"));
    expect(bar.buttons.center.getAttribute("aria-pressed")).toBe("true");
    expect(bar.buttons.left.getAttribute("aria-pressed")).toBe("false");
    v.run(cmds.setBlockStyle("h2"));
    expect(bar.styleBox.selectedOptions[0].text).toBe("Heading 2");
    v.run(cmds.setBlockStyle("normal"));
    v.run(cmds.toggleList("bullet"));
    expect(bar.buttons.bullet.getAttribute("aria-pressed")).toBe("true");
    expect(bar.buttons.number.getAttribute("aria-pressed")).toBe("false");
  });

  test("buttons and the style box edit the document", () => {
    const v = viewOf("<p>hello world</p>");
    const bar = new FormatBar(v, asker());
    select(v, 1, 6);
    bar.buttons.italic.click();
    expect(out(v)).toBe("<p><i>hello</i> world</p>");
    bar.styleBox.value = "h1";
    bar.styleBox.dispatchEvent(new Event("change"));
    expect(out(v).startsWith("<h1>")).toBe(true);
    bar.buttons.right.click();
    expect(out(v)).toContain("text-align:right");
  });

  test("colour and link ask first", async () => {
    const v = viewOf("<p>hello world</p>");
    const bar = new FormatBar(v, asker({ color: "#00ff00", text: "https://x.org/" }));
    select(v, 1, 6);
    bar.buttons.color.click();
    await flush();
    expect(out(v)).toContain("color:#00ff00");
    select(v, 1, 6);
    bar.buttons.link.click();
    await flush();
    expect(out(v)).toContain('<a href="https://x.org/"');
    // cancelled, or the untouched "https://": nothing
    const w = viewOf("<p>ab</p>");
    const before = out(w);
    const b2 = new FormatBar(w, asker({ text: "https://" }));
    select(w, 1, 2);
    b2.buttons.link.click();
    await flush();
    expect(out(w)).toBe(before);
  });

  test("the size box applies once, only on a change", () => {
    const mixed = '<p><span style="font-size:10pt">a</span><span style="font-size:20pt">b</span></p>';
    const v = viewOf(mixed);
    const bar = new FormatBar(v, asker());
    select(v, 1, 3);
    const steps = undoDepth(v);
    bar.sizeBox.dispatchEvent(new Event("focus"));
    bar.sizeBox.dispatchEvent(new Event("blur")); // clicked in and away: nothing
    expect(out(v)).toBe(mixed);
    expect(undoDepth(v)).toBe(steps);
    bar.sizeBox.value = "14";
    bar.sizeBox.dispatchEvent(new Event("input"));
    bar.sizeBox.dispatchEvent(new Event("change"));
    bar.sizeBox.dispatchEvent(new Event("blur"));
    expect(out(v)).toBe('<p><span style="font-size:14pt">ab</span></p>');
    expect(undoDepth(v)).toBe(steps + 1); // one step undoes the whole choice
    v.run(undo);
    expect(out(v)).toBe(mixed);
  });

  test("the table menu works only in a table", async () => {
    const v = viewOf("<p>ab</p>");
    const bar = new FormatBar(v, asker({ table: [2, 2] }));
    select(v, 2);
    expect(bar.tableMenu.classList.contains("disabled")).toBe(true);
    expect([...bar.tableMenu.querySelectorAll("button")].every((b) => b.disabled)).toBe(true);
    bar.tableMenu.open = true;
    bar.tableMenu.dispatchEvent(new Event("toggle"));
    expect(bar.tableMenu.open).toBe(false);
    bar.buttons.table.click();
    await flush();
    expect(out(v).match(/<tr>/g)?.length).toBe(2);
    expect(bar.tableMenu.classList.contains("disabled")).toBe(false);
    const items = [...bar.tableMenu.querySelectorAll("button")];
    expect(items[1].disabled).toBe(false);
    items[1].click(); // row below
    expect(out(v).match(/<tr>/g)?.length).toBe(3);
  });

  test("the font box lists common fonts and those of the document", () => {
    const v = viewOf('<p><span style="font-family:Comic Neue">a</span>b</p>');
    const bar = new FormatBar(v, asker());
    bar.fontBox.dispatchEvent(new Event("focus"));
    const names = [...bar.fontBox.options].map((o) => o.value);
    expect(names).toContain("Arial");
    expect(names).toContain("Comic Neue");
    select(v, 3);
    bar.fontBox.value = "Georgia";
    bar.fontBox.dispatchEvent(new Event("change"));
    v.view.dispatch(v.view.state.tr.insertText("x"));
    expect(out(v)).toContain('<span style="font-family:Georgia">x</span>');
    select(v, 1);
    expect(bar.fontBox.value).toBe("Comic Neue");
  });

  test("every bar action exports a fixed point", async () => {
    const h =
      '<p>a<b>b</b>c</p><ul><li>x<i>y</i></li></ul><table><tr><td><p>q</p></td><td><p>r</p></td></tr></table><h2>T</h2><p><a href="u"></a>z</p>';
    const v = viewOf(h);
    const bar = new FormatBar(v, asker({ color: "#123456", text: "https://e.org/", table: [1, 2] }));
    const size = () => v.view.state.doc.content.size;
    const clicks = Object.keys(bar.buttons);
    for (const [i, key] of clicks.entries()) {
      for (const [a, b] of [
        [2, 2],
        [1, Math.min(9, size() - 1)],
        [Math.max(1, size() - 4), size() - 1],
      ]) {
        const v2 = viewOf(out(v));
        const bar2 = new FormatBar(v2, asker({ color: "#123456", text: "https://e.org/", table: [1, 2] }));
        try {
          select(v2, a, b);
        } catch {
          continue;
        }
        bar2.buttons[key].click();
        await flush();
        v2.view.state.doc.check();
        const once = out(v2);
        expect(out(viewOf(once)), `${key} ${a}-${b} (${i})`).toBe(once);
      }
    }
    for (const style of ["h1", "quote", "code", "normal"]) {
      select(v, 2);
      bar.styleBox.value = style;
      bar.styleBox.dispatchEvent(new Event("change"));
      const once = out(v);
      expect(out(viewOf(once)), style).toBe(once);
    }
  });
});

describe("FindBar", () => {
  const text = "<p>a cat. A cat.</p>";

  test("find wraps around; no match says so", () => {
    const v = viewOf(text);
    const bar = new FindBar(v);
    expect(bar.find("cat")).toBe(true);
    expect(v.view.state.selection.from).toBe(3);
    expect(bar.find("cat")).toBe(true);
    expect(v.view.state.selection.from).toBe(10);
    expect(bar.find("cat")).toBe(true); // the third wraps to the first
    expect(v.view.state.selection.from).toBe(3);
    expect(bar.find("dog")).toBe(false);
    expect(bar.status.textContent).toBe("No matches.");
  });

  test("backward and match case", () => {
    const v = viewOf(text);
    const bar = new FindBar(v);
    select(v, 1);
    expect(bar.find("cat", { backward: true })).toBe(true);
    expect(v.view.state.selection.from).toBe(10);
    expect(bar.find("CAT", { matchCase: true })).toBe(false);
    expect(bar.find("CAT")).toBe(true);
  });

  test("replace all with match case and whole word is one undo step", () => {
    const v = viewOf(text);
    const bar = new FindBar(v);
    expect(bar.replaceAll("a", "the", { matchCase: true, wholeWord: true })).toBe(1);
    expect(out(v)).toBe("<p>the cat. A cat.</p>");
    expect(bar.status.textContent).toBe("Replaced 1.");
    v.run(undo);
    expect(out(v)).toBe(text);
    expect(bar.replaceAll("cat", "dog")).toBe(2);
    expect(out(v)).toBe("<p>a dog. A dog.</p>");
    v.run(undo);
    expect(out(v)).toBe(text);
    expect(bar.replaceAll("zzz", "y")).toBe(0);
    expect(bar.status.textContent).toBe("No matches.");
  });

  test('"cat" → "cats" ends, and "$" is kept as typed', () => {
    const v = viewOf("<p>cat cat cat</p>");
    const bar = new FindBar(v);
    expect(bar.replaceAll("cat", "cats")).toBe(3);
    expect(out(v)).toBe("<p>cats cats cats</p>");
    expect(bar.replaceAll("cats", "$&$1$$")).toBe(3);
    expect(out(v)).toBe("<p>$&amp;$1$$ $&amp;$1$$ $&amp;$1$$</p>");
  });

  test("replace: the selected match, then the next is selected", () => {
    const v = viewOf(text);
    const bar = new FindBar(v);
    expect(bar.replace("cat", "dog")).toBe(false); // nothing selected: only finds the first
    expect(out(v)).toBe(text);
    expect(bar.replace("cat", "dog")).toBe(true);
    expect(out(v)).toBe("<p>a dog. A cat.</p>");
    expect(selected(v)).toBe("cat");
    v.run(undo);
    expect(out(v)).toBe(text);
  });

  test("replacing keeps the formatting of the match", () => {
    const v = viewOf("<p>x <b>cat</b> y</p>");
    const bar = new FindBar(v);
    bar.replaceAll("cat", "dog");
    expect(out(v)).toBe("<p>x <b>dog</b> y</p>");
  });

  test("matches across an empty link, not across pictures or line breaks", () => {
    let v = viewOf('<p>say a <a href="u"></a> b now</p>');
    let bar = new FindBar(v);
    expect(bar.find("a  b")).toBe(true);
    expect(selected(v)).toBe("a  b");
    expect(bar.find("A  B")).toBe(true);
    expect(bar.find("A  B", { matchCase: true })).toBe(false);
    expect(bar.replaceAll("now", "then")).toBe(1);
    v = viewOf('<p>category <a href="u"></a>cat</p>');
    bar = new FindBar(v);
    expect(bar.find("cat", { wholeWord: true })).toBe(true);
    expect(v.view.state.selection.to).toBe(v.view.state.doc.content.size - 1);
    expect(new FindBar(viewOf("<p>category</p>")).find("cat", { wholeWord: true })).toBe(false);
    expect(new FindBar(viewOf("<p>ab<a href='u'></a>cd</p>")).find("ab", { wholeWord: true })).toBe(false);
    expect(new FindBar(viewOf("<p>cat2 cat</p>")).replaceAll("cat", "x", { wholeWord: true })).toBe(1);
    expect(new FindBar(viewOf('<p>b<img src="img/1.png">c</p>')).find("bc")).toBe(false);
    expect(new FindBar(viewOf("<p>b<br>c</p>")).find("bc")).toBe(false);
  });

  test("a space finds a no-break space too, and odd characters are plain text", () => {
    expect(new FindBar(viewOf("<p>a&nbsp; b</p>")).find("a  b")).toBe(true);
    expect(new FindBar(viewOf("<p>(a+b)*[c]-{d}|e^f$g.h?/\\</p>")).find("(a+b)*[c]-{d}|e^f$g.h?/\\")).toBe(true);
    expect(new FindBar(viewOf("<p>a😀b</p>")).find("😀")).toBe(true);
  });

  test("open, Enter, Shift+Enter and Esc", () => {
    const v = viewOf(text);
    const bar = new FindBar(v);
    expect(bar.el.hidden).toBe(true);
    select(v, 3, 6);
    bar.open(true);
    expect(bar.el.hidden).toBe(false);
    expect(bar.findInput.value).toBe("cat");
    expect(bar.replaceInput.hidden).toBe(false);
    bar.open(false);
    expect(bar.replaceInput.hidden).toBe(true);
    const key = (k: string, shift = false) =>
      bar.findInput.dispatchEvent(new KeyboardEvent("keydown", { key: k, shiftKey: shift, bubbles: true, cancelable: true }));
    select(v, 1);
    key("Enter");
    expect(v.view.state.selection.from).toBe(3);
    key("Enter");
    expect(v.view.state.selection.from).toBe(10);
    key("Enter", true);
    expect(v.view.state.selection.from).toBe(3);
    key("Escape");
    expect(bar.el.hidden).toBe(true);
  });

  test("replacing exports a fixed point", () => {
    const h =
      '<p>a <b><a href="u">b</a></b><a href="v"></a>b <span style="color:#ff0000;font-size:9pt">bb</span></p>' +
      "<ul><li>b<i>b</i></li></ul><pre>b  b</pre><table><tr><td><p>b</p></td></tr></table>";
    for (const [find, repl] of [
      ["b", "  x"],
      ["b b", ""],
      ["bb", "b"],
      ["a b", "Q"],
    ]) {
      const v = viewOf(h);
      new FindBar(v).replaceAll(find, repl);
      v.view.state.doc.check();
      const once = out(v);
      expect(out(viewOf(once)), `${find} -> ${repl}`).toBe(once);
    }
  });

  test("many changed blocks are rebuilt in one step, with the same result", () => {
    const line = '<p>x cat <b>cat</b> y</p><ul><li>a <a href="u"></a>cat</li></ul>';
    const few = viewOf(line);
    new FindBar(few).replaceAll("cat", "dog");
    const many = viewOf(line.repeat(40)); // 80 blocks
    const bar = new FindBar(many);
    expect(bar.replaceAll("cat", "dog")).toBe(120);
    expect(out(many)).toBe(out(few).repeat(40));
    expect(undoDepth(many)).toBe(1);
    many.run(undo);
    expect(out(many)).toBe(viewOf(line.repeat(40)).content().html);
  });
});

describe("Task 8a: find never freezes", () => {
  const E = String.fromCodePoint(0x1f600);

  test("backward find and whole-word Replace all on an emoji", () => {
    const h = `<p>a${E}b ${E} x${E}</p>`;
    let v = viewOf(h);
    let bar = new FindBar(v);
    select(v, 1);
    expect(bar.find(E, { backward: true })).toBe(true);
    expect(selected(v)).toBe(E);
    const last = v.view.state.selection.from;
    expect(bar.find(E, { backward: true })).toBe(true);
    expect(v.view.state.selection.from).toBeLessThan(last);
    expect(bar.find(E)).toBe(true);
    // the emoji between letters and after "x" are inside words
    expect(bar.replaceAll(E, "Z", { wholeWord: true })).toBe(1);
    expect(out(v)).toBe(`<p>a${E}b Z x${E}</p>`);
    v = viewOf(h);
    bar = new FindBar(v);
    expect(bar.find(`${E}b`, { wholeWord: true, backward: true })).toBe(false);
    expect(bar.find(`${E}b`, { wholeWord: true })).toBe(false);
    expect(bar.replaceAll(E, `${E}${E}`)).toBe(3);
    expect(new FindBar(viewOf(`<p>${E}${E}${E}</p>`)).replaceAll(`${E}${E}`, "x")).toBe(1);
  });

  test("every scan advances, whatever it refuses", () => {
    const lone = String.fromCharCode(0xd83d);
    const texts = [`${E}${E}`, `a${E}${lone}b${lone}`, `${lone}${lone}x`, "", "aaa"];
    const patterns = [E, `${E}*`, lone, "a*", "", "."];
    for (const text of texts) {
      for (const pattern of patterns) {
        for (const flags of ["gu", "giu"]) {
          let calls = 0;
          const all = scanMatches(text, pattern, flags, () => {
            calls++;
            return false; // refuse every match
          });
          expect(all).toEqual([]);
          expect(calls, `${pattern} in ${text}`).toBeLessThanOrEqual(text.length + 1);
          const taken = scanMatches(text, pattern, flags, () => true);
          for (let i = 1; i < taken.length; i++) expect(taken[i][0]).toBeGreaterThanOrEqual(taken[i - 1][1]);
        }
      }
    }
  });

  test("forward find finds the match the cursor is in (wrapping without a limit)", () => {
    const v = viewOf("<p>xhello y</p>");
    const bar = new FindBar(v);
    select(v, 4);
    expect(bar.find("hello")).toBe(true);
    expect(selected(v)).toBe("hello");
    select(v, 4);
    expect(bar.find("hello", { backward: true })).toBe(true);
    expect(selected(v)).toBe("hello");
  });

  test("Enter and Esc while composing (IME) are left alone; Close has a name", () => {
    const v = viewOf("<p>a cat. A cat.</p>");
    const bar = new FindBar(v);
    bar.open(false);
    bar.findInput.value = "cat";
    select(v, 1);
    const key = (k: string) => {
      const e = new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, isComposing: true });
      bar.findInput.dispatchEvent(e);
    };
    key("Enter");
    expect(v.view.state.selection.from).toBe(1);
    key("Escape");
    expect(bar.el.hidden).toBe(false);
    expect(bar.el.querySelector(".doc-find-close")?.getAttribute("aria-label")).toBe("Close");
  });
});

describe("Task 8a: colour pickers and bar details", () => {
  const inputs = () => document.body.querySelectorAll('input[type="color"]');

  test("highlight starts at yellow, text colour lets black be chosen", async () => {
    const hl = browserAsker.color("Highlight colour");
    const a = inputs()[inputs().length - 1] as HTMLInputElement;
    expect(a.value).toBe("#ffff01");
    a.value = "#00ff00";
    a.dispatchEvent(new Event("input")); // dragging in the picker
    window.dispatchEvent(new Event("focus")); // closed: the last colour counts
    expect(await hl).toBe("#00ff00");
    expect(inputs().length).toBe(0);
    const text = browserAsker.color("Text colour");
    const b = inputs()[0] as HTMLInputElement;
    expect(b.value).not.toBe("#000000");
    b.value = "#000000";
    b.dispatchEvent(new Event("change"));
    expect(await text).toBe("#000000");
  });

  test("a cancelled pick resolves and leaves no input behind", async () => {
    const first = browserAsker.color("Text colour");
    const second = browserAsker.color("Text colour"); // a new pick ends the old one
    expect(await first).toBe(null);
    expect(inputs().length).toBe(1);
    window.dispatchEvent(new Event("focus")); // the picker closed, nothing chosen
    expect(await second).toBe(null);
    expect(inputs().length).toBe(0);
  });

  test("a mixed selection shows a blank style and font, so any choice applies", () => {
    const v = viewOf('<p>aa</p><h1>bb</h1><p><span style="font-family:Arial">cc</span>dd</p>');
    const bar = new FormatBar(v, asker());
    select(v, 2, 6);
    expect(bar.styleBox.value).toBe("");
    bar.styleBox.value = "h1";
    bar.styleBox.dispatchEvent(new Event("change"));
    expect(out(v).startsWith("<h1>aa</h1><h1>bb</h1>")).toBe(true);
    const size = v.view.state.doc.content.size;
    select(v, size - 5, size - 1);
    expect(bar.fontBox.value).toBe("");
    bar.fontBox.dispatchEvent(new Event("focus"));
    bar.fontBox.value = "Arial";
    bar.fontBox.dispatchEvent(new Event("change"));
    expect(out(v)).toContain('<span style="font-family:Arial">ccdd</span>');
    select(v, 2);
    expect(bar.styleBox.value).toBe("h1");
  });

  test("the table menu: Esc closes it, arrow keys move through its items", () => {
    const v = viewOf("<table><tr><td><p>a</p></td></tr></table>");
    const bar = new FormatBar(v, asker());
    document.body.append(bar.el);
    select(v, 3);
    const items = [...bar.tableMenu.querySelectorAll<HTMLButtonElement>(".menu-item")];
    bar.tableMenu.open = true;
    bar.tableMenu.dispatchEvent(new Event("toggle"));
    expect(document.activeElement).toBe(items[0]);
    const key = (k: string) =>
      document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
    key("ArrowDown");
    expect(document.activeElement).toBe(items[1]);
    key("ArrowUp");
    key("ArrowUp");
    expect(document.activeElement).toBe(items[items.length - 1]);
    // opened with the mouse, the focus may still be elsewhere: Esc anywhere closes it
    (document.activeElement as HTMLElement).blur();
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(bar.tableMenu.open).toBe(false);
    bar.el.remove();
  });
});

describe("Task 9: the Task 8a re-review items", () => {
  test("forward find starts at the cursor, so an overlapping match is found", () => {
    const v = viewOf("<p>aaa</p>");
    const bar = new FindBar(v);
    select(v, 2); // after the first a
    expect(bar.find("aa")).toBe(true);
    expect([v.view.state.selection.from, v.view.state.selection.to]).toEqual([2, 4]);
    expect(scanMatches("aaa", "aa", "gu", () => true, 1)).toEqual([[1, 3]]);
  });

  const colourInput = () => document.body.querySelector('input[type="color"]') as HTMLInputElement;

  test("a colour pick that ends on its starting value is no choice; pure yellow can be chosen", async () => {
    const hl = browserAsker.color("Highlight colour");
    const a = colourInput();
    expect(a.value).toBe("#ffff01");
    a.dispatchEvent(new Event("input")); // reported, but still the starting value
    a.dispatchEvent(new Event("cancel"));
    expect(await hl).toBe(null);
    const text = browserAsker.color("Text colour");
    const b = colourInput();
    b.value = "#123456";
    b.dispatchEvent(new Event("input"));
    b.value = "#010101"; // dragged back to where it started
    b.dispatchEvent(new Event("input"));
    window.dispatchEvent(new Event("focus"));
    expect(await text).toBe(null);
    const yellow = browserAsker.color("Highlight colour");
    const c = colourInput();
    c.value = "#ffff00";
    c.dispatchEvent(new Event("change"));
    expect(await yellow).toBe("#ffff00");
  });

  test("a file pick isn't lost when its change comes long after the focus", async () => {
    vi.useFakeTimers();
    try {
      const picked = browserAsker.file("image/png");
      const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
      window.dispatchEvent(new Event("focus"));
      vi.advanceTimersByTime(2000);
      const file = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
      Object.defineProperty(input, "files", { value: [file] });
      input.dispatchEvent(new Event("change"));
      expect(await picked).toBe(file);
      expect(document.body.querySelector('input[type="file"]')).toBe(null);
    } finally {
      vi.useRealTimers();
    }
  });

  test("a file pick ends on cancel, or when a new pick starts", async () => {
    const first = browserAsker.file("image/png");
    (document.body.querySelector('input[type="file"]') as HTMLInputElement).dispatchEvent(new Event("cancel"));
    expect(await first).toBe(null);
    const second = browserAsker.file("image/png");
    const third = browserAsker.file("image/png");
    expect(await second).toBe(null);
    expect(document.body.querySelectorAll('input[type="file"]').length).toBe(1);
    (document.body.querySelector('input[type="file"]') as HTMLInputElement).dispatchEvent(new Event("cancel"));
    expect(await third).toBe(null);
  });
});

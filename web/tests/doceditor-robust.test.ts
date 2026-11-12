// @vitest-environment jsdom
// Task 7a: Enter over selections, commands that delete first, values the export would drop,
// paste fallbacks and references, plain-text paste, Tab, undo grouping, big selections.
import { Slice } from "prosemirror-model";
import { AllSelection, EditorState, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { undo } from "prosemirror-history";
import { describe, expect, test } from "vitest";
import { normaliseHtml } from "../src/doceditor/subset";
import type { OpenedDocument } from "../src/doceditor/types";
import * as cmds from "../src/ui/doceditor/commands";
import { docToHtml, htmlToDoc } from "../src/ui/doceditor/convert";
import { docSchema } from "../src/ui/doceditor/schema";
import { DocView, EDITOR_KEYS, createState } from "../src/ui/doceditor/view";

const NL = String.fromCharCode(10);
const CR = String.fromCharCode(13);
const TAB = String.fromCharCode(9);

// jsdom has no ClipboardEvent (EditorView.pasteHTML makes one)
if (typeof globalThis.ClipboardEvent === "undefined") {
  (globalThis as Record<string, unknown>).ClipboardEvent = class extends Event {
    clipboardData = null;
  };
}

const stateOf = (h: string, from?: number, to = from) => {
  const s = createState(htmlToDoc(normaliseHtml(h).html));
  return from === undefined ? s : s.apply(s.tr.setSelection(TextSelection.create(s.doc, from, to)));
};
const html = (s: EditorState) => docToHtml(s.doc);
function run(s: EditorState, cmd: Command): EditorState {
  let next = s;
  cmd(s, (tr: Transaction) => {
    next = s.apply(tr);
  });
  next.doc.check();
  return next;
}
const selectAll = (s: EditorState) => s.apply(s.tr.setSelection(new AllSelection(s.doc)));

describe("Enter and Shift+Enter over a selection", () => {
  const ol = "<ol><li>ab</li><li>cd</li><li>ef</li></ol>";
  test("the selection is deleted, then the item is split, in one step", () => {
    let s = run(stateOf(ol, 4, 10), EDITOR_KEYS.Enter);
    expect(html(s)).toBe("<ol><li>a</li><li>d</li><li>ef</li></ol>");
    s = run(s, undo);
    expect(html(s)).toBe(ol);
    s = run(stateOf(ol, 4, 10), EDITOR_KEYS["Shift-Enter"]);
    expect(html(s)).toBe("<ol><li>a<br>d</li><li>ef</li></ol>");
  });

  test("selections that made splitBlock throw", () => {
    const d2 = `<ol><li>z <u><b></b>${TAB}</u>x  y&amp;<b>b c<i></i>a</b></li><li><u><b></b></u><img src="img/2.png"></li></ol>`;
    const size = stateOf(d2).doc.content.size;
    for (const key of ["Enter", "Shift-Enter", "Mod-Enter"]) {
      for (const [a, b] of [
        [3, 23],
        [3, 12],
        [1, 20],
        [0, size],
      ]) {
        expect(() => run(stateOf(d2, a, Math.min(b, size)), EDITOR_KEYS[key]), `${key} ${a}-${b}`).not.toThrow();
      }
    }
    expect(html(run(selectAll(stateOf(ol)), EDITOR_KEYS.Enter))).toBe("<p></p><p></p>");
    // deleting everything in the list leaves only an empty list: Enter makes an item there, and
    // Enter in an empty item ends the list (as on the desktop): one empty line is left
    expect(EDITOR_KEYS.Enter(stateOf(d2, 3, 23))).toBe(true);
    const after = run(stateOf(d2, 3, 23), EDITOR_KEYS.Enter);
    expect(after.doc.toString()).toBe("doc(paragraph)");
  });

  test("a page break after deleting a selection that leaves no text block", () => {
    const d1 = `<ol><li style="text-align:right;page-break-before:always"></li><li></li><li style="page-break-before:always">${TAB}</li></ol><hr><p></p>`;
    expect(() => run(stateOf(d1, 3, 12), cmds.insertPageBreak)).not.toThrow();
    expect(() => run(stateOf(ol, 3, 12), cmds.insertPageBreak)).not.toThrow();
    expect(() => run(selectAll(stateOf(d1)), cmds.insertPageBreak)).not.toThrow();
  });
});

describe("values the export would drop are refused or made canonical", () => {
  const sel = () => stateOf("<p>hello world</p>", 1, 6);
  test("links", () => {
    for (const href of ["javascript:alert(1)", " JavaScript:x", "data:text/html,x", "", "  ", "vbscript:x"]) {
      expect(cmds.insertLink(href)(sel()), href).toBe(false);
      expect(cmds.insertLink(href, "t")(stateOf("<p>ab</p>", 2)), href).toBe(false);
    }
    for (const href of ["https://x.org/", "mailto:a@b.c", "page.html#top", "tel:1"]) {
      expect(html(run(sel(), cmds.insertLink(href)))).toBe(`<p><a href="${href}">hello</a> world</p>`);
    }
  });

  test("colours, sizes, fonts", () => {
    expect(html(run(sel(), cmds.setColor("red")))).toBe('<p><span style="color:#ff0000">hello</span> world</p>');
    expect(html(run(sel(), cmds.setColor("#F00")))).toBe('<p><span style="color:#ff0000">hello</span> world</p>');
    expect(cmds.setColor("nonsense")(sel())).toBe(false);
    expect(cmds.setHighlight("nonsense")(sel())).toBe(false);
    expect(cmds.setSize(0)(sel())).toBe(false);
    expect(cmds.setSize(5000)(sel())).toBe(false);
    expect(cmds.setSize(Number.NaN)(sel())).toBe(false);
    const s = run(sel(), cmds.setSize(10.257));
    expect(cmds.activeState(s).size).toBe(10.26);
    expect(html(s)).toBe('<p><span style="font-size:10.26pt">hello</span> world</p>');
    const f = run(sel(), cmds.setFont('My "Font"; x, serif'));
    expect(cmds.activeState(f).font).toBe("My Font x");
    expect(cmds.setFont('"";')(sel())).toBe(false);
  });

  test("pictures", () => {
    const s = stateOf("<p>hello</p>", 3);
    expect(cmds.insertImage("https://evil/x.png")(s)).toBe(false);
    expect(cmds.insertImage('img/"x.png')(s)).toBe(false);
    expect(html(run(s, cmds.insertImage("img/1.png", 0, 99999999)))).toBe('<p>he<img src="img/1.png">llo</p>');
  });
});

// ----- the view -------------------------------------------------------------------------------

const PNG = Uint8Array.from(
  atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="),
  (c) => c.charCodeAt(0),
);
const b64 = (data: Uint8Array) => btoa(Array.from(data, (c) => String.fromCharCode(c)).join(""));
const opened = (h: string, images = new Map<string, Uint8Array>()): OpenedDocument => ({
  html: h,
  images,
  sourceFormat: "html",
  warnings: [],
  frontMatter: "",
  title: "",
  template: null,
});
function viewAt(h: string, pos: number, images?: Map<string, Uint8Array>): DocView {
  const v = new DocView(document.createElement("div"));
  v.load(opened(h, images));
  v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, pos)));
  return v;
}
/** A paste event carrying `data` (type → text). */
function pasteEvent(data: Record<string, string>): ClipboardEvent {
  const event = new ClipboardEvent("paste");
  Object.defineProperty(event, "clipboardData", {
    value: { getData: (t: string) => data[t] ?? "", files: [], types: Object.keys(data) },
  });
  return event;
}

describe("paste", () => {
  test("HTML the normaliser can't read pastes its plain text", () => {
    const v = viewAt("<p>ab</p>", 2);
    v.normalisePasted = () => {
      throw new Error("unreadable");
    };
    v.view.pasteHTML("<p>x</p>", pasteEvent({ "text/html": "<p>x</p>", "text/plain": `plain${NL}${NL}text` }));
    expect(v.content().html).toBe("<p>aplain</p><p></p><p>textb</p>");
  });

  test("a paste the document can't hold pastes its plain text", () => {
    const v = viewAt("<table><tr><td><p>a</p></td><td><p>bc</p></td></tr></table><p>de</p><p>fg</p>", 4);
    v.view.someProp("handlePaste", (f) => f(v.view, pasteEvent({ "text/html": "<p>q</p>", "text/plain": "q" }), Slice.empty));
    const doc = v.view.state.doc;
    const tr = v.view.state.tr.replace(0, 0, doc.slice(4, 7)).setMeta("uiEvent", "paste");
    expect(() => tr.doc.check()).toThrow(); // ProseMirror's own fitting
    v.view.dispatch(tr);
    expect(v.content().html).toBe("<table><tr><td><p>qa</p></td><td><p>bc</p></td></tr></table><p>de</p><p>fg</p>");
  });

  test("picture references from another document are dropped; this editor's own copy keeps them", () => {
    const pics = new Map([["img/1.png", PNG]]);
    let v = viewAt('<p>a<img src="img/1.png">b</p>', 1, pics);
    v.view.pasteHTML('<p>x<img src="img/1.png">y</p>');
    expect(v.content().html).toBe('<p>xya<img src="img/1.png">b</p>');
    v = viewAt('<p>a<img src="img/1.png">b</p>', 1, pics);
    const doc = v.view.state.doc;
    v.view.pasteHTML(v.clipboardHtml(doc.slice(1, doc.content.size - 1)));
    expect(v.content().html).toBe('<p>a<img src="img/1.png">ba<img src="img/1.png">b</p>');
  });

  test("a pasted data: picture never takes a name the document already points at", () => {
    // img/2.png is used in the text but its bytes are missing
    const v = viewAt('<p>a<img src="img/2.png">b</p>', 1, new Map([["img/1.png", PNG]]));
    const other = Uint8Array.from([...PNG, 1]);
    v.view.pasteHTML(`<p><img src="data:image/png;base64,${b64(other)}"></p>`);
    const c = v.content();
    expect(c.html).toBe('<p><img src="img/3.png">a<img src="img/2.png">b</p>');
    expect(c.images.get("img/3.png")).toEqual(other);
  });

  test("plain text keeps its blank lines", () => {
    const v = viewAt("<p>ab</p>", 2);
    v.view.pasteText(`x${NL}${NL}y${CR}${NL}${CR}${NL}z`);
    expect(v.content().html).toBe("<p>ax</p><p></p><p>y</p><p></p><p>zb</p>");
  });
});

describe("keys", () => {
  test("Tab is a tab character everywhere, as on the desktop; Shift+Tab does nothing", () => {
    let s = run(stateOf("<table><tr><td><p>a</p></td><td><p>b</p></td></tr></table>", 10), EDITOR_KEYS.Tab);
    expect(html(s)).toBe(`<table><tr><td><p>a</p></td><td><p>b${TAB}</p></td></tr></table>`);
    s = run(stateOf("<ul><li>a</li><li>b</li></ul>", 8), EDITOR_KEYS.Tab);
    expect(html(s)).toBe(`<ul><li>a</li><li>${TAB}b</li></ul>`);
    const before = stateOf("<ul><li>a</li><li>b</li></ul>", 8);
    expect(EDITOR_KEYS["Shift-Tab"](before)).toBe(true); // taken, so focus stays in the page
    expect(html(run(before, EDITOR_KEYS["Shift-Tab"]))).toBe("<ul><li>a</li><li>b</li></ul>");
  });

  test("typing after a command is its own undo step (a cursor Ctrl+B counts)", () => {
    const v = viewAt("<p>z</p>", 2);
    const type = (t: string) => v.view.dispatch(v.view.state.tr.insertText(t));
    type("a");
    type("b");
    v.run(cmds.toggleMark("bold"));
    type("c");
    expect(v.content().html).toBe("<p>zab<b>c</b></p>");
    v.run(undo);
    expect(v.content().html).toBe("<p>zab</p>");
  });
});

describe("big selections", () => {
  const big = (n: number) => {
    const p = () =>
      docSchema.nodes.paragraph.create(null, [
        docSchema.text("Lorem ipsum "),
        docSchema.text("dolor", [docSchema.marks.bold.create()]),
        docSchema.text(" sit "),
        docSchema.text("amet", [docSchema.marks.link.create({ href: "u" }), docSchema.marks.italic.create()]),
        docSchema.text(" consectetur adipiscing elit"),
      ]);
    return docSchema.nodes.doc.create(null, Array.from({ length: n }, p));
  };

  test("Ctrl+A then bold on 16 000 paragraphs is fast, and typing stays fast", () => {
    let s = selectAll(createState(big(16000)));
    let t = performance.now();
    s = run(s, cmds.toggleMark("bold"));
    expect(performance.now() - t).toBeLessThan(2000);
    expect(s.selection instanceof AllSelection).toBe(true);
    let bold = 0;
    s.doc.descendants((n) => {
      if (n.isText && docSchema.marks.bold.isInSet(n.marks)) bold++;
      return true;
    });
    expect(bold).toBe(16000 * 3); // the plain runs merge with "dolor"; the link stays its own
    s = s.apply(s.tr.setSelection(TextSelection.create(s.doc, 5)));
    t = performance.now();
    for (let i = 0; i < 200; i++) s = s.apply(s.tr.insertText("x"));
    expect((performance.now() - t) / 200).toBeLessThan(1);
  }, 60000);

  test("a list made from (and taken off) many lines in one step keeps the cursor", () => {
    const lines = Array.from({ length: 100 }, (_, i) => `<p>p${i}</p>`).join("");
    let s = stateOf(lines);
    const at = (st: EditorState, text: string) => {
      let found = -1;
      st.doc.descendants((n, pos) => {
        if (found < 0 && n.isText && n.text === text) found = pos + 1;
        return found < 0;
      });
      return found;
    };
    s = s.apply(s.tr.setSelection(TextSelection.create(s.doc, at(s, "p0"), at(s, "p99") + 1)));
    s = run(s, cmds.toggleList("bullet"));
    expect(html(s)).toBe(`<ul>${Array.from({ length: 100 }, (_, i) => `<li>p${i}</li>`).join("")}</ul>`);
    expect(s.selection.from).toBe(at(s, "p0"));
    expect(s.selection.to).toBe(at(s, "p99") + 1);
    s = run(s, cmds.toggleList("bullet"));
    expect(html(s)).toBe(lines);
    expect(s.selection.from).toBe(at(s, "p0"));
    expect(html(run(run(s, undo), undo))).toBe(lines);
    const back = run(s, undo);
    expect(html(back)).toBe(`<ul>${Array.from({ length: 100 }, (_, i) => `<li>p${i}</li>`).join("")}</ul>`);
  });

  test("the fast path gives the same document as the step-by-step one", () => {
    const h =
      "<p>a<b>b</b>c</p><ul><li>x<i>y</i></li></ul><table><tr><td><p>q</p></td></tr></table><h2>T</h2>" +
      '<blockquote><p>q<b>w</b></p></blockquote><pre>c<span style="font-size:9pt">d</span></pre>';
    const many = h.repeat(40);
    const commands: [string, Command][] = [
      ["bold", cmds.toggleMark("bold")],
      ["colour", cmds.setColor("#00ff00")],
      ["font", cmds.setFont("Arial")],
      ["no highlight", cmds.setHighlight(null)],
      ["align", cmds.setAlign("center")],
      ["indent", cmds.changeIndent(2)],
      ["clear", cmds.clearFormatting],
      ["h1", cmds.setBlockStyle("h1")],
      ["normal", cmds.setBlockStyle("normal")],
      ["bullet", cmds.toggleList("bullet")],
      ["number", cmds.toggleList("number")],
    ];
    for (const [name, cmd] of commands) {
      const small = html(run(selectAll(stateOf(h)), cmd));
      const out = run(selectAll(stateOf(many)), cmd);
      expect(html(out), name).toBe(small.repeat(40));
      expect(html(run(out, undo)), name).toBe(normaliseHtml(many).html);
    }
  });
});

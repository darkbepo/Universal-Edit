// @vitest-environment jsdom
// The website document editor's formatting commands (mirroring the desktop's formatting.py and
// tests/test_gui_doceditor.py), its plugins and the DocView (load, content, paste, clipboard).
import appSource from "../src/ui/app.ts?raw";
import { type Node as PMNode } from "prosemirror-model";
import { AllSelection, EditorState, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { redo, undo } from "prosemirror-history";
import { CellSelection } from "prosemirror-tables";
import { describe, expect, test } from "vitest";
import { normaliseHtml } from "../src/doceditor/subset";
import type { OpenedDocument } from "../src/doceditor/types";
import {
  activeState,
  changeIndent,
  clearFormatting,
  insertImage,
  insertLink,
  insertPageBreak,
  insertRule,
  insertTable,
  setAlign,
  setBlockStyle,
  setColor,
  setFont,
  setHighlight,
  setSize,
  stepOverEmpty,
  tableOp,
  toggleList,
  toggleMark,
} from "../src/ui/doceditor/commands";
import { docToHtml, htmlToDoc } from "../src/ui/doceditor/convert";
import { docSchema } from "../src/ui/doceditor/schema";
import { DocView, createState, plainText } from "../src/ui/doceditor/view";

const NBSP = String.fromCharCode(0xa0);

// jsdom has no ClipboardEvent (EditorView.pasteHTML makes one)
if (typeof globalThis.ClipboardEvent === "undefined") {
  (globalThis as Record<string, unknown>).ClipboardEvent = class extends Event {
    clipboardData = null;
  };
}

const stateOf = (html: string) => createState(htmlToDoc(html));
const html = (s: EditorState) => docToHtml(s.doc);

/** Run `cmd`; returns the new state (the same one when the command doesn't apply). */
function run(s: EditorState, cmd: Command): EditorState {
  let next = s;
  cmd(s, (tr: Transaction) => {
    next = s.apply(tr);
  });
  return next;
}

/** Position of the `n`th occurrence of `text` (+ `offset` characters into it). */
function find(s: EditorState, text: string, offset = 0, n = 0): number {
  let found = -1;
  let seen = 0;
  s.doc.descendants((node, pos) => {
    if (found >= 0 || !node.isText) return found < 0;
    let i = (node.text as string).indexOf(text);
    while (i >= 0) {
      if (seen++ === n) {
        found = pos + i + offset;
        return false;
      }
      i = (node.text as string).indexOf(text, i + 1);
    }
    return true;
  });
  if (found < 0) throw new Error(`no ${text}`);
  return found;
}
const select = (s: EditorState, from: number, to = from) =>
  s.apply(s.tr.setSelection(TextSelection.create(s.doc, from, to)));
const selectText = (s: EditorState, text: string, n = 0) => {
  const at = find(s, text, 0, n);
  return select(s, at, at + text.length);
};
const cursor = (s: EditorState, text: string, offset = 0) => select(s, find(s, text, offset));
const selectAll = (s: EditorState) => s.apply(s.tr.setSelection(new AllSelection(s.doc)));

describe("block styles", () => {
  test("bold then h1, then back to normal", () => {
    let s = selectText(stateOf("<p>hello world</p>"), "hello");
    s = run(s, toggleMark("bold"));
    expect(html(s)).toBe("<p><b>hello</b> world</p>");
    s = run(s, setBlockStyle("h1"));
    expect(html(s)).toBe("<h1>hello world</h1>");
    s = run(s, setBlockStyle("normal"));
    expect(html(s)).toBe("<p>hello world</p>");
  });

  test("each style, and activeState reports it", () => {
    let s = cursor(stateOf("<p>hello world</p>"), "hello", 2);
    for (const [style, out] of [
      ["h2", "<h2>hello world</h2>"],
      ["quote", "<blockquote><p>hello world</p></blockquote>"],
      ["code", "<pre>hello world</pre>"],
      ["normal", "<p>hello world</p>"],
    ] as const) {
      s = run(s, setBlockStyle(style));
      expect(html(s)).toBe(out);
      expect(activeState(s).style).toBe(style);
    }
  });

  test("back to normal removes size and bold inside, as the desktop does", () => {
    let s = cursor(stateOf('<h1><b>a</b><span style="font-size:20pt">b</span><i>c</i></h1>'), "a");
    s = run(s, setBlockStyle("normal"));
    expect(html(s)).toBe("<p>ab<i>c</i></p>");
    s = cursor(stateOf('<pre><b>a</b><span style="font-family:Arial;font-size:9pt">b</span></pre>'), "a");
    expect(html(run(s, setBlockStyle("normal")))).toBe("<p>ab</p>");
    // a quote keeps them
    s = cursor(stateOf("<blockquote><p><b>a</b></p></blockquote>"), "a");
    expect(html(run(s, setBlockStyle("normal")))).toBe("<p><b>a</b></p>");
  });

  test("several blocks; alignment and indent stay", () => {
    let s = selectAll(stateOf('<p style="text-align:center">a</p><h2>b</h2><p>c</p>'));
    s = run(s, setBlockStyle("h3"));
    expect(html(s)).toBe('<h3 style="text-align:center">a</h3><h3>b</h3><h3>c</h3>');
    s = run(selectAll(s), setBlockStyle("quote"));
    expect(html(s)).toBe('<blockquote><p style="text-align:center">a</p><p>b</p><p>c</p></blockquote>');
  });

  test("leaving a quote splits it around the block", () => {
    let s = cursor(stateOf("<blockquote><p>a</p><p>b</p><p>c</p></blockquote>"), "b");
    s = run(s, setBlockStyle("normal"));
    expect(html(s)).toBe("<blockquote><p>a</p></blockquote><p>b</p><blockquote><p>c</p></blockquote>");
    expect(activeState(s).style).toBe("normal");
  });

  test("code lines join the code block right above; one below stays separate", () => {
    let s = cursor(stateOf("<pre>a\n</pre><p>b</p><p>c</p><pre>d\n</pre>"), "b");
    s = run(s, setBlockStyle("code"));
    s = run(cursor(s, "c"), setBlockStyle("code"));
    expect(html(s)).toBe("<pre>a\nb\nc\n</pre><pre>d\n</pre>");
    s = cursor(stateOf("<p>b</p><pre>d\n</pre>"), "b");
    expect(html(run(s, setBlockStyle("code")))).toBe("<pre>b</pre><pre>d\n</pre>");
    s = selectAll(stateOf("<pre>a</pre><p>b</p><p>c</p>"));
    expect(html(run(s, setBlockStyle("code")))).toBe("<pre>a\nb\nc</pre>");
  });

  test("a code line made normal leaves the code block around it", () => {
    let s = cursor(stateOf("<pre>a\nb\nc\n</pre>"), "b");
    s = run(s, setBlockStyle("normal"));
    expect(html(s)).toBe("<pre>a</pre><p>b</p><pre>c\n</pre>");
    expect(s.selection.$from.parent.textContent).toBe("b");
    s = selectAll(stateOf("<pre>x\ny\n</pre>"));
    expect(html(run(s, setBlockStyle("h1")))).toBe("<h1>x</h1><h1>y</h1>");
  });

  test("list lines keep their list", () => {
    const s = selectAll(stateOf("<ul><li>a</li></ul><p>b</p>"));
    expect(html(run(s, setBlockStyle("h1")))).toBe("<ul><li>a</li></ul><h1>b</h1>");
  });
});

describe("character formatting", () => {
  test("toggles, font, size, colour, highlight", () => {
    let s = selectText(stateOf("<p>hello world</p>"), "hello");
    for (const m of ["bold", "italic", "underline", "strike"] as const) s = run(s, toggleMark(m));
    expect(activeState(s).marks.has("bold") && activeState(s).marks.has("strike")).toBe(true);
    expect(html(s)).toBe("<p><b><i><u><s>hello</s></u></i></b> world</p>");
    s = run(s, toggleMark("bold"));
    expect(activeState(s).marks.has("bold")).toBe(false);
    s = run(s, setFont("Courier New"));
    s = run(s, setSize(14));
    s = run(s, setColor("#ff0000"));
    s = run(s, setHighlight("#ffff00"));
    const out = html(s);
    expect(out).toContain("font-family:Courier New");
    expect(out).toContain("font-size:14pt");
    expect(out).toContain("color:#ff0000");
    expect(out).toContain("background-color:#ffff00");
    expect(activeState(s).font).toBe("Courier New");
    expect(activeState(s).size).toBe(14);
    s = run(s, setHighlight(null));
    expect(html(s)).not.toContain("background-color");
  });

  test("with a cursor the marks apply to what is typed next", () => {
    let s = cursor(stateOf("<p>ab</p>"), "a", 1);
    s = run(s, toggleMark("bold"));
    s = run(s, setColor("#00ff00"));
    expect(activeState(s).marks.has("bold")).toBe(true);
    s = s.apply(s.tr.insertText("X"));
    expect(html(s)).toBe('<p>a<span style="color:#00ff00"><b>X</b></span>b</p>');
  });

  test("a partly bold selection becomes bold", () => {
    let s = selectText(stateOf("<p><b>he</b>llo</p>"), "he");
    s = select(s, s.selection.from, s.selection.from + 5);
    expect(html(run(s, toggleMark("bold")))).toBe("<p><b>hello</b></p>");
  });

  test("alignment", () => {
    let s = cursor(stateOf("<p>hello world</p>"), "hello");
    s = run(s, setAlign("center"));
    expect(html(s)).toBe('<p style="text-align:center">hello world</p>');
    expect(activeState(s).align).toBe("center");
    s = run(s, setAlign("left"));
    expect(html(s)).toBe("<p>hello world</p>");
    s = run(s, setAlign("justify"));
    expect(html(s)).toContain("text-align:justify");
  });
});

describe("lists and indent", () => {
  test("lists and indent nest validly", () => {
    let s = selectAll(stateOf("<p>a</p><p>b</p><p>c</p>"));
    s = run(s, toggleList("bullet"));
    expect(html(s)).toBe("<ul><li>a</li><li>b</li><li>c</li></ul>");
    expect(activeState(s).list).toBe("bullet");
    s = run(cursor(s, "b"), changeIndent(1));
    expect(html(s)).toBe("<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>");
    s = run(s, changeIndent(-1));
    expect(html(s)).toBe("<ul><li>a</li><li>b</li><li>c</li></ul>");
    s = run(selectAll(s), toggleList("number"));
    expect(html(s)).toBe("<ol><li>a</li><li>b</li><li>c</li></ol>");
    s = run(selectAll(s), toggleList("number"));
    expect(html(s)).toBe("<p>a</p><p>b</p><p>c</p>");
  });

  test("a list can't nest deeper than one level below the item above", () => {
    let s = run(selectAll(stateOf("<p>a</p><p>b</p>")), toggleList("bullet"));
    s = run(cursor(s, "a"), changeIndent(1)); // nothing above it to nest under
    expect(html(s)).toBe("<ul><li>a</li><li>b</li></ul>");
    s = run(cursor(s, "b"), changeIndent(1));
    s = run(s, changeIndent(1));
    expect(html(s)).toBe("<ul><li>a<ul><li>b</li></ul></li></ul>");
    s = cursor(stateOf("<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"), "b");
    s = run(s, changeIndent(-1));
    expect(html(s)).toBe("<ul><li>a</li><li>b<ul><li>c</li></ul></li></ul>");
    // the top level stays in the list
    s = run(cursor(s, "a"), changeIndent(-1));
    expect(html(s)).toBe("<ul><li>a</li><li>b<ul><li>c</li></ul></li></ul>");
  });

  test("lists nest at most 8 levels", () => {
    let s = stateOf(`<ul>${Array.from({ length: 12 }, (_, i) => `<li>x${i}</li>`).join("")}</ul>`);
    for (let i = 1; i < 12; i++) s = run(cursor(s, `x${i}`), changeIndent(i));
    let depth = 0;
    s.doc.descendants((node, pos) => {
      if (node.type === docSchema.nodes.list_item) depth = Math.max(depth, (s.doc.resolve(pos).depth + 1) / 2);
      return true;
    });
    expect(depth).toBe(8);
  });

  test("block indent outside lists", () => {
    let s = cursor(stateOf("<p>hello world</p>"), "hello");
    s = run(s, changeIndent(2));
    expect(html(s)).toBe('<p style="margin-left:72pt">hello world</p>');
    s = run(s, changeIndent(-5));
    expect(html(s)).toBe("<p>hello world</p>");
    s = run(s, changeIndent(20));
    expect(html(s)).toContain("margin-left:288pt");
  });

  test("several lines at once: outdent, and a list switched off from nested items", () => {
    const nested = "<ul><li>a<ul><li>b</li><li>c</li></ul></li></ul>";
    let s = stateOf(nested);
    s = select(s, find(s, "b"), find(s, "c") + 1);
    expect(html(run(s, changeIndent(-1)))).toBe("<ul><li>a</li><li>b</li><li>c</li></ul>");
    s = stateOf(nested);
    s = run(select(s, find(s, "b"), find(s, "c") + 1), toggleList("bullet"));
    expect(html(s)).toBe("<ul><li>a</li></ul><p>b</p><p>c</p>");
    s = run(selectAll(stateOf(nested)), toggleList("bullet"));
    expect(html(s)).toBe("<p>a</p><p>b</p><p>c</p>");
  });

  test("a list toggled on a mixed selection", () => {
    let s = selectAll(stateOf("<ul><li>a</li></ul><h2>b</h2><p>c</p>"));
    s = run(s, toggleList("number"));
    expect(html(s)).toBe("<ol><li>a</li></ol><ol><li>b</li><li>c</li></ol>");
  });
});

describe("inserts", () => {
  test("links", () => {
    let s = selectText(stateOf("<p>hello world</p>"), "hello");
    s = run(s, insertLink("https://x.org/"));
    expect(html(s)).toBe('<p><a href="https://x.org/">hello</a> world</p>');
    s = select(s, s.doc.content.size - 1);
    s = run(s, insertLink("https://y.org/", "why"));
    expect(html(s).endsWith('<a href="https://y.org/">why</a></p>')).toBe(true);
    s = s.apply(s.tr.insertText("!")); // a link doesn't grow when typing after it
    expect(html(s).endsWith("</a>!</p>")).toBe(true);
    s = run(cursor(stateOf("<p>ab</p>"), "a", 1), insertLink("https://z.org/"));
    expect(html(s)).toBe('<p>a<a href="https://z.org/">https://z.org/</a>b</p>');
  });

  test("pictures", () => {
    let s = cursor(stateOf("<p><b>ab</b></p>"), "a", 1);
    s = run(s, insertImage("img/1.png", 20, 10));
    expect(html(s)).toBe('<p><b>a</b><img src="img/1.png" width="20" height="10"><b>b</b></p>');
  });

  test("table ops", () => {
    let s = cursor(stateOf("<p>hello world</p>"), "hello");
    s = run(s, insertTable(2, 2));
    expect(html(s)).toBe(
      "<table><tr><td><p></p></td><td><p></p></td></tr><tr><td><p></p></td><td><p></p></td></tr></table><p>hello world</p>",
    );
    expect(activeState(s).inTable).toBe(true);
    s = run(s, tableOp("row_below"));
    expect(html(s).split("<tr>").length - 1).toBe(3);
    s = run(s, tableOp("col_right"));
    expect(html(s).split("<td").length - 1).toBe(9);
    s = run(s, tableOp("row_above"));
    s = run(s, tableOp("col_left"));
    expect(html(s).split("<tr>").length - 1).toBe(4);
    s = run(s, tableOp("del_row"));
    s = run(s, tableOp("del_col"));
    expect(html(s).split("<tr>").length - 1).toBe(3);
    expect(html(s).split("<td").length - 1).toBe(9);
    s = run(s, tableOp("del_table"));
    expect(html(s)).toBe("<p>hello world</p>");
  });

  test("deleting the last row or column deletes the table", () => {
    let s = cursor(stateOf("<p>x</p><table><tr><td>a</td><td>b</td></tr></table>"), "a");
    expect(html(run(s, tableOp("del_row")))).toBe("<p>x</p>");
    s = cursor(stateOf("<p>x</p><table><tr><td>a</td></tr><tr><td>b</td></tr></table>"), "a");
    expect(html(run(s, tableOp("del_col")))).toBe("<p>x</p>");
  });

  test("merge and split", () => {
    let s = stateOf("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>");
    const cell = (text: string) => s.doc.resolve(find(s, text)).before(-1 + s.doc.resolve(find(s, text)).depth);
    s = s.apply(s.tr.setSelection(CellSelection.create(s.doc, cell("a"), cell("b"))));
    s = run(s, tableOp("merge"));
    expect(html(s)).toContain('colspan="2"');
    s = run(cursor(s, "a"), tableOp("split"));
    expect(html(s)).not.toContain("colspan");
    expect(html(s).split("<td").length - 1).toBe(4);
  });

  test("table ops outside a table do nothing", () => {
    const s = cursor(stateOf("<p>hello world</p>"), "hello");
    expect(tableOp("row_below")(s)).toBe(false);
    expect(html(run(s, tableOp("row_below")))).toBe("<p>hello world</p>");
  });

  test("a table can't go into a list item: it goes after the list", () => {
    const s = cursor(stateOf("<ul><li>ab</li></ul>"), "a", 1);
    expect(html(run(s, insertTable(1, 1)))).toBe("<ul><li>ab</li></ul><table><tr><td><p></p></td></tr></table>");
  });

  test("page break and rule", () => {
    let s = select(stateOf("<p>hello world</p>"), find(stateOf("<p>hello world</p>"), "hello", 5));
    s = run(s, insertPageBreak);
    // the space after the break starts the new paragraph and is kept (typed-space rule)
    expect(html(s)).toBe(`<p>hello</p><p style="page-break-before:always">${NBSP}world</p>`);
    s = select(s, s.doc.content.size - 1);
    s = run(s, insertRule);
    expect(html(s)).toBe(`<p>hello</p><p style="page-break-before:always">${NBSP}world</p><hr><p></p>`);
    expect(s.selection.$from.parent.type).toBe(docSchema.nodes.paragraph); // an empty line after it
    expect(s.doc.lastChild?.type).toBe(docSchema.nodes.paragraph);
    s = run(cursor(stateOf("<p>a</p><p></p><p>b</p>"), "a"), (st, d) => {
      d?.(st.tr.setSelection(TextSelection.create(st.doc, 4)));
      return true;
    });
    expect(html(run(s, insertRule))).toBe("<p>a</p><hr><p>b</p>"); // an empty line becomes the rule
    s = cursor(stateOf("<ul><li>a</li></ul>"), "a");
    expect(html(run(s, insertPageBreak))).toBe('<ul><li></li><li style="page-break-before:always">a</li></ul>');
  });

  test("clearFormatting keeps the list", () => {
    let s = selectAll(stateOf('<ul><li><b>bold</b> <a href="https://x.org/">link</a></li></ul><h1>T</h1>'));
    s = run(s, clearFormatting);
    expect(html(s)).toBe("<ul><li>bold link</li></ul><p>T</p>");
  });

  test("clearFormatting keeps pictures and tables; with a cursor it clears the line", () => {
    let s = selectAll(
      stateOf('<table><tr><td><p><b>x</b><img src="img/1.png" width="2" height="2"></p></td></tr></table>'),
    );
    const out = html(run(s, clearFormatting));
    expect(out).toContain("<table>");
    expect(out).toContain("<img");
    expect(out).not.toContain("<b>");
    s = cursor(stateOf('<p><b>a</b><a href="u"></a>b</p><p><i>c</i></p>'), "b");
    expect(html(run(s, clearFormatting))).toBe("<p>ab</p><p><i>c</i></p>");
  });
});

describe("undo", () => {
  test("one undo step per command", () => {
    const base = "<p>hello world</p><p>second</p>";
    const commands: [string, Command][] = [
      ["h1", setBlockStyle("h1")],
      ["quote", setBlockStyle("quote")],
      ["code", setBlockStyle("code")],
      ["bold", toggleMark("bold")],
      ["font", setFont("Arial")],
      ["size", setSize(20)],
      ["colour", setColor("#ff0000")],
      ["highlight", setHighlight("#00ff00")],
      ["align", setAlign("right")],
      ["bullet", toggleList("bullet")],
      ["indent", changeIndent(1)],
      ["link", insertLink("u")],
      ["image", insertImage("img/1.png")],
      ["table", insertTable(2, 3)],
      ["break", insertPageBreak],
      ["rule", insertRule],
      ["clear", clearFormatting],
    ];
    for (const [name, cmd] of commands) {
      let s = selectText(stateOf(base), "hello");
      s = run(s, cmd);
      s = run(s, cmd === clearFormatting ? setBlockStyle("h1") : toggleMark("italic")); // a second command
      expect(html(s), name).not.toBe(base);
      s = run(s, undo);
      s = run(s, undo);
      expect(html(s), name).toBe(base);
      s = run(run(s, redo), redo);
      expect(html(s), name).not.toBe(base);
    }
  });

  test("a table op is one step", () => {
    let s = cursor(stateOf("<table><tr><td>a</td></tr></table>"), "a");
    s = run(s, tableOp("col_right"));
    s = run(s, tableOp("row_below"));
    s = run(s, undo);
    expect(html(s)).toBe("<table><tr><td><p>a</p></td><td><p></p></td></tr></table>");
  });
});

describe("activeState follows the cursor", () => {
  const s = stateOf(
    '<h2>T</h2><p style="text-align:center"><b>bo</b>ld <span style="font-family:Arial;font-size:9pt">f</span></p>' +
      "<ol><li>n</li></ol><blockquote><p>q</p></blockquote><table><tr><td>c</td></tr></table>",
  );
  test.each([
    ["T", { style: "h2", align: "left", inTable: false, list: null }],
    ["bo", { style: "normal", align: "center", bold: true }],
    ["ld", { bold: false }],
    ["f", { font: "Arial", size: 9 }],
    ["n", { list: "number" }],
    ["q", { style: "quote" }],
    ["c", { inTable: true }],
  ])("%s", (text, want) => {
    const a = activeState(cursor(s, text, 1));
    const got: Record<string, unknown> = { ...a, bold: a.marks.has("bold") };
    for (const [k, v] of Object.entries(want)) expect(got[k], k).toBe(v);
  });
});

describe("plugins", () => {
  test("stale nest records are dropped when marks are removed", () => {
    let s = stateOf('<p><b><a href="u">x</a></b>y</p>');
    const nestOf = (st: EditorState) => st.doc.firstChild?.firstChild?.marks.find((m) => m.type.name === "nest");
    expect(nestOf(s)?.attrs.order).toBe("b a");
    s = run(selectText(s, "x"), toggleMark("bold"));
    expect(nestOf(s)).toBeUndefined();
    s = stateOf('<p><span style="color:red"><b><a href="u">x</a></b></span></p>');
    expect(nestOf(s)?.attrs.order).toBe("span:c b a");
    s = run(selectText(s, "x"), toggleMark("bold"));
    expect(nestOf(s)?.attrs.order).toBe("span:c a");
    s = run(s, undo);
    expect(nestOf(s)?.attrs.order).toBe("span:c b a"); // one step with the command
  });

  test("typing right after an empty tag doesn't take its formatting", () => {
    let s = stateOf("<p>a<b></b></p>");
    s = select(s, s.doc.firstChild!.nodeSize - 1);
    s = s.apply(s.tr.insertText("z"));
    expect(html(s)).toBe("<p>a<b></b>z</p>");
    s = stateOf('<p><i>a<a href="u"></a></i></p>');
    s = select(s, s.doc.firstChild!.nodeSize - 1);
    s = s.apply(s.tr.insertText("z"));
    expect(html(s)).toBe('<p><i>a<a href="u"></a>z</i></p>');
  });

  test("one arrow press steps over an empty tag", () => {
    let s = stateOf('<p>ab<a href="u"></a><b></b>cd</p>');
    s = select(s, 3); // between b and the empty tags
    s = run(s, stepOverEmpty(1));
    expect(s.selection.from).toBe(6); // past both empty tags and "c"
    s = run(s, stepOverEmpty(-1));
    expect(s.selection.from).toBe(6); // nothing empty before: the browser moves
    s = run(select(s, 5), stepOverEmpty(-1));
    expect(s.selection.from).toBe(2); // back past both and "b"
    s = stateOf("<p>a<b></b></p><p>x</p>");
    s = run(select(s, 2), stepOverEmpty(1));
    expect(s.selection.$from.parent.textContent).toBe("x");
  });

  test("loading and typing never pad short table rows; a table op does", () => {
    const ragged = "<table><tr><td><p>a</p></td></tr><tr><td><p>b</p></td><td><p>c</p></td></tr></table>";
    let s = cursor(stateOf(ragged), "a", 1);
    s = s.apply(s.tr.insertText("z"));
    expect(html(s)).toBe("<table><tr><td><p>az</p></td></tr><tr><td><p>b</p></td><td><p>c</p></td></tr></table>");
    s = run(s, tableOp("row_below"));
    expect(html(s).split("<td").length - 1).toBe(6);
  });
});

// ----- the view -------------------------------------------------------------------------------

const PNG_1PX = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  ),
  (c) => c.charCodeAt(0),
);
const opened = (h: string, images = new Map<string, Uint8Array>()): OpenedDocument => ({
  html: h,
  images,
  sourceFormat: "docx",
  warnings: [],
  frontMatter: "---\na: 1\n---\n",
  title: "T",
  template: null,
});

function newView(): DocView {
  return new DocView(document.createElement("div"));
}

describe("DocView", () => {
  test("load → content is the identity over the shared cases", async () => {
    const vectors = (await import("../../tests/fixtures/doceditor-vectors.json")).default as unknown as {
      cases: string[];
      normalise: { out: string }[];
      markdown: { html: string }[];
    };
    const v = newView();
    for (const h of [...vectors.cases, ...vectors.normalise.map((n) => n.out), ...vectors.markdown.map((m) => m.html)]) {
      v.load(opened(h));
      expect(v.content().html).toBe(normaliseHtml(h).html);
      expect(v.dirty).toBe(false);
    }
  });

  test("load resets history and the dirty flag; content keeps only referenced pictures", () => {
    const v = newView();
    let changes = 0;
    v.onChange = () => changes++;
    v.load(opened('<p>a<img src="img/1.png"></p>', new Map([["img/1.png", PNG_1PX], ["img/2.png", PNG_1PX]])));
    expect(v.dirty).toBe(false);
    v.run(selectAllCmd);
    expect(v.dirty).toBe(false); // a selection isn't a change
    v.run(toggleMark("bold"));
    v.view.dispatch(v.view.state.tr.insertText("x", 1, 2));
    expect(v.dirty).toBe(true);
    expect(changes).toBeGreaterThan(0);
    const c = v.content();
    expect([...c.images.keys()]).toEqual(["img/1.png"]);
    expect(c.frontMatter).toBe("---\na: 1\n---\n");
    expect(c.title).toBe("T");
    v.load(opened("<p>b</p>"));
    expect(v.dirty).toBe(false);
    expect(undo(v.view.state)).toBe(false);
  });

  test("undoing back to the loaded document is not dirty", () => {
    const v = newView();
    v.load(opened("<p>ab</p>"));
    v.view.dispatch(v.view.state.tr.insertText("x", 2));
    expect(v.dirty).toBe(true);
    undo(v.view.state, (tr) => v.view.dispatch(tr));
    expect(v.dirty).toBe(false);
  });

  test("an emptied document saves nothing", () => {
    const v = newView();
    v.load(opened("<p>abc</p>"));
    v.view.dispatch(v.view.state.tr.delete(1, 4));
    expect(v.content().html).toBe("");
    expect(v.content().plainText).toBe("");
  });

  test("pasting HTML with a data: picture into a document that has img/1.png", () => {
    const v = newView();
    v.load(opened('<p>a<img src="img/1.png"></p>', new Map([["img/1.png", PNG_1PX]])));
    v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, 2)));
    const other = Uint8Array.from([...PNG_1PX, 0]);
    let b64 = "";
    for (const byte of other) b64 += String.fromCharCode(byte);
    v.view.pasteHTML(`<p>x<img src="data:image/png;base64,${btoa(b64)}"><img src="https://e.com/p.png"></p>`);
    const c = v.content();
    expect(c.html).toBe('<p>ax<img src="img/2.png"><img src="img/1.png"></p>');
    expect(c.images.get("img/1.png")).toEqual(PNG_1PX);
    expect(c.images.get("img/2.png")).toEqual(other);
  });

  test("pasted HTML is normalised (no script links) and keeps empty links and nesting", () => {
    const v = newView();
    v.load(opened("<p>ab</p>"));
    v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, 2)));
    v.view.pasteHTML('<a href="javascript:alert(1)">j</a><b><a href="u">x</a></b><a href="v"></a>');
    expect(v.content().html).toBe('<p>aj<b><a href="u">x</a></b><a href="v"></a>b</p>');
  });

  test("a pasted picture file is added like insertImage", async () => {
    const v = newView();
    v.load(opened('<p>a<img src="img/1.png"></p>', new Map([["img/1.png", PNG_1PX]])));
    v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, 2)));
    await v.addPicture(PNG_1PX, "png");
    expect(v.content().html).toBe('<p>a<img src="img/2.png"><img src="img/1.png"></p>');
    expect(v.content().images.size).toBe(2);
  });

  test("the clipboard has clean subset HTML, which pastes back unchanged", () => {
    const v = newView();
    const h = '<p><b><a href="u">x</a></b> <a href="v"></a> <span style="color:#ff0000"><i>y</i></span></p>';
    v.load(opened(h));
    const doc = v.view.state.doc;
    const slice = doc.slice(1, doc.content.size - 1);
    const dom = v.clipboardHtml(slice);
    expect(dom).not.toContain("data-umc");
    expect(dom).toContain('<b><a href="u">x</a></b>');
    v.load(opened("<p></p>"));
    v.view.dispatch(v.view.state.tr.setSelection(TextSelection.create(v.view.state.doc, 1)));
    v.view.pasteHTML(dom);
    expect(v.content().html).toBe(h);
  });
});

const selectAllCmd: Command = (s, d) => {
  d?.(s.tr.setSelection(new AllSelection(s.doc)));
  return true;
};

describe("plain text", () => {
  // what the desktop's QTextDocument.toPlainText gives (checked with the desktop view)
  test.each([
    [`<p>a${NBSP}${NBSP}b</p><p>${NBSP}${NBSP}x\tq</p>`, "a  b\n  x\tq\n"],
    [
      "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td><p>d</p><p>e</p></td></tr></table><p>z</p>",
      "\na\nb\nc\nd\ne\n\nz\n",
    ],
    ["<ul><li>a<ul><li>b</li></ul></li></ul><hr><p>x<br>y</p>", "a\nb\n\nx\ny\n"],
    ["<pre>a\nb\n</pre><pre>c</pre>", "a\nb\nc\n"],
    ['<p>a<img src="img/1.png">b<a href="u"></a>c</p>', "abc\n"],
    ["<blockquote><p>q</p></blockquote><h1>H</h1>", "q\nH\n"],
    ["<p></p>", ""],
    ["<p>x</p><hr>", "x\n\n"],
    ["<hr><p>x</p>", "\nx\n"],
    ["<p>a</p><table><tr><td>b</td></tr></table>", "a\nb\n\n"],
  ])("%j", (h, text) => {
    expect(plainText(htmlToDoc(h))).toBe(text);
  });
});

test("the page bundle doesn't import the editor's view or commands", () => {
  expect(appSource).not.toMatch(/doceditor\/(view|commands)/);
});

export type { PMNode };

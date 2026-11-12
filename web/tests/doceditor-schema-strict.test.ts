// @vitest-environment jsdom
// The schema only holds what the subset can write (list items, table cells), nested spans keep
// their outer values, and random edits always export a fixed point.
import { DOMParser as PMParser, Fragment, type Node as PMNode } from "prosemirror-model";
import { EditorState, TextSelection, type Command, type Selection, type Transaction } from "prosemirror-state";
import {
  deleteSelection,
  joinBackward,
  joinForward,
  lift,
  setBlockType,
  splitBlock,
  toggleMark,
  wrapIn,
} from "prosemirror-commands";
import { liftListItem, sinkListItem, splitListItem, wrapInList } from "prosemirror-schema-list";
import { describe, expect, test } from "vitest";
import { normaliseHtml } from "../src/doceditor/subset";
import { docToHtml, htmlToDoc } from "../src/ui/doceditor/convert";
import { docSchema } from "../src/ui/doceditor/schema";
import * as cmds from "../src/ui/doceditor/commands";
import { createState } from "../src/ui/doceditor/view";
import { Rand, randomHtml } from "./doceditor-gen";

const nodes = docSchema.nodes;
const marks = docSchema.marks;
const NBSP = String.fromCharCode(0xa0);

function expectIdentity(html: string): void {
  expect(normaliseHtml(html).html).toBe(html); // a canonical input
  let out = html;
  for (let n = 0; n < 3; n++) {
    out = docToHtml(htmlToDoc(out));
    expect(out).toBe(html);
  }
}

describe("nested spans setting the same property", () => {
  test.each([
    '<p><span style="color:#000000"><span style="color:#ff0000">x</span>y</span></p>',
    '<p><span style="color:#000000">a<span style="color:#ff0000">x</span>y</span></p>',
    '<p><span style="font-size:10pt">a<span style="font-size:20pt">b</span></span></p>',
    '<p><span style="color:#000000"><span style="color:#ff0000"></span>y</span></p>',
    '<p><span style="font-family:Arial;color:#000000"><b><span style="font-size:9pt;color:#ff0000">x</span></b>y</span></p>',
    '<p><span style="font-family:Times New Roman"><span style="font-family:Arial">x</span> t</span></p>',
    '<p><span style="background-color:#ffff00"><span style="background-color:#00ff00"><span style="background-color:#0000ff">x</span></span></span></p>',
    '<p><span style="color:#ff0000"><span style="color:#ff0000">same</span></span></p>',
    '<p><a href="u"><span style="color:#000000"><span style="color:#ff0000">l</span></span></a></p>',
  ])("%s", (html) => expectIdentity(html));

  test("changing the inner colour keeps the outer one", () => {
    const html = '<p><span style="color:#000000"><span style="color:#ff0000">x</span>y</span></p>';
    let state = EditorState.create({ doc: htmlToDoc(html) });
    state = state.apply(state.tr.addMark(1, 2, marks.color.create({ rgb: "#0000ff" })));
    expect(docToHtml(state.doc)).toBe(
      '<p><span style="color:#000000"><span style="color:#0000ff">x</span>y</span></p>',
    );
  });

  test("removing the colour drops both spans around that text", () => {
    const html = '<p><span style="color:#000000"><span style="color:#ff0000">x</span>y</span></p>';
    let state = EditorState.create({ doc: htmlToDoc(html) });
    state = state.apply(state.tr.removeMark(1, 2, marks.color));
    expect(docToHtml(state.doc)).toBe('<p>x<span style="color:#000000">y</span></p>');
  });
});

describe("list items hold their line and nested lists only", () => {
  const blocks: [string, () => PMNode][] = [
    ["table", () => htmlToDoc("<table><tr><td><p>t</p></td></tr></table>").child(0)],
    ["heading", () => nodes.heading.create({ level: 2 }, docSchema.text("h"))],
    ["quote", () => nodes.blockquote.create(null, nodes.paragraph.create(null, docSchema.text("q")))],
    ["code", () => nodes.code_block.create(null, docSchema.text("c"))],
    ["rule", () => nodes.horizontal_rule.create()],
  ];
  test.each(blocks)("a %s inserted into an item lands outside it", (_name, make) => {
    const state = EditorState.create({ doc: htmlToDoc("<ul><li>ab</li><li>c</li></ul>") });
    const tr = state.tr.setSelection(TextSelection.create(state.doc, 4)).replaceSelectionWith(make());
    tr.doc.check();
    tr.doc.descendants((node) => {
      if (node.type === nodes.list_item) {
        node.forEach((child, _o, i) => {
          if (i > 0) expect([nodes.bullet_list, nodes.ordered_list]).toContain(child.type);
        });
      }
    });
    const html = docToHtml(tr.doc);
    expect(html).not.toContain("<br>");
    expect(docToHtml(htmlToDoc(html))).toBe(html);
  });

  test("the content expression", () => {
    const item = nodes.list_item;
    const para = nodes.paragraph.create();
    expect(item.validContent(Fragment.from(para))).toBe(true);
    expect(item.validContent(Fragment.from([para, nodes.bullet_list.create(null, item.create(null, para))]))).toBe(true);
    expect(item.validContent(Fragment.from([para, para]))).toBe(false);
    expect(item.validContent(Fragment.from([para, nodes.horizontal_rule.create()]))).toBe(false);
  });
});

describe("tables", () => {
  test("no header cells: a pasted <th> becomes a cell", () => {
    expect(docSchema.nodes.table_header).toBeUndefined();
    const body = new DOMParser().parseFromString(
      '<table><tr><th colspan="2">h</th></tr><tr><td>a</td><td>b</td></tr></table>',
      "text/html",
    ).body;
    const doc = PMParser.fromSchema(docSchema).parse(body);
    const cell = doc.child(0).child(0).child(0);
    expect(cell.type).toBe(nodes.table_cell);
    expect(cell.attrs.colspan).toBe(2);
    expect(docToHtml(doc)).toBe(
      '<table><tr><td colspan="2"><p>h</p></td></tr><tr><td><p>a</p></td><td><p>b</p></td></tr></table>',
    );
  });

  test("a row can't be empty", () => {
    expect(nodes.table_row.validContent(Fragment.empty)).toBe(false);
    expect(nodes.table_row.createAndFill()?.childCount).toBe(1);
  });
});

// ----- random edits: every export is a fixed point -------------------------------------------

function randomEdit(state: EditorState, r: Rand): EditorState {
  const size = state.doc.content.size;
  const a = r.int(size + 1);
  const b = r.int(size + 1);
  let sel: Selection;
  try {
    sel = TextSelection.between(state.doc.resolve(Math.min(a, b)), state.doc.resolve(Math.max(a, b)));
  } catch {
    return state;
  }
  state = state.apply(state.tr.setSelection(sel));
  const insert = (node: () => PMNode): Command => (s, d) => {
    d?.(s.tr.replaceSelectionWith(node()));
    return true;
  };
  const commands: Command[] = [
    toggleMark(marks.bold),
    toggleMark(marks.italic),
    toggleMark(marks.underline),
    toggleMark(marks.link, { href: r.pick(["u", "w"]) }),
    toggleMark(marks.color, { rgb: "#123456" }),
    toggleMark(marks.size, { pt: 18 }),
    (s, d) => {
      d?.(s.tr.insertText(r.pick(["x", " ", "  y ", NBSP, "\t", " " + NBSP + " "])));
      return true;
    },
    insert(() => nodes.hard_break.create()),
    insert(() => nodes.image.create({ src: "img/1.png" })),
    splitBlock,
    joinBackward,
    joinForward,
    deleteSelection,
    setBlockType(nodes.heading, { level: 2 }),
    setBlockType(nodes.code_block),
    setBlockType(nodes.paragraph),
    wrapInList(nodes.bullet_list),
    splitListItem(nodes.list_item),
    liftListItem(nodes.list_item),
    sinkListItem(nodes.list_item),
    wrapIn(nodes.blockquote),
    lift,
    // the editor's own commands (commands.ts)
    cmds.setBlockStyle(r.pick(["normal", "h1", "h3", "quote", "code"] as const)),
    cmds.toggleMark(r.pick(["bold", "italic", "underline", "strike"] as const)),
    cmds.setFont("Arial"),
    cmds.setSize(r.pick([9, 14])),
    cmds.setColor("#ff0000"),
    cmds.setHighlight(r.pick(["#00ff00", null])),
    cmds.setAlign(r.pick(["left", "center", "right", "justify"] as const)),
    cmds.toggleList(r.pick(["bullet", "number"] as const)),
    cmds.changeIndent(r.pick([1, -1, 2])),
    cmds.insertLink("u", r.pick([null, "t"])),
    cmds.insertImage("img/2.png", r.pick([null, 5])),
    cmds.insertTable(1 + r.int(2), 1 + r.int(2)),
    cmds.tableOp(r.pick(["row_above", "row_below", "col_left", "col_right", "del_row", "del_col", "del_table", "split"] as const)),
    cmds.insertPageBreak,
    cmds.insertRule,
    cmds.clearFormatting,
    cmds.stepOverEmpty(r.pick([1, -1] as const)),
    (s, d) => {
      // paste: a copied slice of the document somewhere else
      const slice = s.doc.slice(Math.min(a, b), Math.max(a, b));
      const at = r.int(s.doc.content.size + 1);
      try {
        const tr = s.tr.replace(at, at, slice);
        tr.doc.check(); // ProseMirror's fitting can leave an empty cell; the view drops such pastes
        d?.(tr);
      } catch {
        /* not a place the slice fits */
      }
      return true;
    },
  ];
  let next = state;
  try {
    r.pick(commands)(state, (tr: Transaction) => {
      next = state.apply(tr);
    });
  } catch {
    /* a command that doesn't apply here */
  }
  return next;
}

test.each([1, 2, 3])("random edits export a fixed point (seed %i)", (seed) => {
  const r = new Rand(seed * 104729);
  for (let n = 0; n < 200; n++) {
    // the editor's plugins (nest records, empty tags, tables) take part, as in the page
    let state = createState(htmlToDoc(normaliseHtml(randomHtml(r)).html));
    for (let e = 0; e < 6; e++) state = randomEdit(state, r);
    state.doc.check();
    const out = docToHtml(state.doc);
    expect(normaliseHtml(out).html).toBe(out);
    expect(docToHtml(htmlToDoc(out)), state.doc.toString()).toBe(out);
  }
}, 120000);

test.each([1, 2, 3, 4])("random subset HTML loads and exports unchanged (seed %i)", (seed) => {
  const r = new Rand(seed * 7919);
  for (let n = 0; n < 300; n++) {
    const html = normaliseHtml(randomHtml(r)).html;
    let out = html;
    for (let i = 0; i < 3; i++) out = docToHtml(htmlToDoc(out));
    expect(out).toBe(html);
  }
}, 120000);

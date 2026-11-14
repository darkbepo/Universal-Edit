// CodeMirror setup for the website code editor: colours from the shared --code-* variables
// (same kinds as the desktop's lexing.KINDS), line numbers, current line, brackets, history,
// Tab indents, Ctrl+/ comments; compartments for the parts that change per tab.
import { defaultKeymap, history, historyKeymap, indentWithTab, toggleComment } from "@codemirror/commands";
import { HighlightStyle, bracketMatching, indentOnInput, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers } from "@codemirror/view";
import { type Tag, tags as t } from "@lezer/highlight";

export const KINDS = ["keyword", "string", "comment", "number", "function", "type", "tag", "attribute", "operator", "builtin"] as const;
export type Kind = (typeof KINDS)[number];

export const KIND_TAGS: Record<Kind, Tag[]> = {
  keyword: [t.keyword, t.controlKeyword, t.modifier, t.operatorKeyword, t.definitionKeyword, t.moduleKeyword],
  string: [t.string, t.regexp, t.special(t.string), t.character, t.escape],
  comment: [t.comment, t.lineComment, t.blockComment, t.docComment],
  number: [t.number, t.bool, t.null, t.atom, t.unit],
  function: [t.function(t.variableName), t.function(t.propertyName), t.className, t.definition(t.function(t.variableName)), t.macroName],
  type: [t.typeName, t.namespace, t.labelName],
  tag: [t.tagName, t.heading],
  attribute: [t.attributeName, t.propertyName],
  operator: [t.operator, t.compareOperator, t.arithmeticOperator, t.logicOperator],
  builtin: [t.standard(t.variableName), t.self, t.special(t.variableName)],
};

export const codeHighlight = HighlightStyle.define(
  KINDS.map((kind) => ({
    tag: KIND_TAGS[kind],
    color: `var(--code-${kind})`,
    ...(kind === "comment" ? { fontStyle: "italic" } : {}),
    ...(kind === "keyword" ? { fontWeight: "600" } : {}),
  })),
);

export const MONOSPACE = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", Menlo, monospace';
export const BASE_FONT_PX = 13;

const theme = EditorView.theme({
  "&": { height: "100%", backgroundColor: "var(--surface)", color: "var(--text)" },
  ".cm-scroller": { fontFamily: MONOSPACE, lineHeight: "1.5" },
  ".cm-content": { caretColor: "var(--text)" },
  ".cm-gutters": { backgroundColor: "var(--surface)", color: "var(--code-gutter)", border: "none" },
  ".cm-activeLine": { backgroundColor: "var(--code-line)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text)" },
  "&.cm-focused .cm-matchingBracket, .cm-matchingBracket": { backgroundColor: "var(--code-bracket)" },
  ".cm-find-match": { backgroundColor: "var(--code-match)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--accent-border)" },
});

/** The parts that change per tab or by a setting. */
export const compartments = {
  language: new Compartment(),
  lint: new Compartment(),
  wrap: new Compartment(),
  font: new Compartment(),
  indent: new Compartment(),
};

export const fontSize = (px: number): Extension => EditorView.theme({ ".cm-scroller": { fontSize: `${px}px` } });

export function baseExtensions(opts: { onChange: () => void; onCursor: (line: number, col: number) => void }): Extension[] {
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    history(),
    drawSelection(),
    indentOnInput(),
    bracketMatching(),
    syntaxHighlighting(codeHighlight),
    theme,
    keymap.of([{ key: "Mod-/", run: toggleComment }, indentWithTab, ...historyKeymap, ...defaultKeymap]),
    compartments.language.of([]),
    compartments.lint.of([]),
    compartments.wrap.of([]),
    compartments.font.of(fontSize(BASE_FONT_PX)),
    compartments.indent.of(indentUnit.of("    ")),
    EditorState.tabSize.of(4),
    EditorView.updateListener.of((u) => {
      if (u.docChanged) opts.onChange();
      if (u.selectionSet || u.docChanged) {
        const head = u.state.selection.main.head;
        const line = u.state.doc.lineAt(head);
        opts.onCursor(line.number, head - line.from + 1);
      }
    }),
  ];
}

// CodeMirror languages for the website code editor, loaded only when a tab needs one. Keyed by
// the shared language table (src/umc/codeedit/languages.py → languages.json); the comment marks
// come from the table too, so Ctrl+/ comments the same way as the desktop app.
import { EditorState, type Extension } from "@codemirror/state";
import { StreamLanguage, type StreamParser } from "@codemirror/language";
import table from "./languages.json";

interface Lang {
  id: string;
  line_comment: string | null;
  block_comment: string[] | null;
  codemirror: string;
}
const LANGS = table.languages as Lang[];

export const LANGUAGE_IDS: string[] = LANGS.map((l) => l.id);

const stream = async (load: () => Promise<StreamParser<unknown>>): Promise<Extension> =>
  StreamLanguage.define(await load());

/** CodeMirror key (the table's `codemirror` column) → its language. */
const LOADERS: Record<string, () => Promise<Extension>> = {
  css: async () => (await import("@codemirror/lang-css")).css(),
  sass: async () => (await import("@codemirror/lang-sass")).sass({ indented: false }),
  less: () => stream(async () => (await import("@codemirror/legacy-modes/mode/css")).less),
  javascript: async () => (await import("@codemirror/lang-javascript")).javascript(),
  jsx: async () => (await import("@codemirror/lang-javascript")).javascript({ jsx: true }),
  typescript: async () => (await import("@codemirror/lang-javascript")).javascript({ typescript: true, jsx: true }),
  python: async () => (await import("@codemirror/lang-python")).python(),
  java: async () => (await import("@codemirror/lang-java")).java(),
  cpp: async () => (await import("@codemirror/lang-cpp")).cpp(),
  csharp: () => stream(async () => (await import("@codemirror/legacy-modes/mode/clike")).csharp),
  kotlin: () => stream(async () => (await import("@codemirror/legacy-modes/mode/clike")).kotlin),
  go: async () => (await import("@codemirror/lang-go")).go(),
  rust: async () => (await import("@codemirror/lang-rust")).rust(),
  php: async () => (await import("@codemirror/lang-php")).php(),
  sql: async () => (await import("@codemirror/lang-sql")).sql(),
  json: async () => (await import("@codemirror/lang-json")).json(),
  xml: async () => (await import("@codemirror/lang-xml")).xml(),
  yaml: async () => (await import("@codemirror/lang-yaml")).yaml(),
  markdown: async () => (await import("@codemirror/lang-markdown")).markdown(),
  html: async () => (await import("@codemirror/lang-html")).html(),
  ruby: () => stream(async () => (await import("@codemirror/legacy-modes/mode/ruby")).ruby),
  shell: () => stream(async () => (await import("@codemirror/legacy-modes/mode/shell")).shell),
  powershell: () => stream(async () => (await import("@codemirror/legacy-modes/mode/powershell")).powerShell),
  ini: () => stream(async () => (await import("@codemirror/legacy-modes/mode/properties")).properties),
  properties: () => stream(async () => (await import("@codemirror/legacy-modes/mode/properties")).properties),
  toml: () => stream(async () => (await import("@codemirror/legacy-modes/mode/toml")).toml),
  lua: () => stream(async () => (await import("@codemirror/legacy-modes/mode/lua")).lua),
  swift: () => stream(async () => (await import("@codemirror/legacy-modes/mode/swift")).swift),
  r: () => stream(async () => (await import("@codemirror/legacy-modes/mode/r")).r),
  perl: () => stream(async () => (await import("@codemirror/legacy-modes/mode/perl")).perl),
  dockerfile: () => stream(async () => (await import("@codemirror/legacy-modes/mode/dockerfile")).dockerFile),
  stex: () => stream(async () => (await import("@codemirror/legacy-modes/mode/stex")).stex),
};

/** Whether the language gets colours (plain text, batch files, Makefiles … don't). */
export function hasColours(languageId: string): boolean {
  const lang = LANGS.find((l) => l.id === languageId);
  return !!lang && lang.codemirror in LOADERS;
}

/** The editor extensions for a language: its parser (if any) and the table's comment marks. */
export async function loadLanguage(languageId: string): Promise<Extension> {
  const lang = LANGS.find((l) => l.id === languageId);
  if (!lang) return [];
  const comments = EditorState.languageData.of(() => [
    {
      commentTokens: {
        ...(lang.line_comment ? { line: lang.line_comment } : {}),
        ...(lang.block_comment ? { block: { open: lang.block_comment[0], close: lang.block_comment[1] } } : {}),
      },
    },
  ]);
  const load = LOADERS[lang.codemirror];
  if (!load) return [comments];
  const language = await load();
  return [comments, language];
}

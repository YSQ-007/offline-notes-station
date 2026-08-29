/* CodeMirror 6 build entry — exposes a global CM.build({...}) */

import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection, highlightSpecialChars, rectangularSelection, crosshairCursor } from "@codemirror/view";
import { indentOnInput, syntaxHighlighting, defaultHighlightStyle, bracketMatching, foldGutter, foldKeymap, indentUnit } from "@codemirror/language";
import { defaultKeymap, history, historyKeymap, indentMore, indentLess, insertNewlineAndIndent } from "@codemirror/commands";
import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { markdown, markdownKeymap } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { json } from "@codemirror/lang-json";
import { StreamLanguage } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { oneDark } from "@codemirror/theme-one-dark";

// Map file extension / meta -> CodeMirror language extension
function langFor(meta) {
  meta = (meta || "").toLowerCase();
  if (meta.includes("python") || meta.includes("py")) return python();
  if (meta.includes("shell") || meta.includes("sh") || meta.includes("bash")) return StreamLanguage.define(shell);
  if (meta.includes("json")) return json();
  if (meta.includes("markdown") || meta.includes("md")) return markdown();
  return [];
}

window.CodeMirror = {
  build({ host, value, meta, dark, onchange }) {
    const theme = dark ? [oneDark] : [];
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: value || "",
        extensions: [
          lineNumbers(),
          highlightActiveLine(),
          highlightSpecialChars(),
          drawSelection(),
          rectangularSelection(),
          crosshairCursor(),
          bracketMatching(),
          foldGutter(),
          indentOnInput(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          closeBrackets(),
          history(),
          keymap.of([
            { key: "Tab", run: (v) => v.state.selection.empty ? indentMore(v) : true },
            { key: "Shift-Tab", run: indentLess },
            { key: "Enter", run: insertNewlineAndIndent },
            ...closeBracketsKeymap,
            ...defaultKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...markdownKeymap,
          ]),
          indentUnit.of("    "),
          langFor(meta),
          theme,
          EditorView.updateListener.of((u) => {
            if (u.docChanged && onchange) onchange(u.state.doc.toString());
          }),
        ],
      }),
    });
    return view;
  },
};
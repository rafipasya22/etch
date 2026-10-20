<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import Themes from "monaco-editor-themes";
import { LspClient } from "../editor/lsp";
import * as monaco from "monaco-editor";

interface SketchFile {
  id: string;
  filename: string;
  content: string;
}
let lsp: LspClient | null = null;
let diagnosticsProviderDisposable: monaco.IDisposable | null = null;

const documentVersions = new Map<string, number>(); // fileId -> version

let currentWorkspacePath = "";

function uriForFile(file: SketchFile): string {
  return `file:///${currentWorkspacePath}/${file.filename}`;
}

function registerCompletionProvider() {
  console.log("[LSP] registering completion provider");
  diagnosticsProviderDisposable =
    monaco.languages.registerCompletionItemProvider("cpp", {
      triggerCharacters: [".", ">", ":", '"', "/", "<"],
      provideCompletionItems: async (model, position) => {
        if (!lsp) return { suggestions: [] };
        const fileId = [...models.entries()].find(([, m]) => m === model)?.[0];
        const file = props.files.find((f) => f.id === fileId);
        if (!file) return { suggestions: [] };

        console.log(
          "[LSP] sending completion request for",
          uriForFile(file),
          position,
        );

        const result = await lsp.request("textDocument/completion", {
          textDocument: { uri: uriForFile(file) },
          position: {
            line: position.lineNumber - 1,
            character: position.column - 1,
          },
        });

        console.log("[LSP] completion result:", result);

        const items = Array.isArray(result) ? result : (result?.items ?? []);
        const word = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        };

        return {
          suggestions: items.map((item: any) => ({
            label: item.label,
            kind: monaco.languages.CompletionItemKind.Function,
            insertText: item.insertText ?? item.label,
            detail: item.detail,
            documentation: item.documentation?.value ?? item.documentation,
            range,
          })),
        };
      },
    });
}

let lspReady = false;
const pendingOpens: SketchFile[] = [];

function notifyDidOpen(file: SketchFile) {
  console.log("[LSP] didOpen", uriForFile(file));
  documentVersions.set(file.id, 1);

  if (!lspReady) {
    pendingOpens.push(file);
    return;
  }

  lsp?.notify("textDocument/didOpen", {
    textDocument: {
      uri: uriForFile(file),
      languageId: "cpp",
      version: 1,
      text: file.content,
    },
  });
}

function notifyDidChange(file: SketchFile, newContent: string) {
  if (!lspReady) return; // skip kalau LSP belum siap; isi terbaru udah ke-capture pas didOpen jalan nanti
  const version = (documentVersions.get(file.id) ?? 1) + 1;
  documentVersions.set(file.id, version);
  lsp?.notify("textDocument/didChange", {
    textDocument: { uri: uriForFile(file), version },
    contentChanges: [{ text: newContent }],
  });
}

const props = defineProps<{
  files: SketchFile[];
  activeFileId: string | null;
  fqbn: string;
}>();

const emit = defineEmits<{
  updateContent: [fileId: string, content: string];
}>();

const editorContainer = ref<HTMLElement | null>(null);

Themes.register("darrk", Themes.themes.dark["monokai-pro"]);

let editor: monaco.editor.IStandaloneCodeEditor | null = null;

const models = new Map<string, monaco.editor.ITextModel>();

function languageForFilename(): string {
  return "cpp";
}

function getOrCreateModel(file: SketchFile): monaco.editor.ITextModel {
  let model = models.get(file.id);

  if (!model) {
    model = monaco.editor.createModel(
      file.content,
      languageForFilename(),
    );

    console.log("[Editor] model created, language:", model.getLanguageId());

    model.onDidChangeContent(() => {
      const newContent = model!.getValue();
      emit("updateContent", file.id, newContent);
      notifyDidChange(file, newContent);
    });

    models.set(file.id, model);
    notifyDidOpen(file);
  }

  return model;
}

function switchToFile(fileId: string | null) {
  if (!editor || !fileId) return;

  const file = props.files.find((f) => f.id === fileId);
  if (!file) return;

  const model = getOrCreateModel(file);
  editor.setModel(model);
}

function disposeRemovedModels() {
  const currentIds = new Set(props.files.map((f) => f.id));

  for (const [id, model] of models.entries()) {
    if (!currentIds.has(id)) {
      model.dispose();
      models.delete(id);
    }
  }
}

let lspInitStarted = false;

async function initLsp() {
  if (lspInitStarted) return;

  const activeFile = props.files.find((f) => f.id === props.activeFileId);
  if (!activeFile) {
    console.warn("[LSP] no active file yet, will retry when available");
    return;
  }

  lspInitStarted = true;

  const fqbn = encodeURIComponent(props.fqbn);
  const fname = encodeURIComponent(activeFile.filename);

  lsp = new LspClient(
    `ws://localhost:8000/lsp/clangd?fqbn=${fqbn}&filename=${fname}`,
  );

  const workspacePath = await lsp.workspacePath; // tunggu path asli dari backend
  currentWorkspacePath = workspacePath;

  try {
    const initResult = await lsp.request("initialize", {
      processId: null,
      rootUri: "file:///workspace",
      capabilities: {
        textDocument: {
          completion: { completionItem: { snippetSupport: true } },
          publishDiagnostics: {},
        },
      },
    });
    console.log("[LSP] initialize result:", initResult);
    await lsp.notify("initialized", {});
    lspReady = true;
    for (const file of pendingOpens) {
      notifyDidOpen(file); // sekarang lspReady true, jadi beneran ke-kirim
    }
  } catch (err) {
    console.error("[LSP] failed to initialize:", err);
  }

  lsp.onNotification(
    "textDocument/publishDiagnostics",
    (params: {
      uri: string;
      diagnostics: Array<{ range: any; message: string; severity?: number }>;
    }) => {
      const file = props.files.find((f) => uriForFile(f) === params.uri);
      if (!file) return;
      const model = models.get(file.id);
      if (!model) return;

      const markers = params.diagnostics.map((d) => ({
        startLineNumber: d.range.start.line + 1,
        startColumn: d.range.start.character + 1,
        endLineNumber: d.range.end.line + 1,
        endColumn: d.range.end.character + 1,
        message: d.message,
        severity:
          d.severity === 1
            ? monaco.MarkerSeverity.Error
            : d.severity === 2
              ? monaco.MarkerSeverity.Warning
              : monaco.MarkerSeverity.Info,
      }));
      monaco.editor.setModelMarkers(model, "clangd", markers);
    },
  );

  registerCompletionProvider();
}

onMounted(() => {
  if (!editorContainer.value) return;

  initLsp();

  editor = monaco.editor.create(editorContainer.value, {
    theme: "vs-dark",
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 14,
    padding: { top: 16 },
    scrollBeyondLastLine: false,
    lineNumbers: "on",
  });

  if (props.activeFileId) {
    switchToFile(props.activeFileId);
  }
});

onBeforeUnmount(() => {
  lsp?.dispose();
  diagnosticsProviderDisposable?.dispose();
  editor?.dispose();
  for (const model of models.values()) {
    model.dispose();
  }
  models.clear();
});

watch(
  () => props.activeFileId,
  (newId) => {
    console.log("[Editor] activeFileId changed:", newId);
    switchToFile(newId);
    if (!lsp) {
      initLsp(); // retry kalau belum pernah berhasil connect
    }
  },
);

watch(
  () => props.files,
  (newFiles) => {
    disposeRemovedModels();

    for (const file of newFiles) {
      const model = models.get(file.id);
      if (model && model.getValue() !== file.content) {
        model.setValue(file.content);
      }
    }
  },
  { deep: true },
);
</script>

<template>
  <div ref="editorContainer" class="h-full w-full" />
</template>

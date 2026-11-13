<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref } from "vue";
import Themes from "monaco-editor-themes";
import * as monaco from "monaco-editor";

interface SketchFile {
  id: string;
  filename: string;
  content: string;
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
    });

    models.set(file.id, model);
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

onMounted(() => {
  if (!editorContainer.value) return;

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

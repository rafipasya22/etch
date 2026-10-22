<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, nextTick } from "vue";
import * as Blockly from "blockly";

import "../Blockly/blocks/arduino";
import { arduinoGenerator } from "../Blockly/generators/arduino";
import { arduinoDarkTheme } from "../Blockly/theme";
import { getInstalledLibraryToolboxContents } from "../lib/registry";
import { API_BASE } from "../lib/api";

import "../lib/generators"; // registers forBlock generators for library blocks

const blocklyContainer = ref<HTMLElement | null>(null);

let workspace: Blockly.WorkspaceSvg | null = null;
let resizeObserver: ResizeObserver | null = null;

function generateCode() {
  if (!workspace) return "";
  return arduinoGenerator.workspaceToCode(workspace);
}

function resize() {
  if (!workspace) return;
  Blockly.svgResize(workspace);
}

async function fetchInstalledLibraryNames(): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/libraries/installed`);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.libraries ?? []).map((l: { name: string }) => l.name);
  } catch {
    return [];
  }
}

function buildToolboxContents(
  libraryBlocks: { kind: "block"; type: string }[],
) {
  return [
    { kind: "block", type: "arduino_setup" },
    { kind: "block", type: "arduino_loop" },
    { kind: "block", type: "arduino_pin_mode" },
    { kind: "block", type: "arduino_digital_write" },
    { kind: "block", type: "arduino_digital_read" },
    { kind: "block", type: "arduino_analog_read" },
    { kind: "block", type: "arduino_analog_write" },
    { kind: "block", type: "arduino_delay" },
    { kind: "block", type: "arduino_serial_begin" },
    { kind: "block", type: "arduino_serial_print" },
    ...libraryBlocks,
  ];
}

async function refreshToolbox() {
  if (!workspace) return;

  const installedNames = await fetchInstalledLibraryNames();
  const libraryBlocks = getInstalledLibraryToolboxContents(installedNames);

  workspace.updateToolbox({
    kind: "flyoutToolbox",
    contents: buildToolboxContents(libraryBlocks),
  });
}

defineExpose({
  generateCode,
  resize,
  refreshToolbox,
});

onMounted(async () => {
  await nextTick();
  if (!blocklyContainer.value) return;

  const installedNames = await fetchInstalledLibraryNames();
  const libraryBlocks = getInstalledLibraryToolboxContents(installedNames);
  Blockly.WidgetDiv.createDom();
  Blockly.DropDownDiv.createDom();

  workspace = Blockly.inject(blocklyContainer.value, {
    theme: arduinoDarkTheme,
    toolbox: {
      kind: "flyoutToolbox",
      contents: buildToolboxContents(libraryBlocks),
    },
    grid: {
      spacing: 20,
      length: 3,
      colour: "#444444",
      snap: true,
    },
    zoom: {
      controls: true,
      wheel: true,
      startScale: 1,
      maxScale: 2,
      minScale: 0.5,
    },
    trashcan: true,
  });

  resizeObserver = new ResizeObserver(() => {
    resize();
  });

  resizeObserver.observe(blocklyContainer.value);

  requestAnimationFrame(() => {
    resize();
  });
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  workspace?.dispose();

  resizeObserver = null;
  workspace = null;
});
</script>

<template>
  <div ref="blocklyContainer" class="w-full h-full min-h-0 z-0"></div>
</template>
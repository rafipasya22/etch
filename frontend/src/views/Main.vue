<script setup lang="ts">
// ============================================================
// IMPORTS
// ============================================================
import Editor from "../components/Editor.vue";
import BlockEditor from "../components/Blockeditor.vue";
import FolderTree from "../components/Tree.vue";
import { supabase } from "../lib/supabase";
import { useAuth } from "../stores/auth";
import Libs from "../components/Libs.vue";
import { watch, computed, nextTick, onMounted, ref, markRaw } from "vue";
import { flashEsp32, flashAvr } from "../lib/flasher";

// ============================================================
// PROPS
// ============================================================
const props = withDefaults(
  defineProps<{
    size?: number;
    strokeWidth?: number;
    color?: string;
  }>(),
  {
    size: 12,
    strokeWidth: 4,
    color: "#ffffff",
  },
);

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const radius = computed(() => 25 - props.strokeWidth / 2);
const circumference = computed(() => 2 * Math.PI * radius.value);

// ============================================================
// AUTH
// ============================================================
const { session, user, profile, logout } = useAuth();

async function logOutAccount() {
  logout();
  closeSideMenu();
  window.location.reload();
}

// ============================================================
// UI STATE — menus, modals, sidebar, panels
// ============================================================
const isHidden = ref(true);
const showMenu = ref(false);
const showConf = ref(false);
const updateStatus = ref("");
const activeMenu = ref("edit");
const isSideHidden = ref(false);
const showConfCreate = ref(false);
const isLoading = ref(false);
const isUpdating = ref(false);
const selectEditor = ref("code");
const selectOutput = ref("build");
const showConfDelete = ref(false);

async function gotoManage() {
  activeMenu.value = "manage";
}

async function gotoEdit() {
  activeMenu.value = "edit";
}

async function openMenu() {
  showMenu.value = true;
  isHidden.value = true;
}

async function closeMenu() {
  showMenu.value = false;
}

function openSideMenu() {
  isHidden.value = !isHidden.value;
}

function closeSideMenu() {
  isHidden.value = false;
}

async function hideSide() {
  isSideHidden.value = !isSideHidden.value;
}

async function klos() {
  if (showConfCreate.value == true) {
    showConfCreate.value = false;
  } else if (showConfDelete.value == true) {
    showConfDelete.value = false;
  } else if (showMenu.value == true) {
    closeMenu();
  } else if (isCreateFileOpen.value == true) {
    isCreateFileOpen.value = false;
  } else if (isLibsOpen.value == true) {
    closeLibs();
  }
}

async function submitt() {
  if (showConfCreate.value == true) {
    promptCreateFolder();
  } else if (showConfDelete.value == true) {
    deleteSelectedFolder();
  } else if (isCreateFileOpen.value == true) {
    addFile();
  }
}

// ============================================================
// BUILD OUTPUT PANEL — resize handle
// ============================================================
const box = ref<HTMLElement | null>(null);
const isResizing = ref(false);
const buildOutputRef = ref<HTMLElement | null>(null);

let bottomY = 0;
let rafId: number | null = null;
const MIN_HEIGHT = 110;
const MAX_HEIGHT = 300;
const height = ref(MIN_HEIGHT);

function startResize() {
  if (!box.value) return;

  const rect = box.value.getBoundingClientRect();
  bottomY = rect.bottom;
  isResizing.value = true;

  document.body.style.userSelect = "none";
  document.body.style.cursor = "ns-resize";

  window.addEventListener("mousemove", onResize);
  window.addEventListener("mouseup", stopResize);
}

function onResize(e: MouseEvent) {
  if (rafId !== null) return;

  rafId = requestAnimationFrame(() => {
    const newHeight = bottomY - e.clientY;
    height.value = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, newHeight));
    rafId = null;
  });
}

function stopResize() {
  isResizing.value = false;
  document.body.style.userSelect = "";
  document.body.style.cursor = "";

  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }

  window.removeEventListener("mousemove", onResize);
  window.removeEventListener("mouseup", stopResize);
}

async function scrollBuildOutput() {
  await nextTick();

  if (buildOutputRef.value) {
    buildOutputRef.value.scrollTop = buildOutputRef.value.scrollHeight;
  }
}

// ============================================================
// SKETCH FILES / EDITOR
// ============================================================
interface SketchFile {
  id: string;
  filename: string;
  content: string;
}

const blockEditorRef = ref();
const openFiles = ref<SketchFile[]>([]);
const activeFileIndex = ref(0);
const originalFilenames = ref<string[]>([]);
let msg = ref("");
const isCreateFileOpen = ref(false);
const showAlert = ref(false);
const newFileName = ref("");

const activeFile = computed(() => openFiles.value[activeFileIndex.value]);

const renamingFileIndex = ref<number | null>(null);
const renameFileDraft = ref("");
const renameFileInputRef = ref<HTMLInputElement | null>(null);
const isCreatingFile = ref(false);

async function onLibraryChanged() {
  await blockEditorRef.value?.refreshToolbox();
}

function hasInoFile(): boolean {
  return openFiles.value.some((f) => f.filename.toLowerCase().endsWith(".ino"));
}

async function openCreatefile() {
  isCreateFileOpen.value = true;
}

async function alertUser() {
  showAlert.value = true;
  await new Promise((resolve) => setTimeout(resolve, 2000));
  showAlert.value = false;
}

const ext = ref(".ino");

function addFile() {
  if (!newFileName.value) {
    msg.value = "File Name was not filled!";
    alertUser();
    return;
  }
  const joined = newFileName.value + ext.value;
  const trimmed = joined.trim();
  const lower = trimmed.toLowerCase();
  isCreatingFile.value = true;

  try {
    if (!ext.value) {
      msg.value = "Filename must end with .ino, .h, .hpp, or .cpp";
      alertUser();
      throw Error;
    }
    if (ext.value === ".ino" && hasInoFile()) {
      msg.value = "A sketch can only have one .ino file.";
      alertUser();
      throw Error;
    }
    if (openFiles.value.some((f) => f.filename.toLowerCase() === lower)) {
      msg.value = "A file with this name already exists.";
      alertUser();
      throw Error;
    }

    openFiles.value.push({
      id: crypto.randomUUID(),
      filename: trimmed,
      content: "",
    });
    activeFileIndex.value = openFiles.value.length - 1;

    isCreateFileOpen.value = false;
  } catch (Error) {
    console.log("error:", msg.value);
    return;
  } finally {
    isCreatingFile.value = true;
    newFileName.value = "";
  }
}

function closeFile(i: number) {
  const file = openFiles.value[i];
  if (file.filename.toLowerCase().endsWith(".ino")) {
    const inoCount = openFiles.value.filter((f) =>
      f.filename.toLowerCase().endsWith(".ino"),
    ).length;
    if (inoCount <= 1) {
      msg.value = "A sketch must have at least one .ino file.";
      alertUser();
      return;
    }
  }
  openFiles.value.splice(i, 1);
  if (activeFileIndex.value >= openFiles.value.length) {
    activeFileIndex.value = Math.max(0, openFiles.value.length - 1);
  }
}

function renameFile(i: number, newName: string) {
  const file = openFiles.value[i];
  const trimmed = newName.trim();
  const lower = trimmed.toLowerCase();
  const validExt = [".ino", ".h", ".hpp", ".cpp"];
  const ext = validExt.find((e) => lower.endsWith(e));

  if (!ext) {
    alert("Filename must end with .ino, .h, .hpp, or .cpp");
    return;
  }

  const wasIno = file.filename.toLowerCase().endsWith(".ino");
  const willBeIno = ext === ".ino";

  if (wasIno && !willBeIno) {
    const inoCount = openFiles.value.filter((f) =>
      f.filename.toLowerCase().endsWith(".ino"),
    ).length;
    if (inoCount <= 1) {
      alert("A sketch must have at least one .ino file.");
      return;
    }
  }
  if (!wasIno && willBeIno && hasInoFile()) {
    alert("A sketch can only have one .ino file.");
    return;
  }
  if (
    openFiles.value.some(
      (f, idx) => idx !== i && f.filename.toLowerCase() === lower,
    )
  ) {
    alert("A file with this name already exists.");
    return;
  }

  file.filename = trimmed;
}

function startRenameFile(i: number) {
  renamingFileIndex.value = i;
  renameFileDraft.value = openFiles.value[i].filename;
  nextTick(() => {
    renameFileInputRef.value?.focus();
    renameFileInputRef.value?.select();
  });
}

function confirmRenameFile(i: number) {
  if (renamingFileIndex.value === null) return;
  if (renameFileDraft.value.trim()) {
    renameFile(i, renameFileDraft.value);
  }
  renamingFileIndex.value = null;
}

function handleEditorUpdate(fileId: string, content: string) {
  const file = openFiles.value.find((f) => f.id === fileId);
  if (file) file.content = content;
}

function generateCodeFromBlocks() {
  const code = blockEditorRef.value?.generateCode();
  if (!code) return;

  const inoFile = openFiles.value.find((f) =>
    f.filename.toLowerCase().endsWith(".ino"),
  );
  if (inoFile) {
    inoFile.content = code;
  } else {
    openFiles.value.push({
      id: crypto.randomUUID(),
      filename: "sketch.ino",
      content: code,
    });
  }

  selectEditor.value = "code";
}

// ============================================================
// LOCAL FILE IMPORT / EXPORT
// ============================================================
const fileInput = ref<HTMLInputElement | null>(null);

function openLocalFile() {
  fileInput.value?.click();
}

async function loadLocalFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;

  try {
    const content = await file.text();
    const filename = file.name;
    const lower = filename.toLowerCase();

    const validExt = [".ino", ".h", ".hpp", ".cpp", ".txt"];
    if (!validExt.some((e) => lower.endsWith(e))) {
      alert("Unsupported file type.");
      return;
    }

    if (lower.endsWith(".ino") && hasInoFile()) {
      const confirmed = confirm(
        "This project already has a .ino file. Replace it with the imported one?",
      );
      if (!confirmed) return;

      openFiles.value = openFiles.value.filter(
        (f) => !f.filename.toLowerCase().endsWith(".ino"),
      );
    }

    const existingIndex = openFiles.value.findIndex(
      (f) => f.filename.toLowerCase() === lower,
    );

    if (existingIndex >= 0) {
      openFiles.value[existingIndex].content = content;
      activeFileIndex.value = existingIndex;
    } else {
      openFiles.value.push({
        id: crypto.randomUUID(),
        filename,
        content,
      });
      activeFileIndex.value = openFiles.value.length - 1;
    }
  } catch (error) {
    console.error("Failed to load local file:", error);
    alert("Failed to open file.");
  }

  input.value = "";
}

function saveProjectToPC() {
  for (const file of openFiles.value) {
    const blob = new Blob([file.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }
}

// ============================================================
// BOARD DETECTION
// ============================================================
interface Board {
  port: string;
  board: string | null;
  fqbn: string | null;
  identified: boolean;
  handle: SerialPort;
}

const USB_IDS: Record<string, { board: string; fqbn: string }> = {
  "10c4:ea60": { board: "ESP32 Dev Module", fqbn: "esp32:esp32:esp32" },
  "1a86:55d4": { board: "ESP32 (CH9102)", fqbn: "esp32:esp32:esp32" },
  "303a:1001": { board: "ESP32-S3 (native USB)", fqbn: "esp32:esp32:esp32s3" },
  "1a86:7523": { board: "Arduino Uno", fqbn: "arduino:avr:uno" },
  "2341:0043": { board: "Arduino Uno", fqbn: "arduino:avr:uno" },
};

const BOARD_OPTIONS = [
  { label: "ESP32 Dev Module", fqbn: "esp32:esp32:esp32" },
  { label: "ESP32-S3 Dev Module", fqbn: "esp32:esp32:esp32s3" },
  { label: "ESP32-C3 Dev Module", fqbn: "esp32:esp32:esp32c3" },
  { label: "Arduino Uno", fqbn: "arduino:avr:uno" },
  { label: "Arduino Nano", fqbn: "arduino:avr:nano" },
  { label: "Arduino Mega 2560", fqbn: "arduino:avr:mega" },
];

const manualFqbn = ref(localStorage.getItem("manualFqbn") ?? "");

const activeFqbn = computed(
  () => manualFqbn.value || selectedBoard.value?.fqbn || null,
);

watch(manualFqbn, (v) => localStorage.setItem("manualFqbn", v));

const boards = ref<Board[]>([]);
const selectedBoard = ref<Board | null>(null);
const loadingBoards = ref(false);

function toBoard(port: SerialPort, i: number): Board {
  const { usbVendorId: v, usbProductId: p } = port.getInfo();
  const key =
    v !== undefined && p !== undefined
      ? `${v.toString(16).padStart(4, "0")}:${p.toString(16).padStart(4, "0")}`
      : "";
  const known = USB_IDS[key];
  return {
    port: key ? `USB ${key}` : `Port ${i + 1}`,
    board: known?.board ?? null,
    fqbn: known?.fqbn ?? null,
    identified: !!known,
    handle: markRaw(port),
  };
}

async function detectBoards() {
  loadingBoards.value = true;
  try {
    const ports = await navigator.serial.getPorts();
    boards.value = ports.map(toBoard);
    selectedBoard.value =
      boards.value.find((b) => b.identified) ?? boards.value[0] ?? null;
  } catch (error) {
    console.error("Board detection failed:", error);
    boards.value = [];
    selectedBoard.value = null;
  } finally {
    loadingBoards.value = false;
  }
}

async function addBoard() {
  try {
    await navigator.serial.requestPort();
  } catch {
    return;
  }
  await detectBoards();
}

// ============================================================
// COMPILE / UPLOAD / FIRMWARE
// ============================================================
const compiling = ref(false);
const uploading = ref(false);
const compileResult = ref<any>(null);
const uploadResult = ref<any>(null);

function downloadFirmware() {
  if (!compileResult.value?.build_id) return;
  window.open(
    `${API}/build/${compileResult.value.build_id}/firmware`,
    "_blank",
  );
}

async function uploadCode() {
  const board = selectedBoard.value;
  if (!board) return alert("Please select a port.");

  const fqbn = activeFqbn.value;
  if (!fqbn) return alert("Select a board type first.");

  try {
    if (serialConnected.value) await closeSerial();

    await compileCode();
    if (!compileResult.value?.success || !compileResult.value.build_id) return;

    const url = `${API}/build/${compileResult.value.build_id}/firmware`;
    const log = (m: string) => {
      compileResult.value.upload_output += m + "\n";
      scrollBuildOutput();
    };

    uploading.value = true;
    compileResult.value.upload_output = "";

    try {
      if (fqbn.startsWith("esp32:")) {
        await flashEsp32(board.handle, url, log);
      } else if (fqbn === "arduino:avr:uno" || fqbn === "arduino:avr:nano") {
        await flashAvr(board.handle, fqbn, url, log);
      } else {
        throw new Error(`Flashing ${fqbn} is not supported yet.`);
      }
      compileResult.value.message = "Upload successful!";
    } catch (e) {
      compileResult.value.success = false;
      compileResult.value.error = `Upload failed: ${e}`;
    }
  } catch (e) {
    compileResult.value = {
      ...(compileResult.value ?? {}),
      success: false,
      error: `Upload failed: ${e}`,
    };
  } finally {
    uploading.value = false;
  }
}

async function compileCode() {
  if (!selectedBoard.value) {
    alert("Please select a board");
    return;
  }

  const fqbn = activeFqbn.value;
  if (!fqbn) {
    alert("Select a board type first.");
    return;
  }

  if (!hasInoFile()) {
    alert("Project must have a .ino file.");
    return;
  }

  selectOutput.value = "build";
  compiling.value = true;
  compileResult.value = { success: true, upload_output: "" };
  uploadResult.value = null;

  try {
    const response = await fetch(`${API}/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        board: fqbn,
        files: openFiles.value.map((f) => ({
          filename: f.filename,
          content: f.content,
        })),
      }),
    });

    const data = await response.json();

    if (!response.ok || !data.success) {
      const d = data.detail;
      data.success = false;
      data.error =
        data.stderr ||
        data.stdout ||
        (typeof d === "string" ? d : d?.message) ||
        `Compile failed (HTTP ${response.status})`;
    }
    compileResult.value = data;
  } catch (error) {
    compileResult.value = {
      success: false,
      error: "Failed to connect to compiler server.",
    };
  } finally {
    compiling.value = false;
  }
}

// ============================================================
// SERIAL MONITOR
// ============================================================
const serialConnected = ref(false);
const serialOutput = ref("");
const serialInput = ref("");
const baudRate = ref(115200);
const serialOutputRef = ref<HTMLElement | null>(null);

async function scrollSerialOutput() {
  await nextTick();

  if (serialOutputRef.value) {
    serialOutputRef.value.scrollTop = serialOutputRef.value.scrollHeight;
  }
}

let serialPort: SerialPort | null = null;
let serialReader: ReadableStreamDefaultReader<Uint8Array> | null = null;
let readLoop: Promise<void> | null = null;

async function readSerialLoop() {
  const decoder = new TextDecoder();
  while (serialPort?.readable && serialConnected.value) {
    serialReader = serialPort.readable.getReader();
    try {
      while (true) {
        const { value, done } = await serialReader.read();
        if (done) break;
        serialOutput.value += decoder.decode(value, { stream: true });
        scrollSerialOutput();
      }
    } catch (e) {
      serialOutput.value += `\n[ERROR] ${e}\n`;
      break;
    } finally {
      serialReader.releaseLock();
    }
  }
}

async function openSerial() {
  const board = selectedBoard.value;
  if (!board) return alert("Please select a board.");
  if (serialConnected.value) return;
  try {
    await board.handle.open({ baudRate: baudRate.value });
    serialPort = board.handle;
    serialConnected.value = true;
    serialOutput.value += `Connected to ${board.port} @ ${baudRate.value}\n`;
    readLoop = readSerialLoop();
  } catch (e) {
    serialOutput.value += `\n[ERROR] ${e}\n`;
  }
}

async function sendSerial() {
  if (!serialPort?.writable || !serialInput.value) return;
  const writer = serialPort.writable.getWriter();
  await writer.write(new TextEncoder().encode(serialInput.value + "\n"));
  writer.releaseLock();
  serialInput.value = "";
}

async function closeSerial() {
  serialConnected.value = false;
  try {
    await serialReader?.cancel();
  } catch {}
  try {
    await readLoop;
  } catch {}
  try {
    await serialPort?.close();
  } catch {}
  serialPort = null;
  serialOutput.value += "\n[Serial connection closed]\n";
}

function clearSerial() {
  serialOutput.value = "";
}

// ============================================================
// PROJECTS
// ============================================================
interface Project {
  id: string;
  name: string;
  code?: string;
  created_at?: string;
  updated_at?: string;
  folder_id: string | null;
}

const projects = ref<Project[]>([]);
const currentProjectId = ref<string | null>(null);
const currentProjectName = ref("Untitled");
const currentFolderId = ref<string | null>(null);

async function loadProjects() {
  if (!user.value) {
    projects.value = [];
    return;
  }

  isLoading.value = true;

  try {
    const { data } = await supabase
      .from("projects")
      .select("id, name, code, created_at, updated_at, folder_id")
      .eq("user_id", user.value.id)
      .order("updated_at", { ascending: false });

    projects.value = data ?? [];
  } catch (error) {
    console.error("Failed to load projects:", error);
  } finally {
    isLoading.value = false;
  }
}

const isSaving = ref(false);

async function saveProject() {
  if (!user.value) return;
  isSaving.value = true;
  try {
    if (currentProjectId.value) {
      await supabase
        .from("projects")
        .update({
          name: currentProjectName.value,
          updated_at: new Date().toISOString(),
        })
        .eq("id", currentProjectId.value)
        .eq("user_id", user.value.id);
    } else {
      const { data, error } = await supabase
        .from("projects")
        .insert({
          user_id: user.value.id,
          name: currentProjectName.value,
          folder_id: currentFolderId.value,
        })
        .select()
        .single();

      if (error) {
        throw error;
      }
      currentProjectId.value = data.id;
    }

    const currentFilenames = openFiles.value.map((f) => f.filename);
    const removedFilenames = originalFilenames.value.filter(
      (f) => !currentFilenames.includes(f),
    );

    if (removedFilenames.length > 0) {
      await supabase
        .from("project_files")
        .delete()
        .eq("project_id", currentProjectId.value)
        .in("filename", removedFilenames);
    }

    const rows = openFiles.value.map((f) => ({
      project_id: currentProjectId.value,
      filename: f.filename,
      content: f.content,
    }));

    const { error: fileError } = await supabase
      .from("project_files")
      .upsert(rows, { onConflict: "project_id,filename" });

    if (fileError) throw fileError;

    originalFilenames.value = currentFilenames;
    await loadProjects();
  } catch (error) {
    msg.value = JSON.stringify(error);
    alert();
  } finally {
    isSaving.value = false;
  }
}

async function openProject(projectId: string) {
  if (!user.value) return;

  try {
    const { data: proj, error: projError } = await supabase
      .from("projects")
      .select("*")
      .eq("id", projectId)
      .eq("user_id", user.value.id)
      .single();

    if (projError) {
      throw projError;
    }

    const { data: files, error: fileError } = await supabase
      .from("project_files")
      .select("id, filename, content")
      .eq("project_id", projectId);

    if (fileError) {
      throw fileError;
    }

    currentProjectId.value = proj.id;
    currentProjectName.value = proj.name;

    openFiles.value =
      files && files.length > 0
        ? files
        : [{ id: crypto.randomUUID(), filename: "sketch.ino", content: "" }];

    originalFilenames.value = openFiles.value.map((f) => f.filename);
    activeFileIndex.value = 0;

    localStorage.setItem("lastProjectId", projectId);
  } catch (error) {
    msg.value = JSON.stringify(error);
    alert();
  }
}

async function deleteProject(projectId: string) {
  if (!user.value) return;

  try {
    const { error } = await supabase
      .from("projects")
      .delete()
      .eq("id", projectId)
      .eq("user_id", user.value.id);

    if (error) {
      throw error;
    }

    if (currentProjectId.value === projectId) {
      currentProjectId.value = null;
      currentProjectName.value = "Untitled";
      newProject();
    }

    await loadProjects();
  } catch (error) {
    console.error("Failed to delete project:", error);
    msg.value = "Failed to delete project: " + JSON.stringify(error);
    alert();
  }
}

function newProject() {
  currentProjectId.value = null;
  currentProjectName.value = "Untitled";
  openFiles.value = [
    {
      id: crypto.randomUUID(),
      filename: "sketch.ino",
      content: `void setup() {\n\n}\n\nvoid loop() {\n\n}`,
    },
  ];
  activeFileIndex.value = 0;
}

// ============================================================
// FOLDERS
// ============================================================
interface FolderNode {
  id: string;
  name: string;
  parent_id: string | null;
}

const folders = ref<FolderNode[]>([]);
const newFolderName = ref("");
const isCreating = ref(false);
const idParent = ref<string>();
const idFolder = ref<string>();
const isDeleting = ref(false);

async function loadFolders() {
  if (!user.value) {
    folders.value = [];
    return;
  }

  const { data, error } = await supabase
    .from("folders")
    .select("id, name, parent_id")
    .eq("user_id", user.value.id)
    .order("name");

  if (error) {
    console.error(error);
    return;
  }
  folders.value = data ?? [];
}

async function createFolder(name: string, parentId: string | null) {
  if (!user.value) return;
  isCreating.value = true;

  try {
    const { error: gege } = await supabase
      .from("folders")
      .insert({ user_id: user.value.id, name, parent_id: parentId });

    if (gege) {
      console.error("PROFILE UPDATE ERROR:", gege);
      throw gege;
    }

    await loadFolders();
  } catch (error) {
    console.error(error);
    return;
  } finally {
    isCreating.value = false;
  }
}

async function renameFolder(folderId: string, name: string) {
  const { error } = await supabase
    .from("folders")
    .update({ name })
    .eq("id", folderId)
    .eq("user_id", user.value!.id);

  if (error) {
    console.error(error);
    return;
  }
  await loadFolders();
}

async function deleteFolder(folderId: string) {
  isDeleting.value = true;
  try {
    const { error } = await supabase
      .from("folders")
      .delete()
      .eq("id", folderId)
      .eq("user_id", user.value!.id);

    if (error) {
      throw error;
    }
    await loadFolders();
    await loadProjects();
  } catch (error) {
    console.error(error);
    return;
  } finally {
    showConfDelete.value = false;
    isDeleting.value = false;
  }
}

async function deleteSelectedFolder() {
  const folderId = idFolder.value;
  if (!folderId) return;

  await deleteFolder(folderId);
  idFolder.value = undefined;
  showConfDelete.value = false;
}

async function moveFolder(folderId: string, newParentId: string | null) {
  if (folderId === newParentId) return;

  const { error } = await supabase
    .from("folders")
    .update({ parent_id: newParentId })
    .eq("id", folderId)
    .eq("user_id", user.value!.id);

  if (error) console.error(error);
  else await loadFolders();
}

async function moveProjectToFolder(projectId: string, folderId: string | null) {
  const { error } = await supabase
    .from("projects")
    .update({ folder_id: folderId })
    .eq("id", projectId)
    .eq("user_id", user.value!.id);

  if (error) console.error(error);
  else await loadProjects();
}

const folderTree = computed(() => {
  const map = new Map<string | null, FolderNode[]>();
  for (const f of folders.value) {
    const list = map.get(f.parent_id) ?? [];
    list.push(f);
    map.set(f.parent_id, list);
  }
  return map;
});

function childFolders(parentId: string | null) {
  return folderTree.value.get(parentId) ?? [];
}

function projectsInFolder(folderId: string | null) {
  return projects.value.filter((p) => p.folder_id === folderId);
}

function setActiveFolder(folderId: string | null) {
  currentFolderId.value = folderId;
}

function openCreateFolderModal(parentId: string | null) {
  idParent.value = parentId ?? undefined;
  showConfCreate.value = true;
}

function openDeleteFolderModal(folderId: string | null) {
  idFolder.value = folderId ?? undefined;
  showConfDelete.value = true;
}

function promptCreateFolder() {
  if (newFolderName.value.trim()) {
    try {
      createFolder(newFolderName.value.trim(), idParent.value ?? null);
    } catch {
      console.error("Create folder failed");
    } finally {
      newFolderName.value = "";
      idParent.value = undefined;
      showConfCreate.value = false;
    }
  }
}

// ============================================================
// PROFILE
// ============================================================
const profileFileInput = ref<HTMLInputElement | null>(null);
const uploadingProfilePhoto = ref(false);
const newEmail = ref("");
const newPass = ref("");
const confPass = ref("");

watch(
  user,
  (u) => {
    if (u?.email) newEmail.value = u.email;
  },
  { immediate: true },
);

function openProfilePhotoPicker() {
  profileFileInput.value?.click();
}

async function updateEmail() {
  if (!user.value || newEmail.value === user.value.email) return;

  const { error } = await supabase.auth.updateUser({ email: newEmail.value });

  if (error) {
    if (
      error.code === "email_exists" ||
      /already.*registered/i.test(error.message)
    ) {
      throw new Error("Email is already registered");
    }
    throw error;
  }

  msg.value = "Confirmation link sent, check your inbox";
}

async function updatePass() {
  if (!user.value || !newPass.value) return;
  if (newPass.value.length < 6) {
    throw new Error("Password too short!");
  }
  if (newPass.value != confPass.value) {
    throw new Error("Password doesn't match");
  }

  try {
    const { error } = await supabase.auth.updateUser({
      password: newPass.value,
    });
    if (error) throw error;
  } catch (error) {
    msg.value = "Updating password failed";
  } finally {
    newPass.value = "";
    confPass.value = "";
  }
}

async function handleProfilePhotoUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];

  if (!file || !user.value) return;

  if (!file.type.startsWith("image/")) {
    alert("Please select an image file.");
    return;
  }

  if (file.size > 5 * 1024 * 1024) {
    alert("Profile picture must be smaller than 5 MB.");
    return;
  }

  uploadingProfilePhoto.value = true;

  try {
    const fileExt = file.name.split(".").pop();
    const filePath = `${user.value.id}/profile.${fileExt}`;

    const { error: uploadError } = await supabase.storage
      .from("Avatars")
      .upload(filePath, file, {
        upsert: true,
        contentType: file.type,
      });

    if (uploadError) {
      console.error("UPLOAD ERROR:", uploadError);
      msg.value = "Uploading profile picture failed " + uploadError;
      throw uploadError;
    }

    const { data } = supabase.storage.from("Avatars").getPublicUrl(filePath);

    const photoUrl = `${data.publicUrl}?t=${Date.now()}`;

    const { error: updateError } = await supabase
      .from("profiles")
      .update({
        profile_photo_url: photoUrl,
      })
      .eq("id", user.value.id);

    if (updateError) {
      console.error("PROFILE UPDATE ERROR:", updateError);
      msg.value = "Updating profile picture failed " + updateError;
      throw updateError;
    }

    if (profile.value) {
      profile.value.profile_photo_url = photoUrl;
    }
  } catch (error) {
    console.error("Profile picture upload failed:", error);
  } finally {
    uploadingProfilePhoto.value = false;
    input.value = "";
  }
}

async function showUpdateConf() {
  showConf.value = true;
  await new Promise((resolve) => setTimeout(resolve, 3000));
  showConf.value = false;
  msg.value = "";
}

async function updateProfile() {
  if (!user.value || !profile.value) {
    return;
  }

  isUpdating.value = true;

  try {
    const { error: updateError } = await supabase
      .from("profiles")
      .update({
        name: profile.value.name,
        username: profile.value.username,
      })
      .eq("id", user.value.id);

    if (updateError) {
      console.error("PROFILE UPDATE ERROR:", updateError);
      msg.value = "Updating profile error " + updateError;
      throw updateError;
    }

    await updateEmail();

    await updatePass();

    updateStatus.value = "success";
    msg.value = "Profile update successful!";
  } catch (error) {
    updateStatus.value = "failed";
    msg.value = (error as Error).message || "Update failed";
  } finally {
    isUpdating.value = false;
    showUpdateConf();
  }
}

// ============================================================
// KEYBOARD SHORTCUTS
// ============================================================
function handleKeyboardShortcuts(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveProject();
  }

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "q") {
    event.preventDefault();
    hideSide();
  }

  if (event.key === "Escape") {
    event.preventDefault();
    klos();
  }

  if (
    showConfCreate.value == true ||
    showConfDelete.value == true ||
    isCreateFileOpen.value == true
  ) {
    if (event.key === "Enter") {
      event.preventDefault();
      submitt();
    }
  } else {
    return;
  }

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
    event.preventDefault();
    openLocalFile();
  }
}

// ============================================================
// LIBRARIES
// ============================================================

const isLibsOpen = ref(false);

async function openLibs() {
  isLibsOpen.value = true;
}

async function closeLibs() {
  isLibsOpen.value = false;
}

// ============================================================
// WATCHERS
// ============================================================
watch(
  user,
  async (newUser) => {
    if (newUser) {
      await loadProjects();
    } else {
      projects.value = [];
      currentProjectId.value = null;
      currentProjectName.value = "Untitled";
    }
  },
  { immediate: true },
);

// ============================================================
// LIFECYCLE
// ============================================================
const colorr = "#ff4400";
onMounted(() => {
  if (session.value) {
    console.log("User is logged in");
    const lastId = localStorage.getItem("lastProjectId");
    if (lastId) openProject(lastId);
  } else {
    console.log("No active session");
  }
  loadFolders();
  loadProjects();
  detectBoards();

  window.addEventListener("keydown", handleKeyboardShortcuts);
});
</script>

<template>
  <input
    ref="fileInput"
    type="file"
    accept=".ino,.cpp,.h,.txt"
    class="hidden"
    @change="loadLocalFile"
  />
  <div
    class="h-screen w-screen flex flex-row bg-gray-950 text-white relative overflow-hidden"
  >
    <div
      @click="klos"
      :class="
        showMenu ||
        showConfCreate ||
        showConfDelete ||
        isCreateFileOpen ||
        isLibsOpen
          ? 'opacity-100 pointer-events-auto'
          : 'opacity-0 pointer-events-none'
      "
      class="fixed inset-0 z-[98] bg-black/70 transition-opacity duration-200"
    ></div>
    <!---Menu-->
    <div
      :class="showMenu ? 'translate-y-10' : '-translate-y-200'"
      class="edit w-200 h-120 rounded-lg border border-[#020202] absolute z-99 bg-[#121212] left-1/2 bottom-50 -translate-x-1/2 shadow-lg flex flex-row justify-start items-center transition-all duration-500 ease"
    >
      <aside
        class="flex flex-col justify-start items-start gap-1 h-full w-[20%] border-r border-[#323232]"
      >
        <button
          @click="gotoEdit"
          :class="activeMenu == 'edit' ? 'text-[#ff4400] bg-[#222222]' : ''"
          class="p-3 w-full h-fit flex flex-row justify-start items-center cursor-pointer hover:text-[#ff4400] hover:bg-[#222222] rounded-tl-lg text-xs"
        >
          <span class="material-symbols-outlined !text-[20px] me-1">
            edit
          </span>
          Edit Profile
        </button>
        <button
          @click="gotoManage"
          :class="activeMenu == 'manage' ? 'text-[#ff4400] bg-[#222222]' : ''"
          class="p-3 w-full h-fit flex flex-row justify-start items-center cursor-pointer hover:text-[#ff4400] hover:bg-[#222222] text-xs"
        >
          <span class="material-symbols-outlined !text-[20px] me-1">
            bookmark_stacks
          </span>
          Manage Projects
        </button>
      </aside>
      <div
        class="flex flex-col justify-between items-start gap-1 w-[80%] h-full px-5 py-3"
      >
        <div class="flex flex-col justify-start items-start gap-2 h-fit w-full">
          <div
            class="flex rounded-lg border border-[#323232] flex-row justify-start gap-2 w-full items-center h-fit px-3 pt-3 pb-5"
          >
            <div
              class="profiles border border-[#323232] h-20 w-20 rounded-full text-xs transition relative"
            >
              <input
                ref="profileFileInput"
                type="file"
                accept="image/*"
                class="hidden"
                @change="handleProfilePhotoUpload"
              />
              <img
                v-if="profile?.profile_photo_url"
                class="w-full h-full rounded-full object-cover"
                :src="profile.profile_photo_url"
                alt=""
              />
              <img
                v-else
                class="w-full h-full rounded-full object-cover"
                src="../assets/user.png"
                alt=""
              />
              <button
                @click="openProfilePhotoPicker"
                :disabled="uploadingProfilePhoto"
                class="rounded-full h-8 w-8 flex justify-center items-center bg-[#ff4400] bottom-0 right-0 -translate-y-5 translate-x-13 hover:bg-[#bf3402] cursor-pointer"
              >
                <span class="material-symbols-outlined !text-[20px]">
                  edit
                </span>
              </button>
            </div>
            <div
              class="w-[80%] h-fit rounded-lg h-fit flex flex-col justify-start items-start px-3"
            >
              <div class="top flex flex-col justify-start items-start w-full">
                <h5
                  class="text-base font-semibold whitespace-nowrap truncate w-100"
                >
                  {{ profile?.name || "Name Cannot be Empty" }}
                </h5>
                <div
                  class="personaldata flex flex-col justify-start items-start"
                >
                  <h5 class="text-xs whitespace-nowrap truncate w-70">
                    Username: @{{ profile?.username || "-" }}
                  </h5>
                  <h5 class="text-xs whitespace-nowrap">
                    Email: {{ user?.email || "-" }}
                  </h5>
                  <h5 class="text-xs whitespace-nowrap">
                    Joined {{ user?.created_at || "-" }}
                  </h5>
                </div>
              </div>
            </div>
          </div>
          <div class="mid flex flex-col justify-start items-start w-full p-3">
            <h1 class="text-lg font-semibold whitespace-nowrap">
              Edit Profile
            </h1>

            <form class="flex flex-col justify-start items-start w-full gap-2">
              <div
                class="flex flex-row justify-start items-center w-full gap-2"
              >
                <div class="flex flex-col gap-2 w-full">
                  <label class="text-xs text-gray-400"> Name </label>

                  <input
                    v-if="profile != null"
                    type="text"
                    v-model="profile.name"
                    class="w-full bg-[#1a1a1a] border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
                  />
                </div>

                <div class="flex flex-col gap-2 w-full">
                  <label class="text-xs text-gray-400"> Username </label>

                  <input
                    v-if="profile != null"
                    type="text"
                    v-model="profile.username"
                    class="w-full bg-[#1a1a1a] border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
                  />
                </div>
              </div>
              <div
                class="flex flex-row justify-start items-center w-full gap-2"
              >
                <div class="flex flex-col gap-2 w-full">
                  <label class="text-xs text-gray-400"> Email </label>

                  <input
                    v-if="user != null"
                    type="text"
                    v-model="newEmail"
                    class="w-full bg-[#1a1a1a] border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
                  />
                </div>
              </div>
              <div
                class="flex flex-row justify-start items-center w-full gap-2"
              >
                <div class="flex flex-col gap-2 w-full">
                  <label class="text-xs text-gray-400"> New Password </label>

                  <input
                    type="password"
                    v-model="newPass"
                    class="w-full bg-[#1a1a1a] border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
                  />
                </div>

                <div class="flex flex-col gap-2 w-full">
                  <label class="text-xs text-gray-400">
                    Confirm Password
                  </label>

                  <input
                    type="password"
                    v-model="confPass"
                    class="w-full bg-[#1a1a1a] border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
                  />
                </div>
              </div>
            </form>
          </div>
        </div>
        <div class="flex flex-row justify-end items-center w-full">
          <button
            v-if="isUpdating"
            class="px-4 py-2 mt-2 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30 justify-center items-center flex pointer-events-none"
          >
            <svg
              width="24"
              height="24"
              viewBox="0 0 50 50"
              class="animate-spin"
            >
              <circle
                cx="25"
                cy="25"
                :r="radius"
                fill="none"
                :stroke="color"
                :stroke-width="7"
                stroke-linecap="round"
                :stroke-dasharray="circumference"
                :stroke-dashoffset="circumference * 0.75"
              />
            </svg>
          </button>
          <button
            @click="updateProfile"
            v-else
            class="px-4 py-2 mt-2 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30"
          >
            Save Changes
          </button>
        </div>
      </div>
    </div>
    <!--Update confirm-->
    <div
      :class="[
        showConf ? 'translate-y-0' : 'translate-y-30',
        updateStatus == 'success'
          ? 'text-green-400'
          : updateStatus == 'failed'
            ? 'text-red-400'
            : '',
      ]"
      class="conf w-100 h-13 rounded-lg border border-[#020202] text-green-400 absolute z-99 bg-[#121212] right-0 bottom-0 shadow-lg flex flex-row justify-start items-center me-2 mb-2 px-3 py-1 transition-all duration-500 ease gap-2"
    >
      <span
        v-if="updateStatus == 'success'"
        class="material-symbols-outlined text-green-400"
      >
        check_circle
      </span>

      <span
        v-else-if="updateStatus == 'failed'"
        class="material-symbols-outlined text-red-400"
      >
        cancel
      </span>
      {{ msg }}
    </div>
    <!--Alert-->
    <div
      :class="showAlert ? 'translate-y-0' : 'translate-y-30'"
      class="conf w-100 h-13 rounded-lg border border-[#020202] text-red-400 absolute z-99 bg-[#121212] right-0 bottom-0 shadow-lg flex flex-row justify-start items-center me-2 mb-2 px-3 py-1 transition-all duration-500 ease gap-2"
    >
      <span class="material-symbols-outlined text-red-400"> cancel </span>
      {{ msg }}
    </div>
    <!--Create folder Confirmation-->
    <div
      :class="showConfCreate ? '-translate-y-[100px]' : '-translate-y-[700px]'"
      class="w-110 h-fit rounded-lg border border-[#020202] absolute z-99 bg-[#121212] left-1/2 bottom-50 -translate-x-1/2 shadow-lg flex flex-col justify-start items-start px-3 py-2 transition-all duration-500 ease gap-2"
    >
      <h1 class="text-lg font-semibold whitespace-nowrap flex flex-col">
        {{ idParent ? "Create Subfolder" : "Create New Folder" }}
        <span v-if="idParent" class="text-xs text-[#727272] font-normal">
          Inside of: {{ folders.find((f) => f.id === idParent)?.name }} Folder
        </span>
      </h1>
      <div class="flex flex-row w-full h-fit px-1 py-2 gap-1">
        <input
          type="text"
          v-model="newFolderName"
          placeholder="Enter Folder Name"
          class="w-80 bg-[#1a1a1a] h-10 border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
        />

        <button
          v-if="isCreating"
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30 flex justify-center items-center"
        >
          <svg width="24" height="24" viewBox="0 0 50 50" class="animate-spin">
            <circle
              cx="25"
              cy="25"
              :r="radius"
              fill="none"
              :stroke="color"
              :stroke-width="7"
              stroke-linecap="round"
              :stroke-dasharray="circumference"
              :stroke-dashoffset="circumference * 0.75"
            />
          </svg>
        </button>
        <button
          v-else
          @click="promptCreateFolder"
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30"
        >
          Create Folder
        </button>
      </div>
    </div>
    <!--Delete folder Confirmation-->
    <div
      :class="showConfDelete ? '-translate-y-[100px]' : '-translate-y-[700px]'"
      class="w-110 h-fit rounded-lg border border-[#020202] absolute z-99 bg-[#121212] left-1/2 bottom-50 -translate-x-1/2 shadow-lg flex flex-col justify-between items-start px-3 py-2 transition-all duration-500 ease gap-2"
    >
      <div
        class="flex flex-col justify-start gap-1 mb-2 text-xs text-[#929292]"
      >
        <h1
          class="text-lg font-semibold whitespace-nowrap flex flex-col text-white"
        >
          Delete Folder
        </h1>
        Delete this folder? Subfolders will be deleted too, and projects inside
        will move to root.
      </div>
      <div class="flex flex-row w-full h-fit px-1 py-2 gap-1 justify-end">
        <button
          @click="klos"
          class="px-2 py-1 rounded-lg bg-[#525252] hover:bg-[#323232] text-sm cursor-pointer h-10 w-30"
        >
          Cancel
        </button>

        <button
          v-if="!isDeleting"
          @click="deleteSelectedFolder"
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30"
        >
          Delete Folder
        </button>
        <button
          v-else
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm h-10 w-30 flex justify-center items-center"
        >
          <svg width="24" height="24" viewBox="0 0 50 50" class="animate-spin">
            <circle
              cx="25"
              cy="25"
              :r="radius"
              fill="none"
              :stroke="color"
              :stroke-width="7"
              stroke-linecap="round"
              :stroke-dasharray="circumference"
              :stroke-dashoffset="circumference * 0.75"
            />
          </svg>
        </button>
      </div>
    </div>
    <!--create file conf-->
    <div
      :class="
        isCreateFileOpen ? '-translate-y-[100px]' : '-translate-y-[700px]'
      "
      class="w-110 h-fit rounded-lg border border-[#020202] absolute z-99 bg-[#121212] left-1/2 bottom-50 -translate-x-1/2 shadow-lg flex flex-col justify-start items-start px-3 py-4 transition-all duration-500 ease gap-2"
    >
      <h1 class="text-lg font-semibold whitespace-nowrap flex flex-col">
        Create New File
        <span class="text-xs text-[#727272] font-normal">
          Filename (e.g. helper.h or sensor.cpp)
        </span>
      </h1>
      <div class="flex flex-row w-full h-fit py-2 gap-1">
        <input
          type="text"
          v-model="newFileName"
          placeholder="Enter File Name"
          class="w-[80%] bg-[#1a1a1a] h-10 border border-[#323232] rounded-lg px-3 py-2 text-sm outline-none focus:border-[#ff4400]"
        />
        <select
          v-model="ext"
          class="rounded w-[20%] border hover:border-[#ff4400] transition-all duration-300 ease border-[#323232] bg-[#121212] px-3 py-2 text-sm text-white outline-none focus:outline-none focus:ring-0 cursor-pointer"
        >
          <option value=".ino">.ino</option>
          <option value=".cpp">.cpp</option>
          <option value=".h">.h</option>
        </select>
      </div>
      <div class="flex flex-row justify-end items-center w-full h-fit">
        <button
          v-if="isCreating"
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30 flex justify-center items-center"
        >
          <svg width="24" height="24" viewBox="0 0 50 50" class="animate-spin">
            <circle
              cx="25"
              cy="25"
              :r="radius"
              fill="none"
              :stroke="color"
              :stroke-width="7"
              stroke-linecap="round"
              :stroke-dasharray="circumference"
              :stroke-dashoffset="circumference * 0.75"
            />
          </svg>
        </button>
        <button
          v-else
          @click="addFile"
          class="px-2 py-1 rounded-lg bg-[#ff4400] hover:bg-[#bf3402] text-sm cursor-pointer h-10 w-30"
        >
          Create File
        </button>
      </div>
    </div>
    <!--libs-->
    <div
      :class="isLibsOpen ? 'translate-y-30' : '-translate-y-200'"
      class="edit w-250 h-140 rounded-lg border border-[#020202] absolute z-99 bg-[#121212] left-1/2 bottom-50 -translate-x-1/2 shadow-lg flex flex-col justify-start items-start transition-all duration-500 ease"
    >
      <Libs class="w-full h-full" @library-changed="onLibraryChanged" />
    </div>
    <aside
      :class="isSideHidden ? 'w-[0%]' : 'w-[20%]'"
      class="h-full flex flex-col justify-between items-start bg-[#121212] border-r border-[#1e1e1e] transition-all duration-500 ease"
    >
      <div class="top h-full w-full flex flex-col justify-start items-start">
        <h1
          class="text-lg text-[#ff4400] font-semibold whitespace-nowrap my-2 p-3"
        >
          Serial <span class="text-white"> Flasher </span>
        </h1>
        <div
          class="content flex flex-col justify-start items-start h-full w-full gap-3 px-3 py-1"
        >
          <button
            @click="newProject"
            class="rounded-lg border truncate border-[#1e1e1e] bg-black text-xs hover:bg-[#121212] hover:border-[#ff4400] transition w-full px-3 py-2 cursor-pointer flex flex-row justify-start items-center"
          >
            <span
              class="material-symbols-outlined !text-[20px] text-[#ff4400] me-2"
            >
              note_add
            </span>
            New Project
          </button>
          <button
            @click="openLocalFile"
            class="rounded-lg border truncate border-[#1e1e1e] bg-black text-xs hover:bg-[#121212] hover:border-[#ff4400] transition w-full px-3 py-2 cursor-pointer flex flex-row justify-start items-center"
          >
            <span
              class="material-symbols-outlined text-[#ff4400] !text-[20px] me-2"
            >
              upload_file
            </span>
            Import Project
          </button>

          <button
            @click="openLibs"
            class="rounded-lg border truncate border-[#1e1e1e] bg-black text-xs hover:bg-[#121212] hover:border-[#ff4400] transition w-full px-3 py-2 cursor-pointer flex flex-row justify-start items-center"
          >
            <span
              class="material-symbols-outlined text-[#ff4400] !text-[20px] me-2"
            >
              newsstand
            </span>
            Install Library
          </button>

          <button
            v-if="selectEditor === 'blocks'"
            @click="generateCodeFromBlocks"
            class="rounded-lg border truncate border-[#1e1e1e] bg-black text-xs hover:bg-[#121212] hover:border-[#ff4400] transition w-full px-3 py-2 cursor-pointer flex flex-row justify-start items-center"
          >
            Generate Code
          </button>
          <div class="flex flex-col w-full items-start justify-start">
            <div
              class="text-sm w-full text-[#525252] font-semibold whitespace-nowrap flex flex-row justify-between items-center"
            >
              My Projects
              <div
                class="flex flex-row w-20 h-fit gap-1 justify-end items-center"
              >
                <button
                  @click="openCreateFolderModal(null)"
                  class="text-xs text-[#525252] hover:text-[#ff4400] px-2 py-1 cursor-pointer transition-all duration-300 ease"
                >
                  <span class="material-symbols-outlined !text-[20px]">
                    create_new_folder
                  </span>
                </button>
                <button
                  @click="loadProjects"
                  class="text-xs text-[#525252] hover:text-white transition-all duration-300 ease cursor-pointer"
                >
                  <span
                    :class="isLoading ? 'animate-spin text-[#ff4400]' : ''"
                    class="material-symbols-outlined !text-[20px]"
                  >
                    refresh
                  </span>
                </button>
              </div>
            </div>
            <div
              v-if="!isLoading"
              class="proj mt-2 w-full overflow-y-auto max-h-[calc(100vh-200px)] hide-scrollbar"
              @dragover.prevent
              @drop="
                (e) => {
                  const type = e.dataTransfer?.getData('type');
                  const id = e.dataTransfer?.getData('id');
                  if (!id) return;
                  if (type === 'project') moveProjectToFolder(id, null);
                  if (type === 'folder') moveFolder(id, null);
                }
              "
            >
              <div
                @click="setActiveFolder(null)"
                :class="currentFolderId === null ? 'bg-white/10' : ''"
                class="text-xs text-[#525252] px-2 py-1 rounded cursor-pointer hover:bg-white/5 flex items-center gap-1"
              >
                <span class="material-symbols-outlined !text-[16px]">home</span>
                Root
              </div>

              <FolderTree
                :parent-id="null"
                :selected-folder-id="currentFolderId"
                :child-folders="childFolders"
                :projects-in-folder="projectsInFolder"
                @open-project="openProject"
                @delete-project="deleteProject"
                @rename-folder="renameFolder"
                @delete-folder="openDeleteFolderModal"
                @create-subfolder="openCreateFolderModal"
                @select-folder="setActiveFolder"
                @drop-project="moveProjectToFolder"
                @drop-folder="moveFolder"
              />
            </div>
            <div
              v-else
              class="proj mt-2 w-full flex flex-col gap-1 justify-center items-center overflow-y-auto max-h-[calc(100vh-200px)] hide-scrollbar text-[#525252]"
            >
              Loading projects....
            </div>
          </div>
        </div>
      </div>
      <div
        class="bottom h-[75px] w-full flex flex-row justify-start items-center border-t border-[#323232] px-3"
      >
        <div
          class="profiles border border-[#323232] h-10 w-10 h-min-10 w-min-10 rounded-full text-xs transition cursor-pointer"
        >
          <img
            v-if="profile?.profile_photo_url"
            class="w-full h-full rounded-full object-cover cursor-pointer"
            :src="profile.profile_photo_url"
            alt=""
          />
          <div
            v-else
            class="w-full h-full rounded-full object-cover cursor-pointer flex justify-center items-center bg-[#323232]"
            alt=""
          >
            <span class="material-symbols-outlined !text-[30px] text-black">
              person
            </span>
          </div>
        </div>
        <div
          class="w-[80%] h-full flex flex-row justify-between items-center ms-2 relative"
        >
          <div class="namme flex flex-col justify-start items-start w-35">
            <span class="text-sm text-white truncate w-full">
              {{ profile?.name || "Guest" }}
            </span>
            <span class="text-[10px] text-gray-500 w-35 truncate">
              {{ "@" + profile?.username || "Not logged in" }}
            </span>
          </div>
          <div
            :class="isHidden ? 'opacity-0' : 'opacity-100'"
            class="h-25 w-40 bottom-full right-0 rounded-lg border border-[#323232] absolute translate-y-[10px] z-10 bg-[#1e1e1e] shadow-lg transition-all duration-500 ease"
          >
            <div
              v-if="session"
              class="button flex flex-col justify-start items-center h-full w-full"
            >
              <a
                @click="openMenu"
                class="w-full h-full hover:bg-[#121212] text-xs px-4 py-2 cursor-pointer text-start flex flex-row items-center gap-1"
              >
                <span class="material-symbols-outlined !text-[20px] me-1">
                  edit
                </span>
                Edit Profile
              </a>

              <div class="h-px w-[90%] bg-[#323232] mx-1"></div>
              <a
                @click="logOutAccount"
                class="w-full h-full hover:bg-[#121212] text-xs text-red-600 px-4 py-2 cursor-pointer text-start flex flex-row items-center gap-1"
              >
                <span class="material-symbols-outlined !text-[20px] me-1">
                  power_settings_new
                </span>
                Logout
              </a>
            </div>
            <div
              v-else
              class="button flex flex-col justify-start items-center h-full w-full"
            >
              <a
                href="/login"
                class="w-full h-full hover:bg-[#121212] text-xs px-4 py-2 cursor-pointer text-start flex flex-row items-center"
              >
                <span class="material-symbols-outlined !text-[20px] me-2">
                  login
                </span>
                Login
              </a>
              <div class="h-px w-[90%] bg-gray-500 mx-1"></div>
              <a
                href="/register"
                class="w-full h-full hover:bg-[#121212] text-xs px-4 py-2 cursor-pointer text-start flex flex-row items-center"
              >
                <span class="material-symbols-outlined !text-[20px] me-2">
                  person_add
                </span>
                Register
              </a>
            </div>
          </div>
          <button
            @click="openSideMenu"
            class="opt h-3 w-3 flex justify-center items-center border border-[#323232] p-4 rounded-lg cursor-pointer group hover:border-[#ff4400] hover:bg--[#121212] transition-all duration-300 ease focus:border-[#ff4400]"
          >
            <span
              class="material-symbols-outlined rotate-90 group-hover:text-[#ff4400] group-focus:text-[#ff4400]"
            >
              more_horiz
            </span>
          </button>
        </div>
      </div>
    </aside>
    <div
      :class="isSideHidden ? 'w-full' : 'w-[80%]'"
      class="flex h-full flex-col text-white relative transition-all duration-500 ease"
    >
      <nav
        class="w-full h-16 px-4 flex items-center justify-between gap-5 shrink-0 bg-[#222222]"
      >
        <div class="flex items-center gap-2">
          <span class="text-xs text-[#727272]"> Project Name: </span>
          <input
            v-model="currentProjectName"
            class="w-44 rounded border border-[#323232] bg-[#121212] px-3 py-1.5 text-sm text-white outline-none focus:border-[#ff4400]"
            placeholder="Project name"
          />
        </div>
        <select
          v-model="selectEditor"
          class="rounded border hover:border-[#ff4400] transition-all duration-300 ease border-[#323232] bg-[#121212] px-3 py-2 text-sm text-white outline-none focus:outline-none focus:ring-0 cursor-pointer"
        >
          <option value="code">Code</option>
          <option value="blocks">Blocks</option>
        </select>
        <div
          class="btns flex flex-row justify-start items-center bg-[#121212] rounded-lg w-fit px-2 border border-[#323232]"
        >
          <div class="flex items-center gap-2 pe-1 py-1">
            <span class="text-xs text-[#5e5e5e]"> Board: </span>

            <div
              class="selectboard flex flex-row justify-start items-center border border-[#323232] rounded-lg w-fit hover:border-[#ff4400] transition-all duration-300 ease"
            >
              <select
                v-model="manualFqbn"
                class="bg-[#121212] px-2 py-2 me-2 text-xs text-white outline-none cursor-pointer w-40 rounded-lg"
              >
                <option value="">
                  Auto ({{ selectedBoard?.board ?? "unknown" }})
                </option>
                <option
                  v-for="b in BOARD_OPTIONS"
                  :key="b.fqbn"
                  :value="b.fqbn"
                >
                  {{ b.label }}
                </option>
              </select>

              <div class="h-5 w-px bg-[#5e5e5e] mx-1"></div>

              <button
                @click="addBoard"
                :disabled="loadingBoards"
                class="bg-[#121212] py-2 text-xs group disabled:opacity-50 flex justify-center items-center cursor-pointer w-10 pe-1"
              >
                <span
                  :class="loadingBoards ? 'animate-spin' : ''"
                  class="material-symbols-outlined text-[#ff4400] group-hover:text-yellow-500 !text-[20px]"
                >
                  refresh
                </span>
              </button>
            </div>
          </div>

          <div class="h-5 w-px bg-gray-700 mx-1"></div>
          <button
            v-if="isSaving"
            class="rounded-full px-1 text-[#5e5e5e] text-sm font-medium disabled:cursor-not-allowed hover:text-[#ff4400] cursor-pointer transition-all duration-300 ease flex justify-center items-center"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 50 50"
              class="animate-spin"
            >
              <circle
                cx="25"
                cy="25"
                :r="radius"
                fill="none"
                :stroke="colorr"
                :stroke-width="7"
                stroke-linecap="round"
                :stroke-dasharray="circumference"
                :stroke-dashoffset="circumference * 0.75"
              />
            </svg>
          </button>
          <button
            v-else
            @click="saveProject"
            :disabled="compiling || uploading"
            title="Save Project"
            class="rounded-full text-[#5e5e5e] text-sm font-medium disabled:cursor-not-allowed hover:text-[#ff4400] cursor-pointer transition-all duration-300 ease flex justify-center items-center"
          >
            <span class="material-symbols-outlined !text-[25px]"> save </span>
          </button>
          <button
            @click="saveProjectToPC"
            :disabled="compiling || uploading"
            title="Download Project"
            class="rounded-full text-[#5e5e5e] text-sm font-medium disabled:cursor-not-allowed hover:text-[#ff4400] cursor-pointer transition-all duration-300 ease flex justify-center items-center"
          >
            <span class="material-symbols-outlined !text-[25px]">
              download
            </span>
          </button>
          <button
            @click="compileCode"
            :disabled="uploading || compiling"
            title="Compile"
            class="rounded-full text-[#5e5e5e] text-sm font-medium disabled:cursor-not-allowed hover:text-[#ff4400] cursor-pointer transition-all duration-300 ease flex justify-center items-center"
          >
            <span class="material-symbols-outlined !text-[25px]"> build </span>
          </button>
          <button
            @click="uploadCode"
            :disabled="compiling || uploading || !selectedBoard"
            title="Upload"
            class="rounded-full text-[#5e5e5e] text-sm font-medium disabled:cursor-not-allowed hover:text-[#ff4400] cursor-pointer transition-all duration-300 ease flex justify-center items-center"
          >
            <span class="material-symbols-outlined !text-[25px]"> start </span>
          </button>
        </div>
      </nav>

      <main class="flex flex-col overflow-hidden w-full flex-1 min-h-0">
        <section
          v-show="selectEditor === 'code'"
          class="w-full flex-1 min-h-0 flex flex-col"
        >
          <div
            class="flex flex-row border-b border-[#323232] bg-[#1a1a1a] shrink-0 overflow-x-auto"
          >
            <div
              v-for="(file, i) in openFiles"
              :key="file.id"
              @click="activeFileIndex = i"
              @dblclick="startRenameFile(i)"
              :class="
                i === activeFileIndex
                  ? 'text-[#ff4400] bg-[#222222]'
                  : 'text-[#727272] hover:text-white'
              "
              class="px-3 py-2 text-xs flex items-center gap-2 border-r border-[#323232] shrink-0 cursor-pointer select-none"
            >
              <input
                v-if="renamingFileIndex === i"
                ref="renameFileInputRef"
                v-model="renameFileDraft"
                @click.stop
                @blur="confirmRenameFile(i)"
                @keyup.enter="confirmRenameFile(i)"
                @keyup.escape="renamingFileIndex = null"
                class="bg-[#121212] text-xs outline-none w-24 px-1 rounded"
              />
              <span v-else>{{ file.filename }}</span>

              <span
                @click.stop="closeFile(i)"
                class="material-symbols-outlined !text-[14px] hover:text-red-400"
              >
                close
              </span>
            </div>

            <button
              @click="openCreatefile"
              class="px-3 py-2 text-xs text-[#ff4400] hover:bg-[#222222] shrink-0 cursor-pointer"
            >
              +
            </button>
          </div>

          <div class="flex-1 min-h-0 relative">
            <div v-if="isResizing" class="absolute inset-0 z-20"></div>
            <Editor
              :files="openFiles"
              :active-file-id="activeFile?.id ?? null"
              :fqbn="activeFqbn ?? 'arduino:avr:uno'"
              @update-content="handleEditorUpdate"
            />
          </div>
        </section>

        <section
          v-show="selectEditor === 'blocks'"
          class="w-full flex-1 min-h-0"
        >
          <BlockEditor ref="blockEditorRef" />
        </section>
      </main>
      <div
        v-show="selectOutput === 'build'"
        ref="box"
        class="relative border-t border-[#525252] bg-[#222222] p-4 overflow-hidden"
        :style="{ height: height + 'px' }"
      >
        <div
          class="absolute top-0 left-0 right-0 h-1.5 cursor-ns-resize hover:bg-gray-700"
          @mousedown="startResize"
        ></div>

        <div class="h-full flex flex-col">
          <div
            class="flex flex-row w-full justify-between items-center h-7 mb-2 shrink-0"
          >
            <select
              v-model="selectOutput"
              class="rounded border border-[#323232] bg-[#121212] px-3 py-2 text-sm text-white outline-none focus:outline-none focus:ring-0 cursor-pointer"
            >
              <option value="build">Build Output</option>
              <option value="monitor">Serial Monitor</option>
            </select>

            <button
              v-if="compileResult?.success"
              @click="downloadFirmware"
              class="rounded bg-green-600 px-4 py-2 text-xs font-medium hover:bg-green-700 cursor-pointer"
            >
              Download Firmware
            </button>
          </div>

          <div
            ref="buildOutputRef"
            class="font-mono text-sm whitespace-pre-wrap overflow-y-auto p-3 flex-1 min-h-0 custom-scrollbar"
          >
            <div v-if="compileResult">
              <div v-if="compileResult.compile_output" class="mb-4">
                <div class="font-bold mb-2">=== COMPILE ===</div>

                {{ compileResult.compile_output }}
              </div>

              <div v-if="compileResult.upload_output">
                <div class="font-bold mb-2">=== UPLOAD ===</div>

                {{ compileResult.upload_output }}
              </div>

              <div
                v-if="uploading && !compiling"
                class="flex flex-row justify-start items-center gap-2 mt-3"
              >
                <svg
                  :width="size"
                  :height="size"
                  viewBox="0 0 50 50"
                  class="animate-spin"
                >
                  <circle
                    cx="25"
                    cy="25"
                    :r="radius"
                    fill="none"
                    :stroke="color"
                    :stroke-width="strokeWidth"
                    stroke-linecap="round"
                    :stroke-dasharray="circumference"
                    :stroke-dashoffset="circumference * 0.75"
                  />
                </svg>
                <div class="">Uploading...</div>
              </div>

              <div
                v-if="compiling"
                class="flex flex-row justify-start items-center gap-2 mt-3"
              >
                <svg
                  :width="size"
                  :height="size"
                  viewBox="0 0 50 50"
                  class="animate-spin"
                >
                  <circle
                    cx="25"
                    cy="25"
                    :r="radius"
                    fill="none"
                    :stroke="color"
                    :stroke-width="strokeWidth"
                    stroke-linecap="round"
                    :stroke-dasharray="circumference"
                    :stroke-dashoffset="circumference * 0.75"
                  />
                </svg>
                <div class="">Compiling...</div>
              </div>

              <div
                v-if="
                  compileResult.success && !uploading && compileResult.message
                "
                class="mt-3 text-green-400"
              >
                ✓ {{ compileResult.message }}
              </div>

              <div
                v-if="compileResult.error"
                class="mt-3 text-red-400 whitespace-pre-wrap"
              >
                ✕ {{ compileResult.error }}
              </div>
            </div>

            <div v-else class="text-[#727272]">No build output yet.</div>
          </div>
        </div>
      </div>
      <div
        v-show="selectOutput === 'monitor'"
        class="relative border-t border-[#525252] bg-[#222222] p-4"
      >
        <div class="flex items-center justify-between mb-2">
          <select
            v-model="selectOutput"
            class="rounded border border-[#323232] bg-[#121212] px-3 py-2 text-sm text-white outline-none focus:outline-none focus:ring-0 cursor-pointer"
          >
            <option value="build">Build Output</option>
            <option value="monitor">Serial Monitor</option>
          </select>

          <div class="flex items-center gap-2">
            <select
              v-model="selectedBoard"
              class="rounded border border-gray-600 bg-[#2a2a2a] px-2 py-1 text-xs"
            >
              <option v-for="board in boards" :key="board.port" :value="board">
                {{ board.port }}
              </option>
            </select>

            <select
              v-model="baudRate"
              class="rounded border border-gray-600 bg-[#2a2a2a] px-2 py-1 text-xs"
            >
              <option :value="9600">9600</option>

              <option :value="19200">19200</option>

              <option :value="38400">38400</option>

              <option :value="57600">57600</option>

              <option :value="115200">115200</option>
            </select>

            <button
              v-if="!serialConnected"
              @click="openSerial"
              class="rounded bg-green-600 px-3 py-1 text-xs hover:bg-green-700"
            >
              Open
            </button>

            <button
              v-else
              @click="closeSerial"
              class="rounded bg-red-600 px-3 py-1 text-xs hover:bg-red-700"
            >
              Close
            </button>

            <button
              @click="clearSerial"
              class="rounded bg-gray-600 px-3 py-1 text-xs hover:bg-gray-700"
            >
              Clear
            </button>
          </div>
        </div>

        <div
          ref="serialOutputRef"
          class="h-64 overflow-y-auto rounded border border-gray-700 bg-black p-3 font-mono text-sm whitespace-pre-wrap custom-scrollbar"
        >
          <span v-if="!serialOutput" class="text-gray-600">
            Serial monitor not connected.
          </span>

          <span v-else>
            {{ serialOutput }}
          </span>
        </div>
        <div class="flex gap-2 mt-2">
          <input
            v-model="serialInput"
            @keyup.enter="sendSerial"
            :disabled="!serialConnected"
            placeholder="Send message..."
            class="flex-1 rounded border border-gray-700 bg-[#2a2a2a] px-3 py-2 text-sm outline-none"
          />

          <button
            @click="sendSerial"
            :disabled="!serialConnected || !serialInput"
            class="rounded bg-[#ff4400] px-4 py-2 text-sm hover:bg-[#ff4400] disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

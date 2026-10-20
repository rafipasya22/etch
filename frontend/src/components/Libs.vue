<script setup lang="ts">
import { ref, onMounted, computed } from "vue";

const emit = defineEmits<{
  libraryChanged: [];
}>();

interface LibrarySearchResult {
  name: string;
  author: string;
  sentence: string;
  paragraph: string;
  latest_version: string;
  versions: string[];
}

interface InstalledLibrary {
  name: string;
  version: string;
  author: string;
}

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const searchQuery = ref("");
const searchedQuery = ref("");
const searchResults = ref<LibrarySearchResult[]>([]);
const installedLibraries = ref<InstalledLibrary[]>([]);

const isSearching = ref(false);
const isLoadingInstalled = ref(false);
const installingName = ref<string | null>(null);
const uninstallingName = ref<string | null>(null);

const errorMessage = ref<string | null>(null);

const installedNames = computed(
  () => new Set(installedLibraries.value.map((l) => l.name)),
);

async function runSearch() {
  errorMessage.value = null;

  const q = searchQuery.value.trim();
  if (!q) {
    searchResults.value = [];
    searchedQuery.value = "";
    return;
  }
  if (q.length < 2 || isSearching.value) return;

  searchedQuery.value = q;
  isSearching.value = true;

  try {
    const res = await fetch(
      `${API_BASE}/libraries/search?query=${encodeURIComponent(q)}`,
    );

    if (!res.ok) throw new Error("Search failed");

    const data = await res.json();
    searchResults.value = data.libraries ?? [];
  } catch (e) {
    errorMessage.value = "Gagal mencari library.";
    searchResults.value = [];
  } finally {
    isSearching.value = false;
  }
}

async function fetchInstalled() {
  isLoadingInstalled.value = true;
  errorMessage.value = null;

  try {
    const res = await fetch(`${API_BASE}/libraries/installed`);

    if (!res.ok) throw new Error("Failed to load installed libraries");

    const data = await res.json();
    installedLibraries.value = data.libraries ?? [];
  } catch (e) {
    errorMessage.value = "Gagal memuat daftar library terpasang.";
  } finally {
    isLoadingInstalled.value = false;
  }
}

async function installLibrary(name: string, version?: string) {
  installingName.value = name;
  errorMessage.value = null;

  try {
    const res = await fetch(`${API_BASE}/libraries/install`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, version }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data?.detail?.message ?? "Install failed");
    }

    await fetchInstalled();
    emit("libraryChanged");
  } catch (e: any) {
    errorMessage.value = e.message ?? `Gagal instal ${name}.`;
  } finally {
    installingName.value = null;
  }
}

async function uninstallLibrary(name: string) {
  uninstallingName.value = name;
  errorMessage.value = null;

  try {
    const res = await fetch(`${API_BASE}/libraries/uninstall`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data?.detail?.message ?? "Uninstall failed");
    }

    await fetchInstalled();
    emit("libraryChanged");
  } catch (e: any) {
    errorMessage.value = e.message ?? `Gagal hapus ${name}.`;
  } finally {
    uninstallingName.value = null;
  }
}

function isInstalled(name: string) {
  return installedNames.value.has(name);
}

onMounted(() => {
  fetchInstalled();
});

defineExpose({
  fetchInstalled,
});
</script>

<template>
  <div
    class="flex flex-row justify-between items-center max-h-[10%] h-[10%] w-full px-3 py-4"
  >
    <h1 class="text-base font-semibold whitespace-nowrap">Install Library</h1>
    <div class="flex flex-row justify-start items-center gap-2 h-full">
      <span class="text-xs text-[#727272]"> Enter Library Name: </span>
      <input
        v-model="searchQuery"
        @keyup.enter="runSearch"
        type="text"
        placeholder="Find libraries, then press Enter (eg. Servo, DHT)"
        class="w-80 h-7 px-3 py-2 rounded bg-zinc-800 border border-zinc-700 outline-none focus:border-zinc-500 placeholder-zinc-500 text-xs"
      />
    </div>

    <div
      v-if="errorMessage"
      class="px-3 py-2 text-red-400 bg-red-950/40 border-b border-red-900"
    >
      {{ errorMessage }}
    </div>
  </div>

  <div
    class="flex flex-row justify-start items-start h-[90%] max-h-[90%] w-full"
  >
    <!-- Installed libraries -->
    <div class="w-[30%] flex flex-col items-stretch h-full gap-0">
      <div class="flex-none h-8">
        <div
          class="px-3 py-2 text-xs tracking-wide text-zinc-500 sticky top-0 bg-zinc-900 flex flex-row items-center justify-between h-full w-full"
        >
          <span>Installed Libraries</span>
          <button
            @click="fetchInstalled"
            class="text-zinc-500 hover:text-[#ff4400] cursor-pointer flex justify-center items-center"
            title="Refresh"
          >
            <span
              :class="isLoadingInstalled ? 'animate-spin text-[#ff4400]' : ''"
              class="material-symbols-outlined !text-[20px]"
            >
              refresh
            </span>
          </button>
        </div>
      </div>

      <div
        class="flex-1 min-h-0 overflow-y-auto custom-scrollbar w-full border-r border-[#323232]"
      >
        <div
          v-if="isLoadingInstalled"
          class="px-3 py-2 text-zinc-500 text-xs h-full w-full flex justify-center items-center"
        >
          Loading....
        </div>

        <div
          v-else-if="installedLibraries.length === 0"
          class="px-3 py-4 text-zinc-500"
        >
          No Library Installed.
        </div>

        <template v-else>
          <div
            v-for="lib in installedLibraries"
            :key="lib.name"
            class="px-3 py-3 border-b border-zinc-800 hover:bg-zinc-800/50 flex items-center justify-between gap-2"
          >
            <div class="min-w-0">
              <div class="text-sm font-semibold text-zinc-100 truncate">
                {{ lib.name }}
              </div>
              <div class="text-xs text-zinc-500 truncate">
                {{ lib.author }} · v{{ lib.version }}
              </div>
            </div>

            <button
              @click="uninstallLibrary(lib.name)"
              :disabled="uninstallingName === lib.name"
              class="shrink-0 px-3 py-1.5 cursor-pointer rounded bg-zinc-800 hover:bg-red-900/50 disabled:opacity-50 text-xs text-zinc-400 hover:text-red-400 border border-zinc-700"
            >
              {{ uninstallingName === lib.name ? "Deleting..." : "Delete" }}
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- Search results -->
    <div class="w-[70%] h-full flex flex-col items-stretch gap-0">
      <div class="flex-none h-8">
        <div
          class="px-3 py-2 text-xs tracking-wide text-zinc-500 sticky top-0 bg-zinc-900 h-8 max-h-8 w-full"
        >
          Search Result
        </div>
      </div>

      <div class="flex-1 min-h-0 overflow-y-auto custom-scrollbar w-full">
        <div
          v-if="isSearching"
          class="px-3 py-4 text-zinc-500 w-full h-full flex justify-center items-center"
        >
          <div class="flex items-center gap-1.5 justify-center py-4">
            <span
              class="w-2 h-2 rounded-full bg-[#ff4400] animate-bounce [animation-delay:-0.3s]"
            ></span>
            <span
              class="w-2 h-2 rounded-full bg-[#ff4400] animate-bounce [animation-delay:-0.15s]"
            ></span>
            <span
              class="w-2 h-2 rounded-full bg-[#ff4400] animate-bounce"
            ></span>
          </div>
        </div>

        <div
          v-else-if="searchResults.length === 0 && !searchedQuery"
          class="px-3 py-4 text-zinc-500 w-full h-full flex justify-center items-center"
        >
          Type a library name and press Enter to search
        </div>

        <div
          v-else-if="searchResults.length === 0"
          class="px-3 py-4 text-zinc-500 w-full h-full flex justify-center items-center"
        >
          No results for "{{ searchedQuery }}".
        </div>

        <template v-else>
          <div
            v-for="lib in searchResults"
            :key="lib.name"
            class="px-3 py-3 border-b border-zinc-800 hover:bg-zinc-800/50"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="text-sm font-semibold text-zinc-100 truncate">
                  {{ lib.name }}
                </div>
                <div class="text-xs text-zinc-500 truncate">
                  {{ lib.author }} · v{{ lib.latest_version }}
                </div>
                <div class="text-xs text-zinc-400 mt-1 line-clamp-2">
                  {{ lib.sentence }}
                </div>
              </div>

              <button
                v-if="!isInstalled(lib.name)"
                @click="installLibrary(lib.name, lib.latest_version)"
                :disabled="installingName === lib.name"
                class="shrink-0 px-3 py-1.5 cursor-pointer rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed text-xs whitespace-nowrap"
              >
                {{ installingName === lib.name ? "Installing..." : "Install" }}
              </button>

              <span
                v-else
                class="shrink-0 px-3 py-1.5 rounded bg-zinc-800 text-zinc-400 text-xs whitespace-nowrap border border-zinc-700"
              >
                Installed
              </span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
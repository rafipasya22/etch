# Etch

Write, compile, and flash Arduino firmware from your browser. No local toolchain, no drivers to install beyond what your browser already provides.

Etch compiles sketches on a backend (arduino-cli) and flashes the resulting firmware to your board over Web Serial.

> **Live demo:** `https://etch-murex.vercel.app/`
> The compile backend may be offline when the demo is not being actively shown. If compile requests fail, that is why.

## Features

- Code editor in the browser, with clangd-powered autocomplete (optional, see [Limitations](#limitations))
- Block editor for visual programming
- Compile in the cloud, download or flash the firmware directly
- Flash to a connected board over Web Serial
- Search and install Arduino libraries from the UI
- Account login with Supabase Auth

## Supported boards

| Board | FQBN |
|---|---|
| ESP32 Dev Module | `esp32:esp32:esp32` |
| ESP32-S3 | `esp32:esp32:esp32s3` |
| ESP32-C3 | `esp32:esp32:esp32c3` |
| Arduino Uno | `arduino:avr:uno` |
| Arduino Nano | `arduino:avr:nano` |
| Arduino Mega | `arduino:avr:mega` |

Adding a board means adding its FQBN to `ALLOWED_FQBN` in the backend and making sure its core is installed in the Docker image.

## How it works

```
Browser (Vue 3, Vercel)
   │   HTTPS: /compile, /libraries/*, /build/{id}/firmware
   │   WSS:   /lsp/clangd
   ▼
FastAPI backend (Docker)
   ├── arduino-cli  → compiles sketches, installs libraries
   └── clangd       → language server for editor autocomplete

Browser ──Web Serial──▶ your microcontroller
```

1. You write a sketch in the editor and pick a board.
2. The frontend sends the files to `POST /compile`.
3. The backend compiles with arduino-cli and returns a build id.
4. The frontend downloads the firmware from `GET /build/{id}/firmware` and flashes it to your board over Web Serial.

## Tech stack

- **Frontend:** Vue 3, TypeScript, Vite, Monaco editor
- **Backend:** FastAPI, arduino-cli, clangd
- **Auth:** Supabase
- **Hosting:** Vercel (frontend), Docker (backend)

## Requirements

- Docker (for the backend)
- Node.js 20+ (for the frontend)
- A Chromium-based browser (Chrome or Edge). Web Serial is not available in Firefox or Safari.

## Getting started

### Run everything with Docker

```bash
docker compose up --build
```

The first build downloads the board cores and test-compiles every supported board, so it takes a while. To skip the board check while iterating:

```bash
docker build --build-arg VERIFY_BOARDS=false -t etch-api .
```

To pin the ESP32 core version (smaller image):

```bash
docker build --build-arg ESP32_CORE_VERSION=2.0.17 -t etch-api .
```

### Run the frontend locally

```bash
cd frontend
npm install
cp .env.example .env   # then fill in the values
npm run dev
```

The dev server runs at `http://localhost:5173`.

### Run the backend locally (without Docker)

You need `arduino-cli` and `clangd` installed, with the board cores installed:

```bash
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install arduino:avr esp32:esp32

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Configuration

### Frontend (`frontend/.env`)

| Variable | Description |
|---|---|
| `VITE_API_URL` | Backend base URL. Defaults to `http://localhost:8000`. |
| `VITE_SUPABASE_URL` | Your Supabase project URL. |
| `VITE_SUPABASE_ANON_KEY` | Your Supabase anon (public) key. |

Vite inlines these at build time, so changing them requires a rebuild or redeploy.

### Backend (environment variables)

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated list of frontend origins allowed by CORS. |
| `ENABLE_LSP` | `true` | Enables the `/lsp/clangd` websocket for autocomplete. |
| `ALLOW_LIB_UNINSTALL` | `false` | Allows the library uninstall endpoint. |
| `MAX_CONCURRENT_COMPILES` | `2` | Maximum compiles running at the same time. Extra requests get a 429. |
| `PORT` | `8000` | Port uvicorn listens on. |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check. |
| POST | `/compile` | Compile a sketch. Body: `board`, `files[]` (`filename`, `content`), optional `libraries[]`. Rate limited. |
| GET | `/build/{build_id}/firmware` | Download the compiled firmware. |
| GET | `/libraries/search?query=` | Search the Arduino library index. |
| GET | `/libraries/installed` | List installed libraries. |
| POST | `/libraries/install` | Install a library, optionally a specific version. |
| POST | `/libraries/uninstall` | Uninstall a library. Disabled unless `ALLOW_LIB_UNINSTALL=true`. |
| WS | `/lsp/clangd?fqbn=&filename=` | Language server bridge for autocomplete. |

## Limits and security

- Boards are restricted to an allowlist.
- Compile requests accept at most 20 files, 500 KB total, and 10 libraries.
- Only `.ino`, `.h`, `.hpp`, and `.cpp` files are accepted.
- Per-IP rate limits apply to compile, install, and search endpoints.
- Old build directories are cleaned up automatically after an hour.

If you expose the backend publicly, keep `MAX_CONCURRENT_COMPILES` low. Compiles use real CPU and memory.

## Limitations

- Web Serial works only in Chromium-based browsers.
- Editor autocomplete is configured with AVR compiler flags, so completions for ESP32-specific APIs are limited.
- Libraries are installed into the backend and shared between all users.
- Compile speed depends on the machine hosting the backend.

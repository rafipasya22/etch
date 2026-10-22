import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from compile import compile_arduino

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# CONFIG 
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
ENABLE_LSP = os.getenv("ENABLE_LSP", "true").lower() == "true"
ALLOW_LIB_UNINSTALL = os.getenv("ALLOW_LIB_UNINSTALL", "false").lower() == "true"
MAX_CONCURRENT_COMPILES = int(os.getenv("MAX_CONCURRENT_COMPILES", "2"))

ALLOWED_FQBN = {
    "esp32:esp32:esp32",
    "esp32:esp32:esp32s3",
    "esp32:esp32:esp32c3",
    "arduino:avr:uno",
    "arduino:avr:nano",
    "arduino:avr:mega",
}
ALLOWED_EXT = {".ino", ".h", ".hpp", ".cpp"}
MAX_FILES = 20
MAX_TOTAL_BYTES = 500_000
MAX_LIBS = 10
LIB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-+]{0,99}$")
LIB_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-+]{0,29}$")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLANGD_WORKSPACES = Path("clangd_workspaces")
CLANGD_WORKSPACES.mkdir(exist_ok=True)

BUILD_DIR = Path("builds")
BUILD_DIR.mkdir(exist_ok=True)

_board_flags_cache: dict[str, list[str]] = {}
compile_slots = threading.BoundedSemaphore(MAX_CONCURRENT_COMPILES)


# MODELS
class SketchFile(BaseModel):
    filename: str
    content: str


class CompileRequest(BaseModel):
    board: str
    files: list[SketchFile]
    libraries: list[str] = []


class LibraryInstallRequest(BaseModel):
    name: str
    version: str | None = None


class LibraryUninstallRequest(BaseModel):
    name: str


# HELPER
_hits: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_calls: int, per_seconds: int):

    def dep(request: Request):
        key = f"{request.url.path}:{client_ip(request)}"
        now = time.time()
        q = _hits[key]
        while q and q[0] < now - per_seconds:
            q.popleft()
        if len(q) >= max_calls:
            raise HTTPException(429, "Too many requests, slow down.")
        q.append(now)

    return dep


def run_cli(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def validate_files(files: list[SketchFile]):
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Send between 1 and {MAX_FILES} files.")
    if sum(len(f.content.encode("utf-8")) for f in files) > MAX_TOTAL_BYTES:
        raise HTTPException(400, "Project too large.")


def validate_lib_name(name: str):
    if not LIB_NAME_RE.match(name):
        raise HTTPException(400, f"Invalid library name: {name!r}")


def cleanup_old_builds(max_age_s: int = 3600):
    now = time.time()
    for d in BUILD_DIR.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > max_age_s:
            shutil.rmtree(d, ignore_errors=True)


def ensure_libraries_installed(libraries: list[str]):
    if len(libraries) > MAX_LIBS:
        raise HTTPException(400, f"Too many libraries (max {MAX_LIBS}).")
    for lib_name in libraries:
        validate_lib_name(lib_name)
        result = run_cli(["arduino-cli", "lib", "install", lib_name])
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "stage": "lib_install",
                    "message": f"Failed installing {lib_name}: {result.stderr or result.stdout}",
                },
            )


# ROUTE
@app.get("/")
def root():
    return {"message": "Etch"}


@app.get("/health")
def health():
    return {"ok": True}


# COMPILE + FIRMWARE
@app.post("/compile", dependencies=[Depends(rate_limit(10, 60))])
def compile_code(request: CompileRequest):
    if request.board not in ALLOWED_FQBN:
        raise HTTPException(400, "Unsupported board.")
    validate_files(request.files)

    if not compile_slots.acquire(blocking=False):
        raise HTTPException(429, "Server busy, try again in a minute.")

    try:
        cleanup_old_builds()
        ensure_libraries_installed(request.libraries)

        build_id = str(uuid.uuid4())
        project_path = BUILD_DIR / build_id
        project_path.mkdir(parents=True)

        main_ino_written = False

        for f in request.files:
            name = Path(f.filename).name
            if Path(name).suffix.lower() not in ALLOWED_EXT:
                shutil.rmtree(project_path, ignore_errors=True)
                raise HTTPException(400, f"Unsupported file: {name}")

            if name.lower().endswith(".ino") and not main_ino_written:
                target_name = f"{build_id}.ino"
                main_ino_written = True
            else:
                target_name = name

            (project_path / target_name).write_text(f.content, encoding="utf-8")

        if not main_ino_written:
            shutil.rmtree(project_path, ignore_errors=True)
            return {"success": False, "stderr": "No .ino file found in project."}

        result = compile_arduino(str(project_path), request.board)

        if not result["success"]:
            shutil.rmtree(project_path, ignore_errors=True)
            return result

        return {
            "success": True,
            "build_id": build_id,
            "firmware": result["firmware_file"].name,
            "compile_output": result["stdout"],
            "stderr": result["stderr"],
            "message": "Compile successful!",
        }
    finally:
        compile_slots.release()


@app.get("/build/{build_id}/firmware")
def download_firmware(build_id: str):
    try:
        uuid.UUID(build_id)
    except ValueError:
        raise HTTPException(400, "Invalid build id")

    build_path = BUILD_DIR / build_id / "build"
    files = list(build_path.glob("*")) if build_path.exists() else []

    fw = (
        next((f for f in files if f.name.endswith(".merged.bin")), None)
        or next((f for f in files if f.suffix == ".hex" and "with_bootloader" not in f.name), None)
        or next((f for f in files if f.suffix == ".bin"), None)
    )
    if not fw:
        raise HTTPException(404, "Firmware not found")

    return FileResponse(fw, filename=fw.name, media_type="application/octet-stream")


# LIBS
@app.get("/libraries/search", dependencies=[Depends(rate_limit(30, 60))])
def search_libraries(query: str = ""):
    query = query.strip()
    if query.startswith("-") or len(query) > 100:
        raise HTTPException(400, "Invalid query")

    result = run_cli(["arduino-cli", "lib", "search", query, "--format", "json"])
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr)

    data = json.loads(result.stdout)

    libraries = [
        {
            "name": lib.get("name"),
            "author": lib.get("latest", {}).get("author"),
            "sentence": lib.get("latest", {}).get("sentence"),
            "paragraph": lib.get("latest", {}).get("paragraph"),
            "latest_version": lib.get("latest", {}).get("version"),
            "versions": lib.get("available_versions", []),
        }
        for lib in data.get("libraries", [])
    ]
    return {"success": True, "libraries": libraries}


@app.get("/libraries/installed")
def list_installed_libraries():
    result = run_cli(["arduino-cli", "lib", "list", "--format", "json"])
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr)

    data = json.loads(result.stdout)

    libraries = [
        {
            "name": item.get("library", {}).get("name"),
            "version": item.get("library", {}).get("version"),
            "author": item.get("library", {}).get("author"),
        }
        for item in data.get("installed_libraries", [])
    ]
    return {"success": True, "libraries": libraries}


@app.post("/libraries/install", dependencies=[Depends(rate_limit(10, 60))])
def install_library(request: LibraryInstallRequest):
    validate_lib_name(request.name)
    if request.version is not None and not LIB_VERSION_RE.match(request.version):
        raise HTTPException(400, "Invalid version")

    target = f"{request.name}@{request.version}" if request.version else request.name
    result = run_cli(["arduino-cli", "lib", "install", target])

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={"stage": "lib_install", "message": result.stderr or result.stdout},
        )
    return {"success": True, "message": f"{target} installed", "output": result.stdout}


@app.post("/libraries/uninstall", dependencies=[Depends(rate_limit(10, 60))])
def uninstall_library(request: LibraryUninstallRequest):
    if not ALLOW_LIB_UNINSTALL:
        raise HTTPException(403, "Uninstall is disabled on this server.")
    validate_lib_name(request.name)

    result = run_cli(["arduino-cli", "lib", "uninstall", request.name])
    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={"stage": "lib_uninstall", "message": result.stderr or result.stdout},
        )
    return {"success": True, "message": f"{request.name} uninstalled"}


# CLANGD 
@app.websocket("/lsp/clangd")
async def clangd_bridge(
    websocket: WebSocket,
    fqbn: str = "arduino:avr:uno",
    filename: str = "sketch.ino",
):
    filename = Path(filename).name
    if (
        not ENABLE_LSP
        or fqbn not in ALLOWED_FQBN
        or Path(filename).suffix.lower() not in ALLOWED_EXT
    ):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    session_id = str(uuid.uuid4())
    workspace = CLANGD_WORKSPACES / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_abs = workspace.resolve()

    await asyncio.to_thread(write_compile_commands, workspace, fqbn, filename)
    (workspace / filename).write_text("", encoding="utf-8")

    await websocket.send_text(
        json.dumps(
            {
                "type": "workspace-info",
                "path": str(workspace_abs).replace("\\", "/"),
            }
        )
    )

    try:
        process = await asyncio.create_subprocess_exec(
            "clangd",
            "--background-index",
            f"--compile-commands-dir={workspace}",
            cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        shutil.rmtree(workspace, ignore_errors=True)
        await websocket.close(code=1011)
        return

    async def ws_to_clangd():
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    parsed = json.loads(msg)
                    method = parsed.get("method")
                    if method == "textDocument/didOpen":
                        text = parsed["params"]["textDocument"]["text"]
                        (workspace / filename).write_text(text, encoding="utf-8")
                    elif method == "textDocument/didChange":
                        text = parsed["params"]["contentChanges"][0]["text"]
                        (workspace / filename).write_text(text, encoding="utf-8")
                except (json.JSONDecodeError, KeyError):
                    pass

                process.stdin.write(msg.encode("utf-8"))
                await process.stdin.drain()
        except WebSocketDisconnect:
            pass

    async def clangd_to_ws():
        buffer = b""
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                msg, buffer, consumed = try_extract_message(buffer)
                if not consumed:
                    break
                try:
                    await websocket.send_text(msg.decode("utf-8"))
                except Exception:
                    return

    try:
        await asyncio.gather(ws_to_clangd(), clangd_to_ws())
    finally:
        process.kill()
        shutil.rmtree(workspace, ignore_errors=True)


def try_extract_message(buffer: bytes):
    header_end = buffer.find(b"\r\n\r\n")
    if header_end == -1:
        return None, buffer, False

    headers = buffer[:header_end].decode("ascii")
    length = None
    for line in headers.split("\r\n"):
        if line.lower().startswith("content-length"):
            length = int(line.split(":")[1].strip())

    if length is None:
        return None, buffer, False

    body_start = header_end + 4
    if len(buffer) < body_start + length:
        return None, buffer, False

    msg = buffer[body_start : body_start + length]
    rest = buffer[body_start + length :]
    return msg, rest, True


def get_arduino_include_flags(fqbn: str) -> list[str]:
    if fqbn in _board_flags_cache:
        return _board_flags_cache[fqbn]

    probe_dir = Path("clangd_probe") / fqbn.replace(":", "_")  
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_ino = probe_dir / f"{probe_dir.name}.ino"
    probe_ino.write_text("void setup() {}\nvoid loop() {}\n")

    result = run_cli(
        ["arduino-cli", "compile", "--fqbn", fqbn, "-v", "--clean", str(probe_dir)]
    )

    compile_line = None
    for line in result.stdout.splitlines():
        if "avr-g++" in line and " -c " in line and ".ino.cpp" in line and "-o" in line:
            compile_line = line
            break

    flags: list[str] = []
    if compile_line:
        flags += ["-I" + p for p in re.findall(r'"-I([^"]+)"', compile_line)]
        flags += re.findall(r'(?<!")(-I[^\s"]+)', compile_line)
        flags += re.findall(r"(-D\S+)", compile_line)

    shutil.rmtree(probe_dir, ignore_errors=True)
    _board_flags_cache[fqbn] = flags
    return flags


def find_avr_libc_include() -> Path | None:
    roots = [
        Path(os.getenv("ARDUINO_DATA_DIR", "")) if os.getenv("ARDUINO_DATA_DIR") else None,
        Path.home() / "AppData" / "Local" / "Arduino15",
        Path.home() / ".arduino15",
    ]
    for root in roots:
        if root is None:
            continue
        matches = sorted(root.glob("packages/arduino/tools/avr-gcc/*/avr/include"))
        if matches:
            return matches[-1]
    return None


def write_compile_commands(workspace: Path, fqbn: str, main_filename: str):
    flags = get_arduino_include_flags(fqbn)
    avr_libc_include = find_avr_libc_include()

    arguments = [
        "clang++",
        "--target=avr",
        "-mmcu=atmega328p",
        "-x", "c++", "-std=gnu++11", "-fpermissive", "-fno-exceptions",
    ]
    if avr_libc_include:
        arguments.append(f"-I{avr_libc_include}")
    arguments += [*flags, "-include", "Arduino.h", main_filename]

    entry = [
        {
            "directory": str(workspace.resolve()),
            "arguments": arguments,
            "file": main_filename,
        }
    ]
    (workspace / "compile_commands.json").write_text(json.dumps(entry))
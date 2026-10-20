from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from pathlib import Path
import uuid
import shutil
import subprocess
from fastapi.responses import FileResponse, StreamingResponse
from compile import compile_arduino
from fastapi.middleware.cors import CORSMiddleware
import json
import serial
import threading
import time
import sys
import asyncio
import shlex

import time
import re

_board_flags_cache: dict[str, list[str]] = {}

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CLANGD_WORKSPACES = Path("clangd_workspaces")
CLANGD_WORKSPACES.mkdir(exist_ok=True)

BUILD_DIR = Path("builds")
BUILD_DIR.mkdir(exist_ok=True)

PROJECTS_DIR = Path("projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


class ProjectRequest(BaseModel):
    name: str
    code: str


class ProjectRenameRequest(BaseModel):
    name: str


class ProjectUpdateRequest(BaseModel):
    name: str
    code: str


class SketchFile(BaseModel):
    filename: str
    content: str


class CompileRequest(BaseModel):
    board: str
    files: list[SketchFile]
    libraries: list[str] = []


class UploadRequest(BaseModel):
    files: list[SketchFile]
    fqbn: str
    port: str
    libraries: list[str] = []


class LibraryInstallRequest(BaseModel):
    name: str
    version: str | None = None


class LibraryUninstallRequest(BaseModel):
    name: str


ss = {
    ("0x10C4", "0xEA60"): {
        "board": "ESP32 Dev Module",
        "fqbn": "esp32:esp32:esp32",
    },
    ("0x1A86", "0x7523"): {
        "board": "Arduino Uno",
        "fqbn": "arduino:avr:uno",
    },
}


def run_cli(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess with safe UTF-8 decoding.

    Windows defaults subprocess text decoding to the system codepage
    (cp1252), which chokes on UTF-8 bytes arduino-cli can emit (special
    chars in library descriptions, etc). Force UTF-8 and replace any
    undecodable byte instead of crashing the whole request.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@app.get("/")
def root():
    return {
        "message": "Arduino Web Compiler API"
    }


def cleanup_old_builds(max_age_s: int = 3600):
    now = time.time()
    for d in BUILD_DIR.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > max_age_s:
            shutil.rmtree(d, ignore_errors=True)

@app.post("/compile")
def compile_code(request: CompileRequest):
    cleanup_old_builds()
    ensure_libraries_installed(request.libraries)

    build_id = str(uuid.uuid4())
    project_path = Path("builds") / build_id
    project_path.mkdir(parents=True)

    main_ino_written = False

    ALLOWED_EXT = {".ino", ".h", ".hpp", ".cpp"}

    for f in request.files:
        name = Path(f.filename).name          
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            raise HTTPException(400, f"Unsupported file: {name}")

        if name.lower().endswith(".ino") and not main_ino_written:
            target_name = f"{build_id}.ino"
            main_ino_written = True
        else:
            target_name = name

        (project_path / target_name).write_text(f.content, encoding="utf-8")

    if not main_ino_written:
        shutil.rmtree(project_path)
        return {
            "success": False,
            "stderr": "No .ino file found in project."
        }

    result = compile_arduino(
        str(project_path),
        request.board
    )

    if not result["success"]:
        shutil.rmtree(project_path)
        return result

    firmware_file = result["firmware_file"]

    return {
        "success": True,
        "build_id": build_id,
        "firmware": firmware_file.name,
        "compile_output": result["stdout"],
        "stderr": result["stderr"],
        "message": "Compile successful!"
    }




@app.get("/build/{build_id}/firmware")
def download_firmware(build_id: str):
    uuid.UUID(build_id)  
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


@app.post("/upload")
def upload_code(request: UploadRequest):
    ensure_libraries_installed(request.libraries)

    build_id = str(uuid.uuid4())
    sketch_dir = BUILD_DIR / build_id
    sketch_dir.mkdir(parents=True, exist_ok=True)

    try:
        main_ino_written = False

        for f in request.files:
            if f.filename.lower().endswith(".ino"):
                target_name = f"{build_id}.ino" if not main_ino_written else f.filename
                main_ino_written = True
            else:
                target_name = f.filename

            file_path = sketch_dir / target_name
            file_path.write_text(f.content, encoding="utf-8")

        if not main_ino_written:
            raise HTTPException(
                status_code=400,
                detail={"message": "No .ino file found in project."}
            )

        compile_cmd = [
            "arduino-cli",
            "compile",
            "--fqbn",
            request.fqbn,
            str(sketch_dir)
        ]

        compile_result = run_cli(compile_cmd)

        if compile_result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "stage": "compile",
                    "message": (
                        compile_result.stderr
                        or compile_result.stdout
                    )
                }
            )

        upload_cmd = [
            "arduino-cli",
            "upload",
            "-v",
            "-p",
            request.port,
            "--fqbn",
            request.fqbn,
            str(sketch_dir)
        ]

        def generate():
            yield f"data: {json.dumps({
                'type': 'compile',
                'message': compile_result.stdout
            })}\n\n"

            yield f"data: {json.dumps({
                'type': 'status',
                'message': 'Starting upload...'
            })}\n\n"

            process = subprocess.Popen(
                upload_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )

            if process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    if not line:
                        continue

                    yield f"data: {json.dumps({
                        'type': 'log',
                        'message': line
                    })}\n\n"

            process.wait()

            if process.returncode == 0:
                yield f"data: {json.dumps({
                    'type': 'success',
                    'message': 'Upload successful!',
                    'build_id': build_id
                })}\n\n"
            else:
                yield f"data: {json.dumps({
                    'type': 'error',
                    'message': 'Upload failed.',
                    'build_id': build_id
                })}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/boards")
def get_boards():
    try:
        result = run_cli(
            ["arduino-cli", "board", "list", "--format", "json"]
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=result.stderr
            )

        data = json.loads(result.stdout)

        boards = []

        for detected in data.get("detected_ports", []):

            port = detected.get("port", {})
            matches = detected.get("matching_boards", [])

            address = port.get("address")
            vid = port.get("properties", {}).get("vid")
            pid = port.get("properties", {}).get("pid")

            base_info = {
                "port": address,
                "label": port.get("label"),
                "protocol": port.get("protocol"),
                "protocol_label": port.get("protocol_label"),
                "vid": vid,
                "pid": pid,
                "serial_number": port.get("properties", {}).get("serialNumber"),
            }

            if matches:

                for match in matches:
                    boards.append({
                        **base_info,
                        "board": match.get("name"),
                        "fqbn": match.get("fqbn"),
                        "identified": True,
                    })

            else:

                known_board = ss.get(
                    (
                        vid if vid else None,
                        pid if pid else None
                    )
                )

                if known_board:
                    boards.append({
                        **base_info,
                        "board": known_board["board"],
                        "fqbn": known_board["fqbn"],
                        "identified": True,
                    })

                else:
                    boards.append({
                        **base_info,
                        "board": None,
                        "fqbn": None,
                        "identified": False,
                    })

        return {
                    "success": True,
                    "boards": boards
                }

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse Arduino CLI response"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.websocket("/serial")
async def serial_monitor(websocket: WebSocket):

    await websocket.accept()

    ser = None

    try:

        config = await websocket.receive_json()

        port = config.get("port")
        baudrate = config.get("baudrate", 115200)

        if not port:
            await websocket.send_json({
                "type": "error",
                "message": "No serial port specified"
            })
            return

        try:

            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=0.05
            )

        except Exception as e:

            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })

            return

        await websocket.send_json({
            "type": "connected",
            "port": port,
            "baudrate": baudrate
        })

        async def read_serial():

            while True:

                if ser is None or not ser.is_open:
                    break

                try:

                    if ser.in_waiting > 0:

                        data = ser.read(
                            ser.in_waiting
                        )

                        text = data.decode(
                            "utf-8",
                            errors="replace"
                        )

                        await websocket.send_json({
                            "type": "data",
                            "data": text
                        })

                except Exception as e:

                    await websocket.send_json({
                        "type": "error",
                        "message": str(e)
                    })

                    break

                await asyncio.sleep(0.01)

        async def receive_messages():

            while True:

                message = await websocket.receive_json()

                if message.get("type") == "write":

                    text = message.get(
                        "data",
                        ""
                    )

                    if ser and ser.is_open:

                        ser.write(
                            text.encode("utf-8")
                        )

        await asyncio.gather(
            read_serial(),
            receive_messages()
        )

    except WebSocketDisconnect:
        print("Serial monitor disconnected")

    except Exception as e:
        print("Serial monitor error:", e)

    finally:

        if ser and ser.is_open:
            ser.close()

        print("Serial port closed")


@app.post("/projects")
def save_project(request: ProjectRequest):

    project_id = str(uuid.uuid4())

    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    ino_file = project_dir / f"{request.name}.ino"

    ino_file.write_text(
        request.code,
        encoding="utf-8"
    )

    return {
        "success": True,
        "project": {
            "id": project_id,
            "name": request.name
        }
    }


@app.get("/projects")
def get_projects():

    projects = []

    for project_dir in PROJECTS_DIR.iterdir():

        if not project_dir.is_dir():
            continue

        ino_files = list(
            project_dir.glob("*.ino")
        )

        if not ino_files:
            continue

        ino_file = ino_files[0]

        projects.append({
            "id": project_dir.name,
            "name": ino_file.stem,
            "filename": ino_file.name
        })

    return {
        "success": True,
        "projects": projects
    }


@app.get("/projects/{project_id}")
def load_project(project_id: str):

    project_dir = PROJECTS_DIR / project_id

    if not project_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    ino_files = list(
        project_dir.glob("*.ino")
    )

    if not ino_files:
        raise HTTPException(
            status_code=404,
            detail="Project source not found"
        )

    ino_file = ino_files[0]

    return {
        "success": True,
        "project": {
            "id": project_id,
            "name": ino_file.stem,
            "filename": ino_file.name,
            "code": ino_file.read_text(
                encoding="utf-8"
            )
        }
    }


@app.put("/projects/{project_id}")
def update_project(
    project_id: str,
    request: ProjectUpdateRequest
):
    project_dir = PROJECTS_DIR / project_id

    if not project_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    ino_files = list(project_dir.glob("*.ino"))

    if not ino_files:
        raise HTTPException(
            status_code=404,
            detail="Project source not found"
        )

    old_file = ino_files[0]

    safe_name = Path(request.name).stem

    new_file = project_dir / f"{safe_name}.ino"

    if old_file != new_file:
        old_file.rename(new_file)

    new_file.write_text(
        request.code,
        encoding="utf-8"
    )

    return {
        "success": True,
        "project": {
            "id": project_id,
            "name": safe_name,
            "filename": new_file.name
        }
    }


@app.get("/libraries/search")
def search_libraries(query: str = ""):
    cmd = ["arduino-cli", "lib", "search", query, "--format", "json"]

    result = run_cli(cmd)

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


@app.post("/libraries/install")
def install_library(request: LibraryInstallRequest):
    target = (
        f"{request.name}@{request.version}"
        if request.version
        else request.name
    )

    result = run_cli(["arduino-cli", "lib", "install", target])

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={"stage": "lib_install", "message": result.stderr or result.stdout},
        )

    return {"success": True, "message": f"{target} installed", "output": result.stdout}


@app.post("/libraries/uninstall")
def uninstall_library(request: LibraryUninstallRequest):
    result = run_cli(["arduino-cli", "lib", "uninstall", request.name])

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={"stage": "lib_uninstall", "message": result.stderr or result.stdout},
        )

    return {"success": True, "message": f"{request.name} uninstalled"}


def ensure_libraries_installed(libraries: list[str]):
    for lib_name in libraries:
        result = run_cli(["arduino-cli", "lib", "install", lib_name])
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "stage": "lib_install",
                    "message": f"Failed installing {lib_name}: {result.stderr or result.stdout}",
                },
            )

@app.websocket("/lsp/clangd")
async def clangd_bridge(websocket: WebSocket, fqbn: str = "arduino:avr:uno", filename: str = "sketch.ino"):
    await websocket.accept()

    session_id = str(uuid.uuid4())
    workspace = CLANGD_WORKSPACES / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_abs = workspace.resolve()

    write_compile_commands(workspace, fqbn, filename)
    (workspace / filename).write_text("", encoding="utf-8")

    await websocket.send_text(json.dumps({
        "type": "workspace-info",
        "path": str(workspace_abs).replace("\\", "/"),
    }))


    process = await asyncio.create_subprocess_exec(
        "clangd", "--background-index",
        f"--compile-commands-dir={workspace}",
        cwd=str(workspace),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

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
            print("RAW FROM CLANGD:", chunk)
            if not chunk:
                print("CLANGD STDOUT CLOSED")
                break
            buffer += chunk
            while True:
                msg, buffer, consumed = try_extract_message(buffer)
                if not consumed:
                    break
                try:
                    await websocket.send_text(msg.decode("utf-8"))
                except Exception as e:
                    print("SEND TO BROWSER FAILED:", e)
                    return

    try:
        await asyncio.gather(ws_to_clangd(), clangd_to_ws())
    finally:
        process.kill()
        shutil.rmtree(workspace, ignore_errors=True)


def try_extract_message(buffer: bytes):
    """Parse one Content-Length-framed LSP message from buffer."""
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

    msg = buffer[body_start:body_start + length]
    rest = buffer[body_start + length:]
    return msg, rest, True


def get_arduino_include_flags(fqbn: str) -> list[str]:
    if fqbn in _board_flags_cache:
        return _board_flags_cache[fqbn]

    probe_dir = Path("clangd_probe") / fqbn.replace(":", "_")
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_ino = probe_dir / f"{probe_dir.name}.ino"
    probe_ino.write_text("void setup() {}\nvoid loop() {}\n")

    result = run_cli([
        "arduino-cli", "compile",
        "--fqbn", fqbn,
        "-v",
        "--clean",
        str(probe_dir),
    ])

    compile_line = None
    for line in result.stdout.splitlines():
        if "avr-g++" in line and " -c " in line and ".ino.cpp" in line and "-o" in line:
            compile_line = line
            break

    flags: list[str] = []
    if compile_line:
        flags += ['-I' + p for p in re.findall(r'"-I([^"]+)"', compile_line)]
        flags += re.findall(r'(?<!")(-I[^\s"]+)', compile_line)
        flags += re.findall(r'(-D\S+)', compile_line)

    print("EXTRACTED FLAGS:", flags)

    shutil.rmtree(probe_dir, ignore_errors=True)
    _board_flags_cache[fqbn] = flags
    return flags

def write_compile_commands(workspace: Path, fqbn: str, main_filename: str):
    flags = get_arduino_include_flags(fqbn)

    avr_gcc_root = Path("C:/Users/Asus/AppData/Local/Arduino15/packages/arduino/tools/avr-gcc/7.3.0-atmel3.6.1-arduino7")
    avr_libc_include = avr_gcc_root / "avr" / "include"

    arguments = [
        "clang++",
        "--target=avr",
        "-mmcu=atmega328p",
        "-x", "c++", "-std=gnu++11", "-fpermissive", "-fno-exceptions",
        f"-I{avr_libc_include}",
        *flags,
        "-include", "Arduino.h",
        main_filename,
    ]

    entry = [{
        "directory": str(workspace.resolve()),
        "arguments": arguments,
        "file": main_filename,
    }]
    content = json.dumps(entry)
    print("COMPILE_COMMANDS.JSON CONTENT:", content)
    (workspace / "compile_commands.json").write_text(content)


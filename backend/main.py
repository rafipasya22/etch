
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from compile import compile_arduino


# CONFIG
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173"
    ).split(",")
    if origin.strip()
]

ALLOW_LIB_UNINSTALL = (
    os.getenv("ALLOW_LIB_UNINSTALL", "false").lower() == "true"
)

MAX_CONCURRENT_COMPILES = int(
    os.getenv("MAX_CONCURRENT_COMPILES", "1")
)

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

LIB_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 _.\-+]{0,99}$"
)

LIB_VERSION_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.\-+]{0,29}$"
)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


BUILD_DIR = Path("builds")
BUILD_DIR.mkdir(exist_ok=True)

_board_flags_cache: dict[str, list[str]] = {}

compile_slots = threading.BoundedSemaphore(
    MAX_CONCURRENT_COMPILES
)


# MODELS
class SketchFile(BaseModel):
    filename: str
    content: str


class CompileRequest(BaseModel):
    board: str
    files: list[SketchFile]
    libraries: list[str] = Field(default_factory=list)


class LibraryInstallRequest(BaseModel):
    name: str
    version: str | None = None


class LibraryUninstallRequest(BaseModel):
    name: str


# HELPER
_hits: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def rate_limit(max_calls: int, per_seconds: int):

    def dep(request: Request):
        key = f"{request.url.path}:{client_ip(request)}"
        now = time.time()
        queue = _hits[key]

        while queue and queue[0] < now - per_seconds:
            queue.popleft()

        if len(queue) >= max_calls:
            raise HTTPException(
                429,
                "Too many requests, slow down."
            )

        queue.append(now)

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
        raise HTTPException(
            400,
            f"Send between 1 and {MAX_FILES} files."
        )

    if sum(len(f.content.encode("utf-8")) for f in files) > MAX_TOTAL_BYTES:
        raise HTTPException(400, "Project too large.")


def validate_lib_name(name: str):
    if not LIB_NAME_RE.match(name):
        raise HTTPException(
            400,
            f"Invalid library name: {name!r}"
        )


def cleanup_old_builds(max_age_s: int = 3600):
    now = time.time()

    for directory in BUILD_DIR.iterdir():
        if (
            directory.is_dir()
            and now - directory.stat().st_mtime > max_age_s
        ):
            shutil.rmtree(directory, ignore_errors=True)


def ensure_libraries_installed(libraries: list[str]):
    if len(libraries) > MAX_LIBS:
        raise HTTPException(
            400,
            f"Too many libraries (max {MAX_LIBS})."
        )

    for lib_name in libraries:
        validate_lib_name(lib_name)

        result = run_cli([
            "arduino-cli",
            "lib",
            "install",
            lib_name
        ])

        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "stage": "lib_install",
                    "message": (
                        f"Failed installing {lib_name}: "
                        f"{result.stderr or result.stdout}"
                    ),
                },
            )


# ROUTES
@app.get("/")
def root():
    return {"message": "Etch"}


@app.get("/health")
def health():
    return {"ok": True}


# COMPILE + FIRMWARE
@app.post(
    "/compile",
    dependencies=[Depends(rate_limit(10, 60))]
)
def compile_code(request: CompileRequest):

    if request.board not in ALLOWED_FQBN:
        raise HTTPException(400, "Unsupported board.")

    validate_files(request.files)

    if not compile_slots.acquire(blocking=False):
        raise HTTPException(
            429,
            "Server busy, try again in a minute."
        )

    project_path = None

    try:
        cleanup_old_builds()
        ensure_libraries_installed(request.libraries)

        build_id = str(uuid.uuid4())

        project_path = BUILD_DIR / build_id
        project_path.mkdir(parents=True)

        main_ino_written = False

        for sketch_file in request.files:
            name = Path(sketch_file.filename).name

            if Path(name).suffix.lower() not in ALLOWED_EXT:
                raise HTTPException(
                    400,
                    f"Unsupported file: {name}"
                )

            if name.lower().endswith(".ino") and not main_ino_written:
                target_name = f"{build_id}.ino"
                main_ino_written = True
            else:
                target_name = name

            (project_path / target_name).write_text(
                sketch_file.content,
                encoding="utf-8"
            )

        if not main_ino_written:
            shutil.rmtree(project_path, ignore_errors=True)

            return {
                "success": False,
                "stderr": "No .ino file found in project."
            }

        result = compile_arduino(
            str(project_path),
            request.board
        )

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

    except HTTPException:
        if project_path:
            shutil.rmtree(project_path, ignore_errors=True)
        raise

    finally:
        compile_slots.release()


@app.get("/build/{build_id}/firmware")
def download_firmware(build_id: str):

    try:
        uuid.UUID(build_id)
    except ValueError:
        raise HTTPException(400, "Invalid build id")

    build_path = BUILD_DIR / build_id / "build"

    files = (
        list(build_path.glob("*"))
        if build_path.exists()
        else []
    )

    firmware = (
        next(
            (f for f in files if f.name.endswith(".merged.bin")),
            None
        )
        or next(
            (
                f for f in files
                if f.suffix == ".hex"
                and "with_bootloader" not in f.name
            ),
            None
        )
        or next(
            (f for f in files if f.suffix == ".bin"),
            None
        )
    )

    if not firmware:
        raise HTTPException(404, "Firmware not found")

    return FileResponse(
        firmware,
        filename=firmware.name,
        media_type="application/octet-stream"
    )


# LIBRARIES
@app.get(
    "/libraries/search",
    dependencies=[Depends(rate_limit(30, 60))]
)
def search_libraries(query: str = ""):

    query = query.strip()

    if query.startswith("-") or len(query) > 100:
        raise HTTPException(400, "Invalid query")

    result = run_cli([
        "arduino-cli",
        "lib",
        "search",
        query,
        "--format",
        "json"
    ])

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=result.stderr
        )

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

    return {
        "success": True,
        "libraries": libraries
    }


@app.get("/libraries/installed")
def list_installed_libraries():

    result = run_cli([
        "arduino-cli",
        "lib",
        "list",
        "--format",
        "json"
    ])

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=result.stderr
        )

    data = json.loads(result.stdout)

    libraries = [
        {
            "name": item.get("library", {}).get("name"),
            "version": item.get("library", {}).get("version"),
            "author": item.get("library", {}).get("author"),
        }
        for item in data.get("installed_libraries", [])
    ]

    return {
        "success": True,
        "libraries": libraries
    }


@app.post(
    "/libraries/install",
    dependencies=[Depends(rate_limit(10, 60))]
)
def install_library(request: LibraryInstallRequest):

    validate_lib_name(request.name)

    if (
        request.version is not None
        and not LIB_VERSION_RE.match(request.version)
    ):
        raise HTTPException(400, "Invalid version")

    target = (
        f"{request.name}@{request.version}"
        if request.version
        else request.name
    )

    result = run_cli([
        "arduino-cli",
        "lib",
        "install",
        target
    ])

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={
                "stage": "lib_install",
                "message": result.stderr or result.stdout
            }
        )

    return {
        "success": True,
        "message": f"{target} installed",
        "output": result.stdout
    }


@app.post(
    "/libraries/uninstall",
    dependencies=[Depends(rate_limit(10, 60))]
)
def uninstall_library(request: LibraryUninstallRequest):

    if not ALLOW_LIB_UNINSTALL:
        raise HTTPException(
            403,
            "Uninstall is disabled on this server."
        )

    validate_lib_name(request.name)

    result = run_cli([
        "arduino-cli",
        "lib",
        "uninstall",
        request.name
    ])

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={
                "stage": "lib_uninstall",
                "message": result.stderr or result.stdout
            }
        )

    return {
        "success": True,
        "message": f"{request.name} uninstalled",
        "output": result.stdout
    }

import subprocess
from pathlib import Path

COMPILE_TIMEOUT_S = 240


def compile_arduino(project_path: str, board: str):
    project_path = Path(project_path)
    build_path = project_path / "build"

    try:
        result = subprocess.run(
            ["arduino-cli", "compile", "--fqbn", board,
             "--output-dir", str(build_path), str(project_path)],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "return_code": -1,
                "stderr": f"Compile timed out after {COMPILE_TIMEOUT_S}s",
                "firmware_file": None}

    firmware_file = None
    if result.returncode == 0 and build_path.exists():
        firmware_file = next(
            (f for f in build_path.iterdir() if f.suffix.lower() in (".hex", ".bin")),
            None,
        )

    success = result.returncode == 0 and firmware_file is not None
    return {
        "success": success,
        "stdout": result.stdout,
        "stderr": result.stderr or ("" if success else "No firmware file produced"),
        "return_code": result.returncode,
        "firmware_file": firmware_file,
    }
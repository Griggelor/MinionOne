"""Build the fixed test-only AppContainer IPC probe via installed MSVC x64."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if sys.platform != "win32":
        raise RuntimeError("Windows MSVC build only")
    root = Path(__file__).resolve().parent.parent
    source = root / "tests/native/boxed_ipc_child_v039.c"
    destination = root / "evidence/v039/boxed_ipc_child_v039.exe"
    destination.parent.mkdir(parents=True, exist_ok=True)
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    paths = subprocess.check_output([
        str(vswhere), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-find",
        r"VC\Auxiliary\Build\vcvars64.bat",
    ], text=True).splitlines()
    if len(paths) != 1 or not Path(paths[0]).is_file():
        raise RuntimeError("trusted MSVC x64 toolchain unavailable")
    batch = destination.parent / "v039_build.cmd"
    batch.write_text(
        '@echo off\ncall "' + paths[0] + '" >nul\n'
        'if errorlevel 1 exit /b 1\n'
        'cl.exe /nologo /W4 /WX /O2 /MT /DUNICODE /D_UNICODE '
        '/Fe:"' + str(destination) + '" "' + str(source) + '"\n'
        'exit /b %errorlevel%\n', encoding="utf-8")
    try:
        subprocess.run([os.environ["ComSpec"], "/d", "/c", str(batch)],
                       cwd=destination.parent, check=True, timeout=120)
    finally:
        batch.unlink(missing_ok=True)
    if not destination.is_file() or destination.stat().st_size < 1024:
        raise RuntimeError("fixed IPC probe was not produced")
    print("V039_FIXED_IPC_CHILD_BUILT; SHELL_AND_WORKER_EXECUTION=DISABLED")


if __name__ == "__main__":
    main()

"""Build the fixed v0.37 C adversary on Windows CI; NEVER accept model code.

Windows Visual Studio Build Tools are required. The resulting executable is
an ephemeral CI helper and is not installed/exposed as a MinionOne worker tool.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def build(source, destination):
    if sys.platform != "win32":
        raise RuntimeError("Win32 MSVC compiler required")
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source.name != "appcontainer_adversary_v037.c":
        raise RuntimeError("unexpected compiler source")
    if destination.suffix.lower() != ".exe" or destination.name != "appcontainer_adversary_v037.exe":
        raise RuntimeError("unexpected fixed native helper output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    vswhere = (Path(os.environ["ProgramFiles(x86)"]) /
               "Microsoft Visual Studio/Installer/vswhere.exe")
    if not vswhere.is_file():
        raise RuntimeError("official Visual Studio compiler discovery unavailable")
    found = subprocess.check_output([
        str(vswhere), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-find", r"VC\Auxiliary\Build\vcvars64.bat",
    ], text=True).splitlines()
    if len(found) != 1 or not Path(found[0]).is_file():
        raise RuntimeError("exact Visual C++ x64 toolchain unavailable")
    # Avoid cmd.exe /S nested-quote stripping around vcvars64.bat.
    # The temporary .cmd contains ONLY known checkout/compiler paths;
    # never worker content, model text or arbitrary tool arguments.
    batch_path = destination.parent / "v037_trusted_compile.cmd"
    batch = (
        '@echo off\r\n'
        'call "' + found[0] + '" >nul\r\n'
        'if errorlevel 1 exit /b 1\r\n'
        'cl.exe /nologo /W4 /WX /O2 /MT '
        '/DUNICODE /D_UNICODE /Fe:"' + str(destination) + '" "'
        + str(source) + '" ws2_32.lib\r\n'
        'exit /b %errorlevel%\r\n'
    )
    try:
        batch_path.write_text(batch, encoding="utf-8")
        subprocess.run([os.environ["ComSpec"], "/d", "/c", str(batch_path)],
                       cwd=destination.parent, check=True, timeout=120)
    finally:
        batch_path.unlink(missing_ok=True)
    if not destination.is_file() or destination.stat().st_size < 1024:
        raise RuntimeError("native helper missing or truncated")
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    build(root / "tests/native/appcontainer_adversary_v037.c", args.out)
    print("V037_TRUSTED_HELPER_COMPILED; OS_SANDBOX=NOT_ATTESTED")


if __name__ == "__main__":
    main()

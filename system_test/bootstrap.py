"""Offline Python fallback when the native Windows app is not used."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    if sys.version_info<(3,11):raise RuntimeError('Python 3.11 or newer is required; Python 3.13 is CI-tested.')
    root=Path(__file__).resolve().parent
    manifest=json.loads((root/'bundle.json').read_text(encoding='utf-8'))
    filename=manifest['wheel']['filename']
    if Path(filename).name!=filename or not filename.endswith('.whl'):raise RuntimeError('Invalid wheel manifest.')
    wheel=root/'packages'/filename
    if sha256(wheel.read_bytes()).hexdigest()!=manifest['wheel']['sha256']:raise RuntimeError('Wheel checksum mismatch.')
    runtime=root/'python-runtime';python=runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():subprocess.run([sys.executable,'-I','-m','venv',str(runtime)],check=True)
    marker=runtime/'minionone-wheel-sha256.txt'
    if not marker.exists() or marker.read_text().strip()!=manifest['wheel']['sha256']:
        subprocess.run([str(python),'-I','-m','pip','install','--no-index','--no-deps','--force-reinstall',str(wheel)],check=True)
        marker.write_text(manifest['wheel']['sha256']+'\n')
    args=sys.argv[1:] or ['serve']
    if args==['install']:
        print('MinionOne '+manifest['version']+' installed locally. Run bootstrap.py serve.');return 0
    return subprocess.call([str(python),'-I','-m','minionone.pilot_cli',*args])

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('MinionOne installation failed. Check Python 3.13, package files and local write access.',file=sys.stderr)
        raise SystemExit(2)

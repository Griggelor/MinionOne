"""Copy installed build/runtime notices into the native distribution."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys

def collect(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True);found=[]
    for name in ('pyinstaller','altgraph','packaging','pyinstaller-hooks-contrib','pefile','pywin32-ctypes','setuptools'):
        distribution=importlib.metadata.distribution(name)
        paths=[p for p in distribution.files or () if any(x in p.name.lower() for x in ('license','copying','notice'))]
        for path in paths:
            source=distribution.locate_file(path)
            if source.is_file():
                if any(part in ('..','.') for part in path.parts) or path.is_absolute():continue
                target=destination/name/Path(*path.parts);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target);found.append({'package':name,'version':distribution.version,'file':str(target.relative_to(destination))})
    for name in ('LICENSE.txt','LICENSE'):
        path=Path(sys.base_prefix)/name
        if path.is_file():shutil.copyfile(path,destination/'CPython_LICENSE.txt');found.append({'package':'CPython','version':sys.version.split()[0],'file':'CPython_LICENSE.txt'});break
    if not any(x['package']=='pyinstaller' for x in found):raise RuntimeError('PyInstaller license notice missing')
    if not any(x['package']=='CPython' for x in found):raise RuntimeError('CPython license notice missing')
    (destination/'RUNTIME_COMPONENTS.json').write_text(json.dumps(found,indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args();collect(args.destination)

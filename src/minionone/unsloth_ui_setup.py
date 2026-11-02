"""Install a version-checked native UI recovery poll in an operator's Unsloth checkout.

The upstream file retains its AGPL license. No upstream implementation is bundled.
Rebuild/restart the host frontend after applying this integration change.
"""
import argparse
from hashlib import sha1
import os
from pathlib import Path
import tempfile

PATH='studio/frontend/src/features/chat/utils/chat-generation-recovery.ts'
BLOB='75408fa210ef8f767cb9f08c3e37bc5d0df793f3'
MARKER='// MinionOne native return recovery poll v0.46'


def install(root):
    path=Path(root).resolve()/PATH;data=path.read_bytes()
    if MARKER.encode() in data:return {'changed':False,'installed':True}
    blob=sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if blob!=BLOB:raise ValueError('Unsupported Unsloth recovery source; use the pinned API/UI revision or review an updated integration')
    text=data.decode('utf-8')
    old='  windowTarget.addEventListener("online", recover);'
    cleanup='    windowTarget.removeEventListener("online", recover);'
    if text.count(old)!=1 or text.count(cleanup)!=1:raise ValueError('Unsupported recovery source')
    text=text.replace(old,'  '+MARKER+'\n  const minionOneRecovery = globalThis.setInterval(onFocus, 2000);\n'+old)
    text=text.replace(cleanup,'    globalThis.clearInterval(minionOneRecovery);\n'+cleanup)
    backup=path.with_suffix(path.suffix+'.minionone-v046.bak')
    with backup.open('xb') as output:output.write(data)
    mode=path.stat().st_mode
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.minionone-recovery-')
    try:
        with os.fdopen(fd,'wb') as output:output.write(text.encode('utf-8'));output.flush();os.fsync(output.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {'changed':True,'installed':True,'rebuild_frontend_required':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Install native UI automatic chat recovery; rebuild the Unsloth frontend afterwards')
    parser.add_argument('--studio-root',type=Path,required=True)
    args=parser.parse_args()
    import json
    print(json.dumps(install(args.studio_root),sort_keys=True))

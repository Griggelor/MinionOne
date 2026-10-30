"""Operator-state process lock held before any pilot recovery or listener bind."""
import os
from pathlib import Path
from .pilot_jobs import PilotError

class PilotLock:
    def __init__(self,database):
        path=Path(str(Path(database).resolve())+'.pilot.lock')
        path.parent.mkdir(parents=True,exist_ok=True)
        self.file=open(path,'a+b');self.locked=False
        try:
            if path.stat().st_size==0:self.file.write(b'0');self.file.flush()
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.locked=True
        except OSError:
            self.file.close();raise PilotError('PILOT_INSTANCE_BUSY') from None
    def close(self):
        if self.locked:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            finally:self.locked=False;self.file.close()

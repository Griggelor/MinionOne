"""Windows NTFS *read-only* input confinement (admission development, NOT a G1b PASS).

Uses held native directory handles, per-component relative NtCreateFile, and
handle-based enumeration. The manager admission path may pin native handles; workers cannot invoke this module. No write APIs exist.
GitHub runner success is NOT a target-host Windows security gate.
"""
from __future__ import annotations
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import sys
from .admission import InputRejected

_VALID_JOB = re.compile(r"JOB-[0-9a-f]{32}\Z", re.ASCII)
_VALID_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,119}\Z", re.ASCII)
_RESERVED = {'CON','PRN','AUX','NUL'} | {f'COM{i}' for i in range(1,10)} | {f'LPT{i}' for i in range(1,10)}
_READ_ATTRIBUTES=0x80
_SYNCHRONIZE=0x00100000
_SHARE_READ=1
_FILE_OPEN=1
_DIRECTORY=1
_NON_DIRECTORY=0x40
_SYNC=0x20
_OPEN_REPARSE=0x00200000
_WIN_BACKUP=0x02000000
_TAG_REPARSE=0x400
_FILE_ID=18
_FILE_STANDARD=1
_FILE_TAG=9
_FILE_BASIC=0
_FILE_NAMES=12
_NO_MORE_FILES=0x80000006
_OBJ_CASE_INSENSITIVE=0x40

class _UnicodeString(ctypes.Structure):
    _fields_=[('Length',wintypes.USHORT),('MaximumLength',wintypes.USHORT),
              ('Buffer',wintypes.LPWSTR)]
class _ObjectAttributes(ctypes.Structure):
    _fields_=[('Length',wintypes.ULONG),('RootDirectory',wintypes.HANDLE),
              ('ObjectName',ctypes.POINTER(_UnicodeString)),('Attributes',wintypes.ULONG),
              ('SecurityDescriptor',ctypes.c_void_p),('SecurityQualityOfService',ctypes.c_void_p)]
class _IoStatusBlock(ctypes.Structure):
    _fields_=[('Status',ctypes.c_void_p),('Information',ctypes.c_size_t)]
class _FileIdInfo(ctypes.Structure):
    _fields_=[('VolumeSerialNumber',ctypes.c_uint64),('FileId',ctypes.c_ubyte*16)]
class _FileStandardInfo(ctypes.Structure):
    _fields_=[('AllocationSize',ctypes.c_int64),('EndOfFile',ctypes.c_int64),
              ('NumberOfLinks',wintypes.DWORD),('DeletePending',wintypes.BOOLEAN),
              ('Directory',wintypes.BOOLEAN)]
class _FileTagInfo(ctypes.Structure):
    _fields_=[('FileAttributes',wintypes.DWORD),('ReparseTag',wintypes.DWORD)]
class _FileBasicInfo(ctypes.Structure):
    _fields_=[('CreationTime',ctypes.c_int64),('LastAccessTime',ctypes.c_int64),
              ('LastWriteTime',ctypes.c_int64),('ChangeTime',ctypes.c_int64),
              ('FileAttributes',wintypes.DWORD)]

def _component(name):
    if (type(name) is not str or not _VALID_NAME.fullmatch(name) or name.endswith('.') or
        name.split('.')[0].upper() in _RESERVED or ':' in name or '/' in name or '\\' in name):
        raise InputRejected('noncanonical Windows input component denied')
    return name

class _Native:
    def __init__(self):
        if sys.platform != 'win32':
            raise InputRejected('Win32 input broker requires native Windows')
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        n=ctypes.WinDLL('ntdll',use_last_error=True)
        self.create=k.CreateFileW
        self.create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                              ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.create.restype=wintypes.HANDLE
        self.close=k.CloseHandle
        self.close.argtypes=[wintypes.HANDLE]
        self.close.restype=wintypes.BOOL
        self.info=k.GetFileInformationByHandleEx
        self.info.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.info.restype=wintypes.BOOL
        self.read=k.ReadFile
        self.read.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        self.read.restype=wintypes.BOOL
        self.open_relative=n.NtCreateFile
        self.open_relative.argtypes=[ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,
                     ctypes.POINTER(_ObjectAttributes),ctypes.POINTER(_IoStatusBlock),
                     ctypes.c_void_p,wintypes.ULONG,wintypes.ULONG,wintypes.ULONG,
                     wintypes.ULONG,ctypes.c_void_p,wintypes.ULONG]
        self.open_relative.restype=ctypes.c_long
        self.enumerate=n.NtQueryDirectoryFile
        self.enumerate.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.c_void_p,
                     ctypes.c_void_p,ctypes.POINTER(_IoStatusBlock),ctypes.c_void_p,
                     wintypes.ULONG,ctypes.c_int,wintypes.BOOLEAN,ctypes.c_void_p,
                     wintypes.BOOLEAN]
        self.enumerate.restype=ctypes.c_long

    def query(self,handle,klass,structure):
        out=structure()
        if not self.info(handle,klass,ctypes.byref(out),ctypes.sizeof(out)):
            raise InputRejected('native handle metadata unavailable')
        return out

    def identity(self,handle):
        info=self.query(handle,_FILE_ID,_FileIdInfo)
        return int(info.VolumeSerialNumber),bytes(info.FileId)

    def validate(self,handle,*,directory,volume=None,single_link=False):
        tag=self.query(handle,_FILE_TAG,_FileTagInfo)
        std=self.query(handle,_FILE_STANDARD,_FileStandardInfo)
        ident=self.identity(handle)
        if tag.FileAttributes & _TAG_REPARSE:
            raise InputRejected('Windows reparse point denied')
        if bool(std.Directory)!=bool(directory) or bool(std.DeletePending):
            raise InputRejected('Windows file type or delete state denied')
        if volume is not None and ident[0]!=volume:
            raise InputRejected('Windows volume boundary denied')
        if single_link and std.NumberOfLinks!=1:
            raise InputRejected('Windows hardlink count denied')
        return ident

    def root(self,path):
        h=self.create(str(path),_READ_ATTRIBUTES|1|_SYNCHRONIZE,_SHARE_READ,None,3,
                      _OPEN_REPARSE|_WIN_BACKUP,None)
        if not h or h==ctypes.c_void_p(-1).value:
            raise InputRejected('configured Windows project root could not be anchored')
        return h

    def child(self,parent,name,*,directory):
        if name!='.minionone':
            _component(name)
        text=ctypes.create_unicode_buffer(name)
        size=len(name.encode('utf-16-le'))
        name_arg=_UnicodeString(size,size+2,ctypes.cast(text,wintypes.LPWSTR))
        attrs=_ObjectAttributes(ctypes.sizeof(_ObjectAttributes),parent,
                 ctypes.pointer(name_arg),_OBJ_CASE_INSENSITIVE,None,None)
        ios=_IoStatusBlock()
        out=wintypes.HANDLE()
        opts=_SYNC|_OPEN_REPARSE|(_DIRECTORY if directory else _NON_DIRECTORY)
        status=self.open_relative(ctypes.byref(out),_READ_ATTRIBUTES|_SYNCHRONIZE|1,
                    ctypes.byref(attrs),ctypes.byref(ios),None,0x80,_SHARE_READ,
                    _FILE_OPEN,opts,None,0)
        if status!=0 or not out.value:
            raise InputRejected('native relative child handle denied (NTSTATUS 0x%08x)' %
                                (status & 0xffffffff))
        return out.value

    def list_names(self,handle,*,max_names=32):
        found=[]
        buffer=ctypes.create_string_buffer(65536)
        for iteration in range(32):
            ios=_IoStatusBlock()
            status=self.enumerate(handle,None,None,None,ctypes.byref(ios),buffer,
                     len(buffer),_FILE_NAMES,False,None,iteration==0)
            if status & 0xffffffff==_NO_MORE_FILES:
                return found
            if status!=0 or ios.Information==0 or ios.Information>len(buffer):
                raise InputRejected('native Windows directory scan failed')
            raw=buffer.raw[:ios.Information]
            offset=0
            while True:
                if offset+12>len(raw):
                    raise InputRejected('invalid Windows directory buffer')
                next_offset=int.from_bytes(raw[offset:offset+4],'little')
                length=int.from_bytes(raw[offset+8:offset+12],'little')
                if length%2 or length>512 or offset+12+length>len(raw):
                    raise InputRejected('invalid Windows directory entry')
                name=raw[offset+12:offset+12+length].decode('utf-16-le','strict')
                if name not in ('.','..'):
                    found.append(_component(name))
                    if len(found)>max_names:
                        raise InputRejected('Windows directory quota exceeded')
                if next_offset==0:
                    break
                if next_offset<12 or next_offset%4 or offset+next_offset>=len(raw):
                    raise InputRejected('invalid Windows directory offset')
                offset+=next_offset
        raise InputRejected('Windows directory scan budget exceeded')

    def read_regular(self,handle,*,max_bytes):
        before=self.validate(handle,directory=False,single_link=True)
        old=self.query(handle,_FILE_STANDARD,_FileStandardInfo)
        old_time=self.query(handle,_FILE_BASIC,_FileBasicInfo)
        if old.EndOfFile<0 or old.EndOfFile>max_bytes:
            raise InputRejected('Windows file quota exceeded')
        data=bytearray()
        while len(data)<=max_bytes:
            buffer=ctypes.create_string_buffer(min(65536,max_bytes+1-len(data)))
            count=wintypes.DWORD()
            if not self.read(handle,buffer,len(buffer),ctypes.byref(count),None):
                raise InputRejected('native Windows read failed')
            if count.value==0:
                break
            data.extend(buffer.raw[:count.value])
        after=self.validate(handle,directory=False,single_link=True)
        new=self.query(handle,_FILE_STANDARD,_FileStandardInfo)
        new_time=self.query(handle,_FILE_BASIC,_FileBasicInfo)
        if (len(data)>max_bytes or before!=after or old.EndOfFile!=new.EndOfFile or
            len(data)!=new.EndOfFile or
            (old_time.LastWriteTime,old_time.ChangeTime)!=
            (new_time.LastWriteTime,new_time.ChangeTime)):
            raise InputRejected('Windows file changed during capture')
        return bytes(data)

class WindowsInputBroker:
    """Native read-only input snapshot; never exposes work/ or output/."""
    def __init__(self,project_dir,job_id):
        self.project=Path(project_dir)
        if type(job_id) is not str or not _VALID_JOB.fullmatch(job_id):
            raise InputRejected('invalid Windows job ID')
        if not self.project.is_absolute():
            raise InputRejected('Windows project must be an absolute operator path')
        self.job_id=job_id
        self._api=None
        self._stack=None
        self._input=None

    def __enter__(self):
        api=_Native()
        stack=ExitStack()
        try:
            root=api.root(self.project)
            stack.callback(api.close,root)
            identity=api.validate(root,directory=True)
            try:
                expected=self.project.stat()
            except OSError as exc:
                raise InputRejected('configured Windows root unavailable') from exc
            if int.from_bytes(identity[1],'little')!=expected.st_ino:
                raise InputRejected('Windows project root identity changed')
            parent=root
            for name in ('.minionone','jobs',self.job_id,'input'):
                child=api.child(parent,name,directory=True)
                stack.callback(api.close,child)
                api.validate(child,directory=True,volume=identity[0])
                parent=child
            self._api,self._stack,self._input=api,stack,parent
            return self
        except Exception:
            stack.close()
            raise

    def __exit__(self,*exc):
        try:
            self._stack.close()
        finally:
            self._api=self._stack=self._input=None

    def list_input(self):
        if self._input is None:
            raise InputRejected('Windows input broker not opened')
        names=self._api.list_names(self._input)
        if len({name.casefold() for name in names})!=len(names):
            raise InputRejected('ambiguous case-insensitive Windows input files')
        return names

    def read_input(self,filename,*,max_bytes=1048576):
        _component(filename)
        if (self._input is None or type(max_bytes) is not int or
            not 0<=max_bytes<=4194304):
            raise InputRejected('Windows broker closed or quota invalid')
        handle=self._api.child(self._input,filename,directory=False)
        try:
            return self._api.read_regular(handle,max_bytes=max_bytes)
        finally:
            self._api.close(handle)

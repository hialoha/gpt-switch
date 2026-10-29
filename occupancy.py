"""Ask Windows which processes actually hold the selected SQLite files."""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path


class UniqueProcess(ct.Structure):
    _fields_ = [('pid',wt.DWORD),('start',wt.FILETIME)]


class ProcessInfo(ct.Structure):
    _fields_ = [('process',UniqueProcess),('name',wt.WCHAR*256),('service',wt.WCHAR*64),
                ('kind',ct.c_int),('status',wt.ULONG),('session',wt.DWORD),('restartable',wt.BOOL)]


def image_path(pid):
    kernel=ct.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD]; kernel.OpenProcess.restype=wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wt.HANDLE,wt.DWORD,wt.LPWSTR,ct.POINTER(wt.DWORD)]
    kernel.CloseHandle.argtypes=[wt.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle: return ''
    try:
        buffer=ct.create_unicode_buffer(32768); size=wt.DWORD(len(buffer))
        return buffer.value if kernel.QueryFullProcessImageNameW(handle,0,buffer,ct.byref(size)) else ''
    finally: kernel.CloseHandle(handle)


def holders(db):
    if os.name != 'nt': raise RuntimeError('文件占用检测仅支持 Windows')
    lib=ct.WinDLL('Rstrtmgr')
    lib.RmStartSession.argtypes=[ct.POINTER(wt.DWORD),wt.DWORD,wt.LPWSTR]
    lib.RmRegisterResources.argtypes=[wt.DWORD,wt.UINT,ct.POINTER(wt.LPCWSTR),wt.UINT,ct.POINTER(UniqueProcess),wt.UINT,ct.POINTER(wt.LPCWSTR)]
    lib.RmGetList.argtypes=[wt.DWORD,ct.POINTER(wt.UINT),ct.POINTER(wt.UINT),ct.POINTER(ProcessInfo),ct.POINTER(wt.DWORD)]
    lib.RmEndSession.argtypes=[wt.DWORD]
    handle=wt.DWORD(); key=ct.create_unicode_buffer(33)
    result=lib.RmStartSession(ct.byref(handle),0,key)
    if result: raise RuntimeError('Windows 占用检测启动失败：'+str(result))
    try:
        paths=[str(Path(str(db)+suffix).resolve()) for suffix in ('','-wal','-shm') if Path(str(db)+suffix).exists()]
        if not paths: raise RuntimeError('数据库文件不存在：'+str(db))
        names=(wt.LPCWSTR*len(paths))(*paths)
        result=lib.RmRegisterResources(handle,len(paths),names,0,None,0,None)
        if result: raise RuntimeError('Windows 文件占用登记失败：'+str(result))
        for _ in range(5):
            needed=wt.UINT(); count=wt.UINT(); reasons=wt.DWORD()
            result=lib.RmGetList(handle,ct.byref(needed),ct.byref(count),None,ct.byref(reasons))
            if result==0: return []
            if result!=234: raise RuntimeError('Windows 文件占用查询失败：'+str(result))
            info=(ProcessInfo*needed.value)(); count.value=needed.value
            result=lib.RmGetList(handle,ct.byref(needed),ct.byref(count),info,ct.byref(reasons))
            if result==234: continue
            if result: raise RuntimeError('Windows 文件占用查询失败：'+str(result))
            return [{'pid':r.process.pid,'name':r.name or r.service or '未知进程','path':image_path(r.process.pid)}
                    for r in info[:count.value] if r.process.pid!=os.getpid()]
        raise RuntimeError('文件占用进程持续变化，请稍后重试')
    finally: lib.RmEndSession(handle)


def describe(items):
    def label(r):
        path=r.get('path','').lower()
        kind='VS Code 插件' if '.vscode' in path else ('Codex 桌面后端' if 'openai' in path and 'codex' in path else r['name'])
        return f"{kind}（PID {r['pid']}，{r.get('path') or r['name']}）"
    return '；'.join(label(r) for r in items)

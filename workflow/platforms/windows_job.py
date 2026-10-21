"""Standalone Python supervisor: attach to a Job before spawning any workload.

The job handle is not inherited. Killing this supervisor closes its handle and
Windows terminates every descendant, even if the workload parent already exited.
Requires the shipped/source Python runtime, not a frozen GUI executable.
"""
import ctypes
from ctypes import wintypes
import subprocess
import sys


def supervise(command):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class BasicLimit(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                    ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount','WriteOperationCount','OtherOperationCount',
            'ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class ExtendedLimit(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IOCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.ExitProcess.argtypes = [wintypes.UINT]
    kernel.ExitProcess.restype = None
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel.CloseHandle(job)
        raise
    # Leave the non-inherited job handle open until supervisor exit. No child
    # can escape by exiting its parent or inheriting an owner handle.
    result = subprocess.run(command)
    sys.stdout.flush()
    sys.stderr.flush()
    kernel.ExitProcess(result.returncode & 0xffffffff)


if __name__ == '__main__':
    try:
        supervise(sys.argv[1:])
    except Exception as error:
        # Do not print command arguments: they may belong to confidential work.
        print('Windows process supervisor failed: '+type(error).__name__, file=sys.stderr)
        raise SystemExit(1)

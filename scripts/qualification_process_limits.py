"""Resource limits for an explicitly owned synthetic API child, never services."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

CREATE_SUSPENDED=0x00000004


class OwnedApiLimits:
    def __init__(self,pid,*,cpus=None,memory_mb=None):
        if pid==os.getpid():raise ValueError('Refuse to constrain the qualification generator')
        if cpus is None and memory_mb is None:raise ValueError('An explicit API resource limit is required')
        if cpus is not None and (type(cpus) is not int or cpus<1):raise ValueError('Invalid CPU count')
        if memory_mb is not None and (type(memory_mb) is not int or memory_mb<32):raise ValueError('Invalid process memory cap')
        import psutil
        self.process=psutil.Process(pid)
        if self.process.ppid()!=os.getpid():raise ValueError('Only this generator\'s direct owned child may be constrained')
        # Windows venv python.exe is a redirector: the application can already
        # be a grandchild when this method is called. Existing descendants must
        # be assigned explicitly; future descendants inherit the owned job.
        self.members=[self.process,*self.process.children(recursive=True)]
        self.job=None;self.kernel=None
        self.info={'scope':'owned API child process only; PostgreSQL/Redis/generator excluded',
                   'api_pid':pid,'requested_cpus':cpus,'requested_memory_mb':memory_mb,
                   'railway_or_container_equivalence':False,'applied':True}
        original=self.process.cpu_affinity()
        try:
            if memory_mb is not None:
                if os.name=='nt':self._windows_memory_limit(memory_mb)
                else:self._linux_memory_limit(memory_mb)
            if cpus is not None:
                if len(original)<cpus:raise ValueError('Requested affinity exceeds available logical processors')
                selected=original[:cpus]
                for member in self.members:member.cpu_affinity(selected)
                actual=self.process.cpu_affinity()
                if any(member.cpu_affinity()!=selected for member in self.members):raise RuntimeError('Owned API CPU affinity was not applied')
                self.info.update(cpu_method='OS process affinity',logical_processor_ids=actual,
                    effective_logical_processors=len(actual),cpu_quota_equivalent=False,
                    verified_owned_process_ids=[member.pid for member in self.members])
        except Exception:
            self.close()
            raise

    def _windows_memory_limit(self,memory_mb):
        if os.name!='nt':raise RuntimeError('Process memory qualification currently requires Windows Job Objects')
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),
                ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
                ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
                ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                'ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',Io),('ProcessMemoryLimit',ctypes.c_size_t),
                ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel;self.Extended=Extended
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.SetInformationJobObject.restype=wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p];kernel.QueryInformationJobObject.restype=wintypes.BOOL
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.AssignProcessToJobObject.restype=wintypes.BOOL
        kernel.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)];kernel.IsProcessInJob.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
        job=kernel.CreateJobObjectW(None,None)
        if not job:raise ctypes.WinError(ctypes.get_last_error())
        self.job=job
        limits=Extended();limits.BasicLimitInformation.LimitFlags=0x00000100|0x00000200|0x00002000
        limits.ProcessMemoryLimit=memory_mb*1024*1024
        limits.JobMemoryLimit=memory_mb*1024*1024
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        assigned=[]
        for owned in self.members:
            process=kernel.OpenProcess(0x0100|0x0001|0x1000,False,owned.pid)
            if not process:raise ctypes.WinError(ctypes.get_last_error())
            try:
                member=wintypes.BOOL()
                if not kernel.IsProcessInJob(process,job,ctypes.byref(member)):raise ctypes.WinError(ctypes.get_last_error())
                if not member.value and not kernel.AssignProcessToJobObject(job,process):raise ctypes.WinError(ctypes.get_last_error())
                if not kernel.IsProcessInJob(process,job,ctypes.byref(member)) or not member.value:
                    raise RuntimeError('Owned API Job Object membership was not verified')
                assigned.append(owned.pid)
            finally:kernel.CloseHandle(process)
        current=self._query_job()
        if current.ProcessMemoryLimit!=limits.ProcessMemoryLimit:raise RuntimeError('Owned API process memory limit not verified')
        self.info.update(memory_method='Windows Job Object process and owned-job committed-memory limits',
            effective_process_commit_limit_bytes=int(current.ProcessMemoryLimit),
            effective_job_commit_limit_bytes=int(current.JobMemoryLimit),
            memory_scope='per-process committed memory; distinct from Linux cgroup/container RSS accounting',
            job_membership_verified=True,job_verified_owned_process_ids=assigned,kill_owned_api_on_generator_exit=True)

    def _linux_memory_limit(self,memory_mb):
        if not sys_platform_linux():raise RuntimeError('Process memory qualification requires Windows or Linux')
        import resource
        limit=memory_mb*1024*1024
        resource.prlimit(self.process.pid,resource.RLIMIT_AS,(limit,limit))
        if resource.prlimit(self.process.pid,resource.RLIMIT_AS)!=(limit,limit):
            raise RuntimeError('Owned API address-space limit not verified')
        self.info.update(memory_method='Linux prlimit RLIMIT_AS',effective_address_space_limit_bytes=limit,
                         memory_scope='virtual address space; distinct from container/cgroup physical RSS accounting')

    def _query_job(self):
        current=self.Extended()
        if not self.kernel.QueryInformationJobObject(self.job,9,ctypes.byref(current),ctypes.sizeof(current),None):
            raise ctypes.WinError(ctypes.get_last_error())
        return current

    def snapshot(self):
        result=dict(self.info)
        if self.job:
            current=self._query_job()
            result.update(effective_process_commit_limit_bytes=int(current.ProcessMemoryLimit),
                          peak_process_commit_bytes=int(current.PeakProcessMemoryUsed),
                          peak_job_commit_bytes=int(current.PeakJobMemoryUsed))
        return result

    def verify_running_tree(self):
        processes=[self.process,*self.process.children(recursive=True)]
        verified=[]
        for process in processes:
            affinity=process.cpu_affinity()
            if 'logical_processor_ids' in self.info and affinity!=self.info['logical_processor_ids']:
                raise RuntimeError('A real API descendant escaped the requested affinity')
            member_value=None
            if self.job:
                handle=self.kernel.OpenProcess(0x1000,False,process.pid)
                if not handle:raise ctypes.WinError(ctypes.get_last_error())
                try:
                    member=wintypes.BOOL()
                    if not self.kernel.IsProcessInJob(handle,self.job,ctypes.byref(member)) or not member.value:
                        raise RuntimeError('A real API descendant escaped the memory job')
                    member_value=bool(member.value)
                finally:self.kernel.CloseHandle(handle)
            verified.append({'pid':process.pid,'logical_processor_ids':affinity,'memory_job_member':member_value})
        self.info['running_api_tree_verified']=verified
        return self.snapshot()

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job);self.job=None


def sys_platform_linux():
    import sys
    return sys.platform.startswith('linux')


def resume_owned_child(pid):
    """Resume the one initial thread of this generator's suspended child."""
    if os.name!='nt':return
    import psutil
    process=psutil.Process(pid)
    if process.ppid()!=os.getpid():raise ValueError('Only a direct owned child may be resumed')
    threads=process.threads()
    if len(threads)!=1:raise RuntimeError('Owned suspended child must have exactly one initial thread')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenThread.restype=wintypes.HANDLE
    kernel.ResumeThread.argtypes=[wintypes.HANDLE];kernel.ResumeThread.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    thread=kernel.OpenThread(0x0002,False,threads[0].id)
    if not thread:raise ctypes.WinError(ctypes.get_last_error())
    try:
        previous=kernel.ResumeThread(thread)
        if previous==0xffffffff:raise ctypes.WinError(ctypes.get_last_error())
        if previous!=1:raise RuntimeError('Owned initial thread was not singly suspended')
    finally:kernel.CloseHandle(thread)

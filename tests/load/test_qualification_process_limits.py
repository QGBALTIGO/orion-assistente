"""Apply the real Windows resource APIs only to a fresh owned tiny child."""
import importlib.util
import os
import subprocess
import sys

import pytest

from scripts.qualification_process_limits import OwnedApiLimits,resume_owned_child,CREATE_SUSPENDED


def test_never_limits_the_generator_itself():
    with pytest.raises(ValueError,match='generator'):OwnedApiLimits(os.getpid(),cpus=2)


@pytest.mark.skipif(os.name!='nt' or importlib.util.find_spec('psutil') is None,reason='Requires real Windows Job Objects and isolated psutil')
def test_real_child_affinity_and_memory_limit_leave_parent_unmodified():
    import psutil
    before=psutil.Process().cpu_affinity()
    child=subprocess.Popen([sys.executable,'-u','-c',
        "import sys; print('ready',flush=True); sys.stdin.readline(); "
        "exec(\"try:\\n x=bytearray(96*1024*1024)\\n x[::4096]=b'x'*(len(x)//4096)\\n print('allocation_succeeded')\\nexcept MemoryError:\\n print('memory_limited')\")"],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        creationflags=subprocess.CREATE_NO_WINDOW|CREATE_SUSPENDED)
    limits=None
    try:
        limits=OwnedApiLimits(child.pid,cpus=min(2,len(before)),memory_mb=64)
        resume_owned_child(child.pid)
        assert child.stdout.readline().strip()=='ready'
        receipt=limits.verify_running_tree()
        assert receipt['job_membership_verified'] and receipt['effective_process_commit_limit_bytes']==64*1024*1024
        assert receipt['effective_logical_processors']==min(2,len(before))
        assert all(item['memory_job_member'] for item in receipt['running_api_tree_verified'])
        output,error=child.communicate('go\n',timeout=15)
        assert child.returncode==0 and 'memory_limited' in output,error
        assert limits.snapshot()['peak_process_commit_bytes']<=64*1024*1024
        assert psutil.Process().cpu_affinity()==before
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        if limits:limits.close()


@pytest.mark.skipif(not sys.platform.startswith('linux') or importlib.util.find_spec('psutil') is None,reason='Requires Linux prlimit and psutil')
def test_real_linux_child_address_space_cap_is_enforced():
    import psutil
    before=psutil.Process().cpu_affinity()
    child=subprocess.Popen([sys.executable,'-u','-c',
        "import sys; print('ready',flush=True); sys.stdin.readline(); "
        "exec(\"try:\\n x=bytearray(192*1024*1024)\\n print('allocation_succeeded')\\nexcept MemoryError:\\n print('memory_limited')\")"],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    limits=None
    try:
        assert child.stdout.readline().strip()=='ready'
        limits=OwnedApiLimits(child.pid,cpus=min(2,len(before)),memory_mb=128)
        output,error=child.communicate('go\n',timeout=15)
        assert child.returncode==0 and 'memory_limited' in output,error
        assert limits.snapshot()['effective_address_space_limit_bytes']==128*1024*1024
        assert psutil.Process().cpu_affinity()==before
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        if limits:limits.close()

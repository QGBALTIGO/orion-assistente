"""Attach the release probe to the exact worker revision after normal startup."""
from hashlib import sha1
from pathlib import Path

path = Path("/srv/app/app/worker.py")
source = path.read_bytes()
assert sha1(b"blob %d" % len(source) + b"\0" + source).hexdigest() == "1436eb6c6a80b018867670b61a06b8bd1ec466b6"
anchor = b"class WorkerSettings:\n    on_startup = startup_plan_permissions"
replacement = b"""async def startup_with_reminder_release_probe(ctx):
    await startup_plan_permissions(ctx)
    from app.orion_reminder_live_probe import start
    start(ctx)


class WorkerSettings:
    on_startup = startup_with_reminder_release_probe"""
assert source.count(anchor) == 1
path.write_bytes(source.replace(anchor, replacement))

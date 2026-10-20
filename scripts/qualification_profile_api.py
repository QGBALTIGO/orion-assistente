"""Opt-in CPU profiling around the real disposable qualification API lifespan.

Importing this module is inert. The factory rejects ordinary/remote databases
before importing the application. Profiles contain call metadata, never request
arguments, local variables, responses or environment values.
"""
from __future__ import annotations

import cProfile
from dataclasses import dataclass
import json
import os
from pathlib import Path
import pstats
import re
import time

from scripts.qualification_load import ROLE_ENV, local_url


@dataclass(frozen=True)
class Settings:
    directory: Path

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        if env.get("ORION_QUALIFICATION_DISPOSABLE") != "1" or env.get("ORION_QUALIFICATION_PROFILE_API") != "1":
            raise RuntimeError("Disposable API profiling requires explicit opt-in")
        identities = []
        for name, role in ROLE_ENV.items():
            url = local_url(env.get(name, ""), role=role)
            if not re.fullmatch(r"orion_qualification_[0-9a-f]{32}", url.database or ""):
                raise ValueError("Disposable profiling database required")
            identities.append((url.host, url.port or 5432, url.database))
        if len(set(identities)) != 1:
            raise ValueError("Profiling roles must share the owned disposable database")
        local_url(env.get("REDIS_URL", ""), database="15", redis_url=True)
        if any(env.get(name, "0") != "0" for name in (
                "MARKETING_EXECUTION_ENABLED", "MARKETING_RECONCILIATION_ENABLED", "QR_CHANNELS_ENABLED",
                "QR_AI_ENABLED", "GOOGLE_LIVE_SYNC", "GROQ_PROBE_ON_START")):
            raise ValueError("External effects must remain disabled during profiling")
        directory = Path(env.get("ORION_QUALIFICATION_PROFILE_DIRECTORY", ""))
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise ValueError("Owned profiling artifact directory required")
        return cls(directory)


def save_profile(profile, directory):
    """One PID-scoped pair per worker; binary pstats also has no call arguments."""
    stem = "worker-" + str(os.getpid())
    stats = pstats.Stats(profile)
    functions = []
    for (filename, line, name), (primitive, calls, own, cumulative, _callers) in stats.stats.items():
        # Keep function identity useful without publishing absolute host paths.
        functions.append({"function": Path(filename).name + ":" + str(line) + ":" + name,
            "primitive_calls": primitive, "calls": calls, "tottime": own, "cumtime": cumulative})
    functions.sort(key=lambda item: item["cumtime"], reverse=True)
    payload = {"schema": 1, "diagnostic_only": True, "clock": "time.process_time",
        "scope": "worker main thread, application startup through graceful shutdown",
        "functions": functions}
    temporary = directory / (stem + ".prof.tmp")
    profile.dump_stats(str(temporary))
    os.replace(temporary, directory / (stem + ".prof"))
    temporary = directory / (stem + ".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, directory / (stem + ".json"))


class ProfiledApi:
    def __init__(self, application, settings):
        self.application = application
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            return await self.application(scope, receive, send)
        # Each Uvicorn worker constructs this locally; no inherited active
        # profiler or cross-process aggregate can mix the workers' measurements.
        profile = cProfile.Profile(timer=time.process_time)
        profile.enable()
        try:
            await self.application(scope, receive, send)
        finally:
            profile.disable()
            save_profile(profile, self.settings.directory)


def create_app():
    settings = Settings.from_environment()
    from app.main import app
    return ProfiledApi(app, settings)

"""ADB transport and environment configuration shared by the web service."""

import asyncio
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def required(name):
    value = os.environ.get(name, "").strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"Set {name} to a non-empty value without whitespace")
    return value


def port(name, default):
    value = int(os.environ.get(name, str(default)))
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


ROOT = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("STATE_DIR", "/var/lib/wanbo-remote"))
ADB = os.environ.get("ADB", shutil.which("adb") or "adb")
TARGET = (required("PROJECTOR_HOST"), port("PROJECTOR_PORT", 5555))
EXPECTED_SERIAL = required("PROJECTOR_SERIAL")
PORT = port("PORT", 8765)
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", f"http://127.0.0.1:{PORT}").rstrip("/")
origin = urlsplit(WEB_ORIGIN)
if (origin.scheme not in ("http", "https") or not origin.hostname
        or origin.username is not None or origin.password is not None
        or origin.path or origin.query or origin.fragment):
    raise ValueError("WEB_ORIGIN must be one absolute HTTP(S) origin without a path")
if origin.port is not None and not 1 <= origin.port <= 65535:
    raise ValueError("WEB_ORIGIN has an invalid port")


async def relay(reader, writer):
    upstream = None
    try:
        upstream_reader, upstream = await asyncio.wait_for(
            asyncio.open_connection(*TARGET), timeout=8
        )

        async def copy(source, destination):
            while data := await source.read(65536):
                destination.write(data)
                await destination.drain()
            if destination.can_write_eof():
                destination.write_eof()
                await destination.drain()

        await asyncio.gather(copy(reader, upstream), copy(upstream_reader, writer))
    except (OSError, TimeoutError):
        pass
    finally:
        streams = [writer] + ([upstream] if upstream else [])
        for stream in streams:
            stream.close()
        await asyncio.gather(*(stream.wait_closed() for stream in streams), return_exceptions=True)


async def adb(*args):
    process = await asyncio.create_subprocess_exec(
        ADB, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
    if process.returncode:
        raise RuntimeError(output.decode(errors="replace").strip())
    return output.decode(errors="replace")

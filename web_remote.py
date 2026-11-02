#!/usr/bin/env python3
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import remote


PORT = remote.PORT
KEYS = {"up": 19, "down": 20, "left": 21, "right": 22, "ok": 23,
        "home": 3, "back": 4, "play": 85, "vol_up": 24, "vol_down": 25, "mute": 164}
APPS = {"jellyfin": "org.jellyfin.androidtv", "smarttube": "app.smarttube.fdroid", "kodi": "org.xbmc.kodi"}
SERVER = "/data/local/tmp/wanbo-web-scrcpy-server.jar"
SERVER_SHA = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"
# USB HID mouse descriptor used by scrcpy 5.0.1 (upstream LICENSE is bundled).
MOUSE_DESCRIPTOR = bytes.fromhex(
    "05010902a1010901a10005091901290515002501950575018102"
    "95017503810105010930093109381581257f750895038106"
    "050c0a38021581257f750895018106c0c0"
)


class Busy(Exception):
    pass


def key_packet(name):
    if name not in KEYS:
        raise ValueError("Unknown button")
    return b"".join(struct.pack(">BBIII", 0, action, KEYS[name], 0, 0) for action in (0, 1))


def text_packet(text):
    if not isinstance(text, str) or not text or "\0" in text:
        raise ValueError("Invalid text")
    data = text.encode("utf-8")
    if len(data) > 4096:
        raise ValueError("Text too long")
    return struct.pack(">BQBI", 9, 1, 1, len(data)) + data


def app_packet(name):
    if not isinstance(name, str) or name not in APPS:
        raise ValueError("Unknown app")
    package = APPS[name].encode("ascii")
    return bytes((16, len(package))) + package


def mouse_packet(dx, dy, wheel, button=None):
    if any(type(v) is not int or abs(v) > limit for v, limit in ((dx, 2048), (dy, 2048), (wheel, 10))):
        raise ValueError("Invalid mouse motion")
    if button not in (None, "left", "right"):
        raise ValueError("Invalid mouse button")
    header = struct.pack(">BHH", 13, 1, 5)
    data = bytearray()
    while dx or dy or wheel:
        x, y = max(-127, min(127, dx)), max(-127, min(127, dy))
        data.extend(header + struct.pack("<Bbbbb", 0, x, y, wheel, 0))
        dx, dy, wheel = dx - x, dy - y, 0
    if button:
        data.extend(header + struct.pack("<Bbbbb", 1 if button == "left" else 2, 0, 0, 0, 0))
        data.extend(header + bytes(5))
    return bytes(data)


async def stop(process):
    if process and process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class Device:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.snapshot_lock = asyncio.Lock()
        self.relay = self.serial = self.forward = self.process = None
        self.reader = self.writer = None
        self.mouse_ready = False

    async def close(self):
        if self.writer:
            self.writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(self.writer.wait_closed(), 1)
        self.reader = self.writer = None
        self.mouse_ready = False
        await stop(self.process)
        self.process = None
        try:
            if self.forward:
                with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                    await remote.adb("-s", self.serial, "forward", "--remove", f"tcp:{self.forward}")
            if self.serial:
                with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                    await remote.adb("disconnect", self.serial)
        finally:
            if self.relay:
                self.relay.close()
                self.relay.close_clients()
                await self.relay.wait_closed()
            self.relay = self.serial = self.forward = None

    async def ensure(self):
        async with self.lock:
            await self._ensure()

    async def _ensure(self):
        if self.writer and not self.writer.is_closing() and not self.reader.at_eof():
            return
        await self.close()
        try:
            self.relay = await asyncio.start_server(remote.relay, "127.0.0.1", 0)
            self.serial = f"127.0.0.1:{self.relay.sockets[0].getsockname()[1]}"
            await remote.adb("connect", self.serial)
            identity = await remote.adb("-s", self.serial, "shell", "-n", "getprop", "ro.serialno")
            if remote.EXPECTED_SERIAL not in identity.splitlines():
                raise RuntimeError("Projector identity mismatch")
            data = (remote.ROOT / "scrcpy-server").read_bytes()
            if hashlib.sha256(data).hexdigest() != SERVER_SHA:
                raise RuntimeError("Unexpected scrcpy server version")
            try:
                digest = await remote.adb("-s", self.serial, "shell", "-n", "sha256sum", SERVER)
            except RuntimeError:
                digest = ""
            if digest.split()[:1] != [SERVER_SHA]:
                upload = await asyncio.create_subprocess_exec(
                    remote.ADB, "-s", self.serial, "shell", "-T", f"cat > {SERVER}",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    async with asyncio.timeout(15):
                        for offset in range(0, len(data), 65536):
                            chunk = data[offset:offset + 65536]
                            await asyncio.sleep(len(chunk) / 2_000_000)
                            upload.stdin.write(chunk)
                            await upload.stdin.drain()
                        upload.stdin.close()
                        if await upload.wait():
                            raise RuntimeError("Server upload failed")
                finally:
                    await stop(upload)
                digest = await remote.adb("-s", self.serial, "shell", "-n", "sha256sum", SERVER)
                if digest.split()[:1] != [SERVER_SHA]:
                    raise RuntimeError("Server checksum mismatch")
            scid = f"{secrets.randbelow(2**31):08x}"
            self.forward = int((await remote.adb(
                "-s", self.serial, "forward", "tcp:0", f"localabstract:scrcpy_{scid}"
            )).strip())
            with (remote.STATE_DIR / "device.log").open("ab") as log:
                self.process = await asyncio.create_subprocess_exec(
                    remote.ADB, "-s", self.serial, "shell",
                    f"CLASSPATH={SERVER}", "app_process", "/", "com.genymobile.scrcpy.Server", "5.0.1",
                    f"scid={scid}", "tunnel_forward=true", "video=false", "audio=false",
                    "control=true", "power_on=false", "cleanup=false", "clipboard_autosync=false",
                    "send_device_meta=false", "send_dummy_byte=true", "log_level=warn",
                    stdout=log, stderr=log,
                )
            async with asyncio.timeout(12):
                while True:
                    try:
                        reader, writer = await asyncio.open_connection("127.0.0.1", self.forward)
                        try:
                            if await asyncio.wait_for(reader.readexactly(1), 1) != b"\0":
                                raise RuntimeError("Invalid control handshake")
                        except BaseException:
                            writer.close()
                            await writer.wait_closed()
                            raise
                        self.reader, self.writer = reader, writer
                        return
                    except (OSError, asyncio.IncompleteReadError, TimeoutError):
                        if self.process.returncode is not None:
                            raise RuntimeError("Control server stopped")
                        await asyncio.sleep(0.1)
        except BaseException:
            await self.close()
            raise

    async def press(self, name):
        packet = key_packet(name)
        if self.lock.locked():
            raise Busy()
        async with self.lock:
            await self._ensure()
            await self._write(packet)

    async def _write(self, packet):
        try:
            self.writer.write(packet)
            await self.writer.drain()
        except (OSError, RuntimeError):
            await self.close()
            # Never replay: the first event might already have reached Android.
            raise

    async def launch(self, name):
        packet = app_packet(name)
        if self.lock.locked():
            raise Busy()
        async with self.lock:
            await self._ensure()
            await self._write(packet)

    async def paste(self, text):
        packet = text_packet(text)
        if self.lock.locked():
            raise Busy()
        async with self.lock:
            await self._ensure()
            await self._write(packet)
            try:
                acknowledgement = await asyncio.wait_for(self.reader.readexactly(9), 3)
                if acknowledgement != struct.pack(">BQ", 1, 1):
                    raise RuntimeError("Unexpected paste acknowledgement")
            except (OSError, RuntimeError, TimeoutError, asyncio.IncompleteReadError):
                await self.close()
                raise

    async def mouse(self, dx=0, dy=0, wheel=0, button=None):
        packet = mouse_packet(dx, dy, wheel, button)
        if self.lock.locked():
            raise Busy()
        async with self.lock:
            await self._ensure()
            if not self.mouse_ready:
                name = b"Wanbo Browser Mouse"
                create = (struct.pack(">BHHHB", 12, 1, 0, 0, len(name)) + name
                          + struct.pack(">H", len(MOUSE_DESCRIPTOR)) + MOUSE_DESCRIPTOR)
                await self._write(create)
                self.mouse_ready = True
            if packet:
                await self._write(packet)

    async def screenshot(self):
        if self.snapshot_lock.locked():
            raise Busy()
        async with self.snapshot_lock:
            await self.ensure()
            directory = (await remote.adb("-s", self.serial, "shell", "-n",
                         "umask 077; mktemp -d /data/local/tmp/wanbo-web.XXXXXX")).strip()
            if not re.fullmatch(r"/data/local/tmp/wanbo-web\.[A-Za-z0-9]{6,}", directory):
                raise RuntimeError("Invalid capture directory")
            path = f"{directory}/screen.png"
            process = None
            data = bytearray()
            try:
                await remote.adb("-s", self.serial, "shell", "-n",
                                 f"chmod 700 {directory} && touch {path} && chmod 600 {path} && screencap -p {path}")
                process = await asyncio.create_subprocess_exec(
                    remote.ADB, "-s", self.serial, "exec-out", "cat", path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                async with asyncio.timeout(12):
                    while chunk := await process.stdout.read(65536):
                        data.extend(chunk)
                        if len(data) > 8_000_000:
                            raise RuntimeError("Screenshot too large")
                        await asyncio.sleep(len(chunk) / 2_000_000)
                    if await process.wait() or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise RuntimeError("Screenshot failed")
            finally:
                await stop(process)
                with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                    await remote.adb("-s", self.serial, "shell", "-n", f"rm -f {path}; rmdir {directory}")
            return bytes(data)


def make_handler(device, loop, token, origin=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; img-src blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed_host(self):
            expected = origin or f"http://127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == expected.split("://", 1)[1]

        def do_GET(self):
            if not self.allowed_host():
                return self.respond(403, b"{}")
            if self.path == "/healthz":
                return self.respond(200, b'{"ok":true}')
            if self.path != "/":
                return self.respond(404, b"{}")
            html = (remote.ROOT / "web_remote.html").read_text().replace("__TOKEN__", token)
            self.respond(200, html.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            expected = origin or f"http://127.0.0.1:{self.server.server_port}"
            if not self.allowed_host() or self.headers.get("Origin") != expected or self.headers.get("X-Wanbo-Token") != token:
                return self.respond(403, b"{}")
            try:
                self.connection.settimeout(3)
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= 16384 or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "application/json":
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
                if self.path == "/api/key":
                    name = body.get("key")
                    if not isinstance(name, str) or name not in KEYS:
                        raise ValueError()
                    operation = device.press(name)
                elif self.path == "/api/connect":
                    operation = device.ensure()
                elif self.path == "/api/screenshot":
                    operation = device.screenshot()
                elif self.path == "/api/text":
                    text = body.get("text")
                    text_packet(text)
                    operation = device.paste(text)
                elif self.path == "/api/app":
                    app = body.get("app")
                    app_packet(app)
                    operation = device.launch(app)
                elif self.path == "/api/mouse":
                    kind = body.get("kind")
                    if kind not in ("enable", "move", "click"):
                        raise ValueError()
                    dx, dy, wheel = (body.get(k, 0) for k in ("dx", "dy", "wheel"))
                    button = body.get("button") if kind == "click" else None
                    if kind == "click" and button not in ("left", "right"):
                        raise ValueError()
                    mouse_packet(dx, dy, wheel, button)
                    operation = device.mouse(dx, dy, wheel, button)
                else:
                    return self.respond(404, b"{}")
            except (ValueError, OSError):
                return self.respond(400, b"{}")
            future = asyncio.run_coroutine_threadsafe(operation, loop)
            try:
                result = future.result(timeout=35)
                self.respond(200, result if isinstance(result, bytes) else b'{"ok":true}',
                             "image/png" if isinstance(result, bytes) else "application/json")
            except Busy:
                self.respond(409, b'{"error":"busy"}')
            except Exception as error:
                future.cancel()
                print(f"{self.path}: {type(error).__name__}: {error}", flush=True)
                self.respond(502, b'{"error":"connection"}')
    return Handler


async def main():
    os.umask(0o077)
    remote.STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    device = Device()
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, finished.set)
    http = ThreadingHTTPServer((remote.BIND_HOST, PORT), make_handler(
        device, loop, secrets.token_hex(24), origin=remote.WEB_ORIGIN))
    http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    print(f"Wanbo browser remote: {remote.WEB_ORIGIN}", flush=True)
    try:
        await finished.wait()
    finally:
        await asyncio.to_thread(http.shutdown)
        http.server_close()
        await device.close()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import http.client
import importlib.util
import json
import os
import struct
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PROJECTOR_HOST", "192.0.2.10")
os.environ.setdefault("PROJECTOR_SERIAL", "example-serial")
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("web_remote", ROOT / "web_remote.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class PacketTest(unittest.TestCase):
    def test_app_launch_is_whitelisted(self):
        packet = web.app_packet("jellyfin")
        self.assertEqual(packet[0], 16)
        self.assertEqual(packet[1], len(packet[2:]))
        self.assertEqual(packet[2:], b"org.jellyfin.androidtv")
        with self.assertRaises(ValueError):
            web.app_packet("com.android.settings")

    def test_unicode_paste_packet(self):
        text = "Привіт, світ 👋"
        packet = web.text_packet(text)
        self.assertEqual(struct.unpack(">BQBI", packet[:14]), (9, 1, 1, len(text.encode())))
        self.assertEqual(packet[14:].decode(), text)
        for invalid in ("", "x" * 4097, "a\0b"):
            with self.assertRaises(ValueError):
                web.text_packet(invalid)

    def test_relative_mouse_move_then_click(self):
        packet = web.mouse_packet(250, -1, 1, "left")
        frames = [packet[i:i + 10] for i in range(0, len(packet), 10)]
        self.assertEqual(len(frames), 4)
        reports = []
        for frame in frames:
            self.assertEqual(struct.unpack(">BHH", frame[:5]), (13, 1, 5))
            reports.append(struct.unpack("<Bbbbb", frame[5:]))
        self.assertEqual(sum(r[1] for r in reports), 250)
        self.assertEqual(sum(r[2] for r in reports), -1)
        self.assertEqual(sum(r[3] for r in reports), 1)
        self.assertEqual([r[0] for r in reports], [0, 0, 1, 0])
        for values in ((99999, 0, 0, None), (True, 0, 0, None), (0, 0, 0, "power")):
            with self.assertRaises(ValueError):
                web.mouse_packet(*values)

    def test_one_down_and_up_without_repeat(self):
        packet = web.key_packet("right")
        self.assertEqual(len(packet), 28)
        self.assertEqual(struct.unpack(">BBIII", packet[:14]), (0, 0, 22, 0, 0))
        self.assertEqual(struct.unpack(">BBIII", packet[14:]), (0, 1, 22, 0, 0))

    def test_only_allowed_buttons(self):
        with self.assertRaises(ValueError):
            web.key_packet("power")
        with self.assertRaises(ValueError):
            web.key_packet("home; reboot")


class InputTest(unittest.IsolatedAsyncioTestCase):
    async def test_paste_waits_for_ack_and_never_replays(self):
        for acknowledgement in (struct.pack(">BQ", 1, 1), b"invalid!!"):
            device = web.Device()
            device._ensure = AsyncMock()
            device.close = AsyncMock()
            device.writer = Mock(write=Mock(), drain=AsyncMock())
            device.reader = Mock(readexactly=AsyncMock(return_value=acknowledgement))
            if acknowledgement == struct.pack(">BQ", 1, 1):
                await device.paste("Привіт 👋")
                device.close.assert_not_awaited()
            else:
                with self.assertRaises(RuntimeError):
                    await device.paste("Привіт 👋")
                device.close.assert_awaited_once()
            device.writer.write.assert_called_once_with(web.text_packet("Привіт 👋"))


ACTIVITIES = """ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
    * TaskRecord example
      intent={act=android.intent.action.VIEW dat=https://example.test/video cmp=org.xbmc.kodi/.Main}
"""
NEUTRAL_ACTIVITIES = """ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
      intent={act=android.intent.action.MAIN cat=[android.intent.category.LAUNCHER] cmp=org.xbmc.kodi/.Main}
"""
SESSIONS = """MEDIA SESSION SERVICE (dumpsys media_session)
  Sessions Stack - have 2 sessions:
    XBMC_session org.xbmc.kodi/XBMC_session (userId=0)
      ownerPid=123, ownerUid=10009, userId=0
      package=org.xbmc.kodi
      active=true
      state=PlaybackState {state=2, position=1000, speed=0.0}
    Bluetooth session
      ownerPid=456, ownerUid=1002, userId=0
      package=com.android.bluetooth
      active=true
      state=PlaybackState {state=3, position=0, speed=1.0}
"""


class KodiTest(unittest.IsolatedAsyncioTestCase):
    async def run_kodi(self, *, repair=False, activities=ACTIVITIES, sessions=SESSIONS,
                       installed=True, launch_succeeds=True):
        device = web.Device()
        device.serial = "test"
        device._ensure = AsyncMock()
        commands = []

        async def adb(*args):
            nonlocal activities, sessions
            command = args[-1]
            commands.append(command)
            if command == "pm path org.xbmc.kodi || true":
                return "package:/data/app/example/base.apk\n" if installed else ""
            if command == "dumpsys activity activities":
                return activities
            if command == "dumpsys media_session":
                return sessions
            if command == "am force-stop org.xbmc.kodi":
                return ""
            if command.startswith("am start "):
                self.assertIn("-f 0x10008000", command)
                self.assertIn("-n org.xbmc.kodi/.Splash", command)
                self.assertNotIn(" -d ", command)
                if launch_succeeds:
                    activities = NEUTRAL_ACTIVITIES
                    sessions = SESSIONS.replace("active=true", "active=false", 1).replace(
                        "state=PlaybackState {state=2, position=1000, speed=0.0}", "state=null")
                return "Status: ok" if launch_succeeds else "Error: activity not found"
            self.fail(f"Unexpected device command: {command}")

        self.assertTrue(callable(getattr(device, "kodi", None)), "Kodi check/repair operation is missing")
        with patch.object(web.remote, "adb", adb):
            result = await device.kodi(repair=repair)
        return result, commands

    async def test_check_detects_media_uri_without_mutating_android(self):
        result, commands = await self.run_kodi()
        self.assertEqual(result, {"state": "media", "repaired": False})
        self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_repair_replaces_media_task_with_neutral_task(self):
        result, commands = await self.run_kodi(repair=True)
        self.assertEqual(result, {"state": "ready", "repaired": True})
        self.assertEqual(commands.count("am force-stop org.xbmc.kodi"), 1)

    async def test_repair_is_idempotent_for_neutral_task(self):
        result, commands = await self.run_kodi(repair=True, activities=NEUTRAL_ACTIVITIES)
        self.assertEqual(result, {"state": "ready", "repaired": False})
        self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_inactive_session_without_playback_is_a_clean_home_screen(self):
        sessions = SESSIONS.replace("active=true", "active=false", 1).replace(
            "state=PlaybackState {state=2, position=1000, speed=0.0}", "state=null")
        result, commands = await self.run_kodi(repair=True, activities=NEUTRAL_ACTIVITIES, sessions=sessions)
        self.assertEqual(result, {"state": "ready", "repaired": False})
        self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_repair_initializes_missing_task(self):
        result, _ = await self.run_kodi(repair=True, activities="""ACTIVITY MANAGER ACTIVITIES
      intent={act=android.intent.action.MAIN cmp=example.launcher/.Home}
""")
        self.assertEqual(result, {"state": "ready", "repaired": True})

    async def test_explicit_empty_session_stack_is_safe(self):
        result, _ = await self.run_kodi(repair=True, sessions="""MEDIA SESSION SERVICE
  Sessions Stack - have 0 sessions:
""")
        self.assertEqual(result, {"state": "ready", "repaired": True})

    async def test_repair_refuses_active_or_unknown_playback(self):
        for state in (3, 4, 5, 6, 8, 99):
            with self.subTest(state=state):
                sessions = SESSIONS.replace("{state=2,", f"{{state={state},", 1)
                result, commands = await self.run_kodi(repair=True, sessions=sessions)
                self.assertEqual(result, {"state": "playing", "repaired": False})
                self.assertFalse(any(c.startswith("am ") for c in commands))
        for sessions in ("", SESSIONS.replace("state=PlaybackState {state=2, position=1000, speed=0.0}", "state=null")):
            result, commands = await self.run_kodi(repair=True, sessions=sessions)
            self.assertEqual(result, {"state": "unknown", "repaired": False})
            self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_repair_refuses_unknown_activity_format_or_missing_app(self):
        for kwargs, state in (({"activities": "permission denied"}, "unknown"),
                              ({"installed": False}, "not_installed")):
            result, commands = await self.run_kodi(repair=True, **kwargs)
            self.assertEqual(result, {"state": state, "repaired": False})
            self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_incomplete_or_changed_dump_format_never_authorizes_restart(self):
        cases = [
            {"sessions": "MEDIA SESSION SERVICE (dumpsys media_session)\n"},
            {"sessions": SESSIONS.replace("package=org.xbmc.kodi", "package=org.xbmc.kodi tag=Kodi").replace("{state=2,", "{state=3,", 1)},
            {"sessions": SESSIONS.replace("package=org.xbmc.kodi", "package=org.xbmc.kodi,tag=Kodi").replace("{state=2,", "{state=3,", 1)},
            {"sessions": SESSIONS.replace("have 2 sessions", "have 3 sessions")},
            {"sessions": SESSIONS.replace("ownerPid=123", "ownerProcess=123")},
            {"sessions": SESSIONS.replace("      active=true\n", "", 1)},
            {"activities": "ACTIVITY MANAGER ACTIVITIES\n"},
            {"activities": ACTIVITIES.replace("intent={", "baseIntent={") + "      intent={act=android.intent.action.MAIN cmp=example.launcher/.Home}\n"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result, commands = await self.run_kodi(repair=True, **kwargs)
                self.assertEqual(result, {"state": "unknown", "repaired": False})
                self.assertFalse(any(c.startswith("am ") for c in commands))

    async def test_repair_verifies_result_instead_of_trusting_am_exit_status(self):
        with self.assertRaises(RuntimeError):
            await self.run_kodi(repair=True, launch_succeeds=False)


class ScreenshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_private_capture_is_removed_after_success(self):
        device = web.Device()
        device.serial = "test"
        device.ensure = AsyncMock()
        process = AsyncMock()
        process.returncode = 0
        process.stdout.read.side_effect = [b"\x89PNG\r\n\x1a\n", b""]
        process.wait.return_value = 0
        adb = AsyncMock(side_effect=["/data/local/tmp/wanbo-web.ABC123\n", "", ""])
        with patch.object(web.remote, "adb", adb), \
             patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            await device.screenshot()
        commands = [" ".join(call.args) for call in adb.await_args_list]
        self.assertIn("mktemp -d", commands[0])
        self.assertIn("chmod 700", commands[1])
        self.assertLess(commands[1].index("chmod 600"), commands[1].index("screencap"))
        self.assertIn("rm -f /data/local/tmp/wanbo-web.ABC123/screen.png", commands[-1])

    async def test_private_capture_is_removed_after_capture_error(self):
        device = web.Device()
        device.serial = "test"
        device.ensure = AsyncMock()
        adb = AsyncMock(side_effect=["/data/local/tmp/wanbo-web.ABC123\n", RuntimeError("capture failed"), ""])
        with patch.object(web.remote, "adb", adb):
            with self.assertRaises(RuntimeError):
                await device.screenshot()
        self.assertIn("rm -f /data/local/tmp/wanbo-web.ABC123/screen.png", " ".join(adb.await_args_list[-1].args))


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.loop_thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.loop_thread.start()
        cls.device = type("FakeDevice", (), {})()
        cls.device.press = AsyncMock(return_value=None)
        cls.device.ensure = AsyncMock(return_value=None)
        cls.device.screenshot = AsyncMock(return_value=b"fake-png")
        cls.device.paste = AsyncMock(return_value=None)
        cls.device.mouse = AsyncMock(return_value=None)
        cls.device.launch = AsyncMock(return_value=None)
        cls.device.kodi = AsyncMock(return_value={"state": "ready", "repaired": False})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cls.device, cls.loop, "test-token"))
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.loop_thread.join()
        cls.loop.close()

    def request(self, path, body=None, *, origin=None, token="test-token", host=None, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Content-Type": "application/json", "X-Wanbo-Token": token,
                   "Origin": origin or self.origin, "Host": host or self.origin.removeprefix("http://")}
        connection.request(method, path, json.dumps(body or {}), headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_rejects_cross_origin_and_missing_token(self):
        before = self.device.press.await_count
        for headers in ({"origin": "https://example.com"}, {"token": ""}, {"host": "example.com"}):
            self.assertEqual(self.request("/api/key", {"key": "home"}, **headers)[0], 403)
        self.assertEqual(self.device.press.await_count, before)

    def test_single_key_reaches_device_once(self):
        self.assertEqual(self.request("/api/key", {"key": "home"})[0], 200)
        self.device.press.assert_awaited_with("home")

    def test_rejects_arbitrary_command(self):
        before = self.device.press.await_count
        self.assertEqual(self.request("/api/key", {"key": "reboot"})[0], 400)
        self.assertEqual(self.device.press.await_count, before)

    def test_unicode_text_and_mouse_endpoints(self):
        text = "Тестовий текст"
        self.assertEqual(self.request("/api/text", {"text": text})[0], 200)
        self.device.paste.assert_awaited_with(text)
        self.assertEqual(self.request("/api/mouse", {"kind": "move", "dx": 10, "dy": -4, "wheel": 0})[0], 200)
        self.device.mouse.assert_awaited_with(10, -4, 0, None)
        self.assertEqual(self.request("/api/mouse", {"kind": "click", "button": "left"})[0], 200)
        self.device.mouse.assert_awaited_with(0, 0, 0, "left")

    def test_text_and_mouse_still_require_trusted_origin(self):
        for path, body in (("/api/text", {"text":"abc"}), ("/api/mouse", {"kind":"enable"})):
            self.assertEqual(self.request(path, body, origin="https://example.com")[0], 403)
        self.assertEqual(self.request("/api/text", {"text":""})[0], 400)
        self.assertEqual(self.request("/api/mouse", {"kind":"move", "dx":"1; reboot"})[0], 400)

    def test_app_endpoint_rejects_arbitrary_package(self):
        self.assertEqual(self.request("/api/app", {"app":"smarttube"})[0], 200)
        self.device.launch.assert_awaited_with("smarttube")
        before = self.device.launch.await_count
        self.assertEqual(self.request("/api/app", {"app":"com.android.settings"})[0], 400)
        self.assertEqual(self.device.launch.await_count, before)

    def test_get_cannot_send_commands(self):
        self.assertEqual(self.request("/api/key", method="GET")[0], 404)

    def test_kodi_actions_return_state_and_require_valid_post(self):
        status, data, _ = self.request("/api/kodi", {"action": "check"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"state": "ready", "repaired": False})
        self.device.kodi.assert_awaited_with(repair=False)
        self.assertEqual(self.request("/api/kodi", {"action": "repair"})[0], 200)
        self.device.kodi.assert_awaited_with(repair=True)
        before = self.device.kodi.await_count
        for body, kwargs, expected in (({"action": "repair; reboot"}, {}, 400),
                                       ({"action": "repair"}, {"token": ""}, 403),
                                       ({"action": "repair"}, {"origin": "https://example.test"}, 403),
                                       ({"action": "repair"}, {"method": "GET"}, 404)):
            self.assertEqual(self.request("/api/kodi", body, **kwargs)[0], expected)
        self.assertEqual(self.device.kodi.await_count, before)

    def test_screenshot_is_explicit_and_not_cached(self):
        before = self.device.screenshot.await_count
        self.assertEqual(self.request("/", method="GET")[0], 200)
        self.assertEqual(self.device.screenshot.await_count, before)
        status, data, headers = self.request("/api/screenshot")
        self.assertEqual((status, data), (200, b"fake-png"))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_health_check_does_not_contact_projector(self):
        before = self.device.ensure.await_count
        self.assertEqual(self.request("/healthz", method="GET")[0], 200)
        self.assertEqual(self.device.ensure.await_count, before)

    def test_host_port_mapping_uses_configured_origin(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(
            self.device, self.loop, "test-token", origin="http://127.0.0.1:8877"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request("GET", "/", headers={"Host":"127.0.0.1:8877"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

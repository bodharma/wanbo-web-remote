import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConfigTest(unittest.TestCase):
    def load(self, **overrides):
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PROJECTOR_HOST", "PROJECTOR_PORT", "PROJECTOR_SERIAL", "WEB_ORIGIN", "PORT", "BIND_HOST")}
        environment.update(PROJECTOR_HOST="192.0.2.10", PROJECTOR_SERIAL="example-serial")
        environment.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import remote,json;print(json.dumps([remote.TARGET,remote.EXPECTED_SERIAL,remote.WEB_ORIGIN]))"],
            cwd=ROOT, env=environment, text=True, capture_output=True,
        )

    def test_device_configuration_comes_from_environment(self):
        result = self.load(PROJECTOR_PORT="5556", WEB_ORIGIN="http://127.0.0.1:8877")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [["192.0.2.10", 5556], "example-serial", "http://127.0.0.1:8877"])

    def test_empty_identity_and_invalid_ports_are_rejected(self):
        for overrides in ({"PROJECTOR_HOST":""}, {"PROJECTOR_SERIAL":""}, {"PROJECTOR_PORT":"0"}, {"PROJECTOR_PORT":"65536"}):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.load(**overrides).returncode, 0)

    def test_origin_must_be_one_absolute_http_origin(self):
        for origin in ("http://user:password@example.test", "https://example.test/path", "ftp://example.test", "https://example.test?query=1"):
            with self.subTest(origin=origin):
                self.assertNotEqual(self.load(WEB_ORIGIN=origin).returncode, 0)


if __name__ == "__main__":
    unittest.main()

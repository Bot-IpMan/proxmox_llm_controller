import os
import sys
import unittest
from pathlib import Path


# Ensure the repository root is importable so that ``import controller``
# resolves to the package in this project rather than relying on the test
# directory being a package itself.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from controller import app


ENV_VARS = [
    "BLISS_ADB_SERIAL",
    "BLISS_ADB_ADDRESS",
    "BLISS_ADB_HOST",
    "BLISS_ADB_PORT",
]


class BlissSerialResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = {name: os.environ.get(name) for name in ENV_VARS}
        for name in ENV_VARS:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self._orig_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_request_host_overrides_env_address(self) -> None:
        os.environ["BLISS_ADB_ADDRESS"] = "192.168.1.218:5555"
        spec = app.BlissADBConnectSpec(host="192.168.1.220")

        resolved = app._resolve_bliss_address(spec)

        self.assertEqual(resolved, "192.168.1.220:5555")

    def test_request_port_is_applied(self) -> None:
        os.environ["BLISS_ADB_ADDRESS"] = "192.168.1.218:5555"
        spec = app.BlissADBConnectSpec(host="192.168.1.220", port=5560)

        resolved = app._resolve_bliss_address(spec)

        self.assertEqual(resolved, "192.168.1.220:5560")

    def test_env_address_used_when_no_overrides(self) -> None:
        os.environ["BLISS_ADB_ADDRESS"] = "192.168.1.218:5555"
        spec = app.BlissADBConnectSpec()

        resolved = app._resolve_bliss_address(spec)

        self.assertEqual(resolved, "192.168.1.218:5555")

    def test_serial_without_host_uses_env_host(self) -> None:
        os.environ["BLISS_ADB_SERIAL"] = "RQCT30W45KM"
        os.environ["BLISS_ADB_HOST"] = "192.168.1.218"
        spec = app.BlissADBConnectSpec()

        resolved = app._resolve_bliss_address(spec)

        self.assertEqual(resolved, "192.168.1.218:5555")


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pmm_mcp.config import load_clients


class ConfigTests(unittest.TestCase):
    def test_loads_multiple_clients_using_environment_references(self) -> None:
        config = {
            "clients": {
                "cliente-a": {
                    "base_url": "https://a.example",
                    "auth": {
                        "username_env": "A_USER",
                        "password_env": "A_PASS",
                    },
                },
                "cliente-b": {
                    "base_url": "https://b.example:8443",
                    "auth": {"token_env": "B_TOKEN"},
                },
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clients.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch.dict(
                os.environ,
                {"A_USER": "admin", "A_PASS": "secret", "B_TOKEN": "glsa_test"},
                clear=False,
            ):
                clients = load_clients(path)

        self.assertEqual(set(clients), {"cliente-a", "cliente-b"})
        self.assertEqual(clients["cliente-a"].username, "admin")
        self.assertEqual(clients["cliente-a"].password, "secret")
        self.assertEqual(clients["cliente-b"].token, "glsa_test")
        self.assertEqual(clients["cliente-a"].api_base_url, "https://a.example/v1")


if __name__ == "__main__":
    unittest.main()

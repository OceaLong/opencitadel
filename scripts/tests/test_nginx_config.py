# ruff: noqa: PT009 -- standalone stdlib unittest suite, no pytest dependency
"""Render the gateway configuration without starting Nginx or Docker."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class NginxConfigTests(unittest.TestCase):
    def render(self, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "default.conf"
            script = temporary / "render.sh"
            script.write_text(
                (ROOT / "deploy/nginx/generate-config.sh")
                .read_text()
                .replace("/etc/nginx/conf.d/default.conf", str(config))
                .replace("/etc/nginx/templates-src", str(ROOT / "deploy/nginx/templates"))
            )
            envsubst = temporary / "envsubst"
            envsubst.write_text(
                f"#!{sys.executable}\n"
                "import os, re, sys\n"
                "text = sys.stdin.read()\n"
                "for name in re.findall(r'\\$\\{([^}]+)\\}', sys.argv[1]):\n"
                "    text = text.replace('${' + name + '}', os.environ.get(name, ''))\n"
                "sys.stdout.write(text)\n"
            )
            envsubst.chmod(0o755)
            environment = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "FRONTEND_BASE_URL",
                    "OPENCITADEL_DOMAIN",
                    "HTTPS_ENABLED",
                    "NGINX_HTTPS_PORT",
                }
            }
            environment.update(PATH=f"{temporary}:{environment['PATH']}", **overrides)
            result = subprocess.run(
                ["sh", str(script)], env=environment, capture_output=True, text=True, check=False
            )
            return result, config.read_text() if config.exists() else ""

    def test_https_redirect_uses_the_public_origin_and_keeps_request_variables(self):
        result, config = self.render(
            HTTPS_ENABLED="true",
            OPENCITADEL_DOMAIN="citadel.example.test",
            FRONTEND_BASE_URL="https://citadel.example.test:9443/",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("return 301 https://citadel.example.test:9443$request_uri;", config)
        self.assertIn("proxy_pass http://$api_upstream:8000$request_uri;", config)
        self.assertIn("/live/citadel.example.test/fullchain.pem", config)

    def test_legacy_domain_and_https_port_remain_supported(self):
        result, config = self.render(
            HTTPS_ENABLED="true",
            OPENCITADEL_DOMAIN="citadel.example.test",
            NGINX_HTTPS_PORT="9443",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("return 301 https://citadel.example.test:9443$request_uri;", config)

    def test_invalid_https_origin_cannot_enter_nginx_configuration(self):
        for origin in ("http://citadel.example.test", "https://citadel.example.test;}"):
            with self.subTest(origin=origin):
                result, config = self.render(
                    HTTPS_ENABLED="true",
                    OPENCITADEL_DOMAIN="citadel.example.test",
                    FRONTEND_BASE_URL=origin,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("valid HTTPS FRONTEND_BASE_URL", result.stderr)
                self.assertEqual(config, "")

    def test_plain_http_does_not_need_a_certificate_or_https_origin(self):
        result, config = self.render(OPENCITADEL_DOMAIN="localhost")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("server_name localhost;", config)
        self.assertNotIn("ssl_certificate", config)
        self.assertIn("proxy_set_header Host              $host;", config)


if __name__ == "__main__":
    unittest.main()

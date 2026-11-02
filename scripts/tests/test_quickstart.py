# ruff: noqa: PT009 -- standalone stdlib unittest suite, no pytest dependency
"""Exercise initialization and the launcher with a Docker substitute."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_KEYS = [
    "API_KEY_SECRET",
    "AUDIT_SIGNING_KEY",
    "JWT_SECRET",
    "SESSION_SECRET",
    "SANDBOX_BROKER_TOKEN",
    "SANDBOX_TOKEN_SEED",
    "PUBLIC_CURSOR_SECRET",
    "DATABASE_AUTHORIZATION_SIGNING_SECRET",
    "METRICS_TOKEN",
    "POSTGRES_ADMIN_PASSWORD",
    "POSTGRES_MIGRATION_PASSWORD",
    "POSTGRES_PASSWORD",
    "POSTGRES_KERNEL_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_SECRET_KEY",
    "OPS_COLLECTOR_TOKEN",
    "OPS_ACTUATOR_TOKEN",
    "BOOTSTRAP_ADMIN_PASSWORD",
]
DOCKER = r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_LOG'], 'a') as output:
    output.write(json.dumps({'args': args, 'env': {key: os.getenv(key) for key in (
        'FRONTEND_BASE_URL', 'NGINX_PORT', 'NGINX_HTTPS_PORT', 'HTTPS_ENABLED',
        'OPENCITADEL_DOMAIN', 'SANDBOX_NETWORK', 'DOCKER_SOCK_GID')}}) + '\n')
if args[:2] == ['context', 'inspect']:
    print(os.getenv('FAKE_DOCKER_ENDPOINT', 'unix:///var/run/docker.sock'))
if 'config' in args and '--environment' in args:
    print(os.getenv('FAKE_RESOLVED_ENVIRONMENT', ''))
if args == ['info'] and os.getenv('FAIL_DAEMON'):
    sys.exit(1)
if ('up' in args and os.getenv('FAIL_UP')) or ('build' in args and os.getenv('FAIL_BUILD')):
    sys.exit(1)
if 'exec' in args and os.getenv('FAIL_SEED'):
    sys.exit(1)
"""


class QuickstartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for script in ("quickstart.sh", "configure-local.sh"):
            shutil.copyfile(ROOT / "scripts" / script, self.root / "scripts" / script)
        shutil.copyfile(ROOT / ".env.example", self.root / ".env.example")
        self.log = self.root / "docker.jsonl"
        self.write_command("docker", f"#!{sys.executable}\n{DOCKER}")
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key not in SECRET_KEYS
            and key
            not in {
                "COMPOSE_PROJECT_NAME",
                "OPENCITADEL_ENV_FILE",
                "COMPOSE_PROFILES",
                "FRONTEND_BASE_URL",
                "NGINX_PORT",
                "NGINX_HTTPS_PORT",
                "OPENCITADEL_DOMAIN",
                "HTTPS_ENABLED",
                "SANDBOX_NETWORK",
                "BOOTSTRAP_ADMIN_EMAIL",
                "DOCKER_HOST",
                "DOCKER_SOCK_GID",
                "STORAGE_PROVIDER",
                "ENV",
                "QUICKSTART_WAIT_TIMEOUT_SECONDS",
            }
        }
        self.environment.update(PATH=f"{self.bin}:{os.environ['PATH']}", DOCKER_LOG=str(self.log))

    def write_command(self, name, body):
        target = self.bin / name
        target.write_text(body)
        target.chmod(0o755)

    def run_script(self, script="quickstart.sh", *arguments, **updates):
        return subprocess.run(
            ["bash", str(self.root / "scripts" / script), *arguments],
            cwd=self.root,
            env={**self.environment, **updates},
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def stored_values(self):
        return {
            key: value.strip("'")
            for key, value in re.findall(
                r"^([A-Z][A-Z0-9_]*)=(.*)$", self.env.read_text(), re.MULTILINE
            )
        }

    @property
    def env(self):
        return self.root / ".env"

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def compose_call(self, operation):
        return next(call for call in self.calls() if operation in call["args"])

    def test_first_start_generates_private_distinct_credentials_and_reports_login(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.stored_values()
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        generated = [values[key] for key in SECRET_KEYS]
        self.assertEqual(len(set(generated)), len(SECRET_KEYS))
        for value in generated:
            self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertEqual(values["OPENCITADEL_ENV_FILE"], ".env")
        self.assertEqual(values["BOOTSTRAP_ADMIN_EMAIL"], "admin@example.com")
        self.assertIn(values["BOOTSTRAP_ADMIN_PASSWORD"], result.stdout)
        self.assertIn("URL: http://localhost:8088", result.stdout)
        up = self.compose_call("up")
        self.assertIn("--wait", up["args"])
        self.assertEqual(up["env"]["NGINX_PORT"], "8088")
        self.assertEqual(up["env"]["HTTPS_ENABLED"], "false")
        self.assertEqual(up["env"]["SANDBOX_NETWORK"], "opencitadel_opencitadel-sandbox-network")
        self.assertIn("--project-name", up["args"])

    def test_repeated_start_preserves_file_and_hides_stored_password(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        contents = self.env.read_bytes()
        password = self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"]
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.env.read_bytes(), contents)
        self.assertNotIn(password, second.stdout + second.stderr)

    def test_unavailable_daemon_fails_before_configuration_or_build(self):
        result = self.run_script(FAIL_DAEMON="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker daemon is unavailable", result.stderr)
        self.assertFalse(self.env.exists())
        self.assertFalse(
            any("build" in call["args"] or "up" in call["args"] for call in self.calls())
        )

    def test_existing_dotenv_remains_data_and_preserves_structured_values(self):
        marker = self.root / "executed"
        contents = (
            "API_KEY_SECRET='existing-secret'\n"
            "COMPOSE_PROJECT_NAME=existing-project\nCOMPOSE_PROFILES=\n"
            "FRONTEND_BASE_URL='http://localhost:9000' # scalar comment\n"
            'SANDBOX_LABELS={"quote":"a \\"word\\"", "dollar":"$HOME", "hash":"#"}\n'
            f"NOT_SHELL=$(touch {marker})\n"
            "BOOTSTRAP_ADMIN_PASSWORD='quoted $dollar # hash and spaces'\n"
        )
        self.env.write_text(contents)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.env.read_text(), contents)
        up = self.compose_call("up")
        self.assertEqual(up["env"]["NGINX_PORT"], "9000")
        self.assertEqual(
            up["env"]["SANDBOX_NETWORK"], "existing-project_opencitadel-sandbox-network"
        )
        self.assertNotIn("--profile", up["args"])
        self.assertNotIn("quoted $dollar", result.stdout)

    def test_unused_copied_template_is_not_regenerated(self):
        contents = (self.root / ".env.example").read_bytes()
        self.env.write_bytes(contents)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Existing .env has no installation secrets", result.stderr)
        self.assertEqual(self.env.read_bytes(), contents)
        self.assertFalse(any("build" in call["args"] for call in self.calls()))

    def test_template_file_selector_is_repaired_without_replacing_secret(self):
        self.env.write_text("OPENCITADEL_ENV_FILE=.env.example\nAPI_KEY_SECRET='persisted'\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.env.read_text(), "OPENCITADEL_ENV_FILE=.env\nAPI_KEY_SECRET='persisted'\n"
        )

    def test_custom_configuration_selects_both_interpolation_and_service_environment(self):
        self.env.write_text("OPENCITADEL_ENV_FILE=custom.env\n")
        custom = self.root / "custom.env"
        custom.write_text("API_KEY_SECRET=persisted\nFRONTEND_BASE_URL=http://localhost:9188\n")
        original = custom.read_bytes()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(custom.read_bytes(), original)
        up = self.compose_call("up")
        self.assertEqual(up["args"][up["args"].index("--env-file") + 1], str(custom))
        self.assertEqual(up["env"]["NGINX_PORT"], "9188")

    def test_missing_selected_configuration_is_not_created(self):
        result = self.run_script(OPENCITADEL_ENV_FILE="missing.env")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Selected configuration does not exist", result.stderr)
        self.assertFalse(self.env.exists())

    def test_existing_scalar_interpolation_uses_compose_without_logging_environment(self):
        self.env.write_text(
            "API_KEY_SECRET=preserved\nINSTANCE_NAME=interpolated-project\n"
            "COMPOSE_PROJECT_NAME=${INSTANCE_NAME}\nOPENCITADEL_DOMAIN=app.example.test\n"
            "FRONTEND_BASE_URL=https://${OPENCITADEL_DOMAIN}:9443\n"
        )
        original = self.env.read_bytes()
        secret = "private-environment-secret-must-not-be-printed"
        result = self.run_script(
            FAKE_RESOLVED_ENVIRONMENT=(
                "COMPOSE_PROJECT_NAME=interpolated-project\n"
                "OPENCITADEL_DOMAIN=app.example.test\n"
                "FRONTEND_BASE_URL=https://app.example.test:9443\n"
                f"API_KEY_SECRET={secret}\n"
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env.read_bytes(), original)
        self.assertIn("URL: https://app.example.test:9443", result.stdout)
        self.assertNotIn(secret, result.stdout + result.stderr)
        up = self.compose_call("up")
        self.assertIn("interpolated-project", up["args"])
        self.assertEqual(up["env"]["NGINX_HTTPS_PORT"], "9443")

    def test_demo_merges_profiles_and_keeps_the_change_idempotent(self):
        self.env.write_text("API_KEY_SECRET=preserved\nCOMPOSE_PROFILES=custom,local\n")
        result = self.run_script("quickstart.sh", "--demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stored_values()["COMPOSE_PROFILES"], "custom,local,patrol,demo")
        contents = self.env.read_bytes()
        second = self.run_script("quickstart.sh", "--demo")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.env.read_bytes(), contents)
        self.assertTrue(any("app.seed_demo" in call["args"] for call in self.calls()))

    def test_empty_shell_profiles_disable_local_and_persist_on_first_initialization(self):
        result = self.run_script(COMPOSE_PROFILES="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stored_values()["COMPOSE_PROFILES"], "")
        self.assertNotIn("--profile", self.compose_call("up")["args"])
        self.env.write_text(
            self.env.read_text().replace("COMPOSE_PROFILES=", "COMPOSE_PROFILES=local")
        )
        self.log.write_text("")
        result = self.run_script(COMPOSE_PROFILES="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stored_values()["COMPOSE_PROFILES"], "local")
        self.assertNotIn("--profile", self.compose_call("up")["args"])

    def test_readiness_failure_exits_with_diagnostics_and_keeps_credentials(self):
        result = self.run_script(FAIL_UP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Startup did not reach readiness", result.stderr)
        self.assertNotIn("OpenCitadel is ready", result.stdout)
        self.assertNotIn(
            self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"], result.stdout + result.stderr
        )
        self.assertTrue(any("logs" in call["args"] for call in self.calls()))
        contents = self.env.read_bytes()
        retry = self.run_script()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn(self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"], retry.stdout)
        self.assertNotIn("initial-admin-login-pending", self.env.read_text())
        self.assertEqual(
            self.env.read_bytes(),
            b"\n".join(
                line
                for line in contents.split(b"\n")
                if not line.startswith(b"# opencitadel-initial-admin-login-pending: ")
            ),
        )

    def test_build_or_seed_failure_cannot_report_success(self):
        for failure, arguments in (("FAIL_BUILD", ()), ("FAIL_SEED", ("--demo",))):
            with self.subTest(failure=failure):
                result = self.run_script("quickstart.sh", *arguments, **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("OpenCitadel is ready", result.stdout)

    def test_entropy_falls_back_to_urandom_and_supplied_password_is_not_logged(self):
        self.write_command("openssl", "#!/bin/sh\nexit 1\n")
        result = self.run_script(BOOTSTRAP_ADMIN_PASSWORD="user-supplied password $ #")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.stored_values()
        self.assertRegex(values["API_KEY_SECRET"], r"^[0-9a-f]{64}$")
        self.assertEqual(values["BOOTSTRAP_ADMIN_PASSWORD"], "user-supplied password $ #")
        self.assertNotIn("user-supplied password", result.stdout + result.stderr)

    def test_https_origin_derives_ports_and_explicit_overrides_win(self):
        result = self.run_script(FRONTEND_BASE_URL="https://app.example.test:9443/")
        self.assertEqual(result.returncode, 0, result.stderr)
        up = self.compose_call("up")
        self.assertEqual(up["env"]["NGINX_HTTPS_PORT"], "9443")
        self.assertEqual(up["env"]["NGINX_PORT"], "80")
        self.assertEqual(up["env"]["HTTPS_ENABLED"], "true")
        self.assertEqual(up["env"]["OPENCITADEL_DOMAIN"], "app.example.test")
        self.log.write_text("")
        override = self.run_script(NGINX_HTTPS_PORT="10443", OPENCITADEL_DOMAIN="explicit.test")
        self.assertEqual(override.returncode, 0, override.stderr)
        up = self.compose_call("up")
        self.assertEqual(up["env"]["NGINX_HTTPS_PORT"], "10443")
        self.assertEqual(up["env"]["OPENCITADEL_DOMAIN"], "explicit.test")

    def test_ipv6_origin_and_explicit_socket_group(self):
        result = self.run_script(FRONTEND_BASE_URL="http://[::1]:9088", DOCKER_SOCK_GID="1234")
        self.assertEqual(result.returncode, 0, result.stderr)
        up = self.compose_call("up")
        self.assertEqual(up["env"]["NGINX_PORT"], "9088")
        self.assertEqual(up["env"]["OPENCITADEL_DOMAIN"], "::1")
        self.assertEqual(up["env"]["DOCKER_SOCK_GID"], "1234")

    def test_remote_linux_docker_does_not_guess_a_socket_group(self):
        self.write_command("uname", "#!/bin/sh\nprintf Linux\n")
        result = self.run_script(FAKE_DOCKER_ENDPOINT="tcp://docker.example.test:2376")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.compose_call("up")["env"]["DOCKER_SOCK_GID"])

    def test_supplied_secret_quotes_and_backslashes_round_trip_without_evaluation(self):
        password = r"literal \'quote $HOME # value"
        result = self.run_script(BOOTSTRAP_ADMIN_PASSWORD=password)
        self.assertEqual(result.returncode, 0, result.stderr)
        probe = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; dotenv_read "$2" BOOTSTRAP_ADMIN_PASSWORD',
                "dotenv-probe",
                str(self.root / "scripts/configure-local.sh"),
                str(self.env),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        self.assertEqual(probe.stdout, password)
        self.assertNotIn(password, result.stdout + result.stderr)

    def test_invalid_origin_or_timeout_stops_before_build(self):
        for updates in (
            {"FRONTEND_BASE_URL": "https://example.test/path"},
            {"FRONTEND_BASE_URL": "http://example.test:70000"},
            {"QUICKSTART_WAIT_TIMEOUT_SECONDS": "not-a-number"},
        ):
            with self.subTest(updates=updates):
                self.log.write_text("")
                result = self.run_script(**updates)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("build" in call["args"] for call in self.calls()))

    def test_configure_command_creates_without_docker_or_printing_password(self):
        result = self.run_script("configure-local.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.env.exists())
        self.assertFalse(self.log.exists())
        self.assertNotIn(self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"], result.stdout)

    def test_configure_then_first_healthy_start_displays_generated_password_once(self):
        configured = self.run_script("configure-local.sh")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        password = self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"]
        self.assertIn("initial-admin-login-pending", self.env.read_text())
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(password, first.stdout)
        self.assertNotIn("initial-admin-login-pending", self.env.read_text())
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn(password, second.stdout + second.stderr)

    def test_password_edited_after_configure_is_never_displayed(self):
        configured = self.run_script("configure-local.sh")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        original = self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"]
        supplied = "a" * 64
        self.env.write_text(self.env.read_text().replace(original, supplied))
        started = self.run_script()
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertNotIn(supplied, started.stdout + started.stderr)
        self.assertNotIn("initial-admin-login-pending", self.env.read_text())

    def test_pending_login_in_symlink_is_preserved_without_printing_password(self):
        configured = self.run_script("configure-local.sh")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        password = self.stored_values()["BOOTSTRAP_ADMIN_PASSWORD"]
        original = self.env.read_bytes()
        target = self.root / "linked-installation.env"
        self.env.rename(target)
        self.env.symlink_to(target)
        started = self.run_script()
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertTrue(self.env.is_symlink())
        self.assertEqual(target.read_bytes(), original)
        self.assertNotIn(password, started.stdout + started.stderr)


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is needed for Compose rendering")
class ComposeSerializationTests(unittest.TestCase):
    """Use Compose's real dotenv parser without contacting a Docker daemon."""

    @classmethod
    def setUpClass(cls):
        cls.docker = shutil.which("docker")
        version = subprocess.run(
            [cls.docker, "compose", "version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if version.returncode:
            raise unittest.SkipTest("Docker Compose v2 is needed for rendering")

    def test_supplied_secrets_reach_compose_environment_without_character_changes(self):
        passwords = (
            "startup-only-test-$literal-'quote'-\\backslash",
            r"literal \'quote $HOME # value",
            "trailing" + chr(92),
            r"pair\\tail",
            "dollars $$literal ${UNSET_LITERAL_PLACEHOLDER}",
            r"escaped-looking \n \t \r \" $literal",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            fixture = root / "compose.yaml"
            fixture.write_text(
                "services:\n  api:\n    image: busybox:latest\n"
                "    env_file:\n      - ${OPENCITADEL_ENV_FILE}\n"
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in SECRET_KEYS and key != "COMPOSE_PROFILES"
            }
            environment["OPENCITADEL_ENV_FILE"] = str(runtime)
            command = [
                self.docker,
                "compose",
                "--project-name",
                "opencitadel-literal-regression",
                "--env-file",
                str(runtime),
                "-f",
                str(fixture),
                "config",
            ]
            for password in passwords:
                with self.subTest(password=password):
                    runtime.write_text("")
                    subprocess.run(
                        [
                            "bash",
                            "-c",
                            'source "$1"; config_write_value "$2" BOOTSTRAP_ADMIN_PASSWORD "$3"',
                            "dotenv-serializer",
                            str(ROOT / "scripts/configure-local.sh"),
                            str(runtime),
                            password,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                    resolved = subprocess.run(
                        [*command, "--environment"],
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=10,
                    )
                    values = dict(
                        line.split("=", 1) for line in resolved.stdout.splitlines() if "=" in line
                    )
                    self.assertEqual(values["BOOTSTRAP_ADMIN_PASSWORD"], password)
                    read_back = subprocess.run(
                        [
                            "bash",
                            "-c",
                            'source "$1"; dotenv_read "$2" BOOTSTRAP_ADMIN_PASSWORD',
                            "dotenv-reader",
                            str(ROOT / "scripts/configure-local.sh"),
                            str(runtime),
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                    self.assertEqual(read_back.stdout, values["BOOTSTRAP_ADMIN_PASSWORD"])
                    rendered = subprocess.run(
                        [*command, "--format", "json"],
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=10,
                    )
                    api_environment = json.loads(rendered.stdout)["services"]["api"]["environment"]
                    # Config escapes dollars for reloadable Compose output; this
                    # is independent of dotenv parsing and must not mask a
                    # change in backslashes, apostrophes or any other character.
                    self.assertEqual(
                        api_environment["BOOTSTRAP_ADMIN_PASSWORD"], password.replace("$", "$$")
                    )


if __name__ == "__main__":
    unittest.main()

# ruff: noqa: PT009 -- standalone stdlib unittest suite, no pytest dependency
"""Run the entrypoints with a recording Docker substitute; never touch a daemon."""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAKE = r"""#!/usr/bin/env python3
import io, json, os, pathlib, sys, tarfile
args=sys.argv[1:]
with open(os.environ['DOCKER_LOG'],'a') as f: f.write(json.dumps(args)+'\n')
if args[:1] == ['info']: sys.exit(0)
if args[:2] == ['volume','ls']:
 print('restore_existing_postgres' if os.environ.get('EXISTING') else ''); sys.exit(0)
if args[:2] == ['ps','-aq']: print(''); sys.exit(0)
if 'config' in args:
 print(json.dumps({'name':'custom','services':{
 'opencitadel-postgres':{'image':'pgvector/pgvector:pg16','environment':{'POSTGRES_USER':'custom_admin','POSTGRES_DB':'custom_db'}},
 'opencitadel-api':{'environment':{'STORAGE_PROVIDER':os.environ.get('PROVIDER','minio'),'MINIO_ENDPOINT':'opencitadel-minio:9000'}},
 'opencitadel-minio':{'image':'minio/minio:test','volumes':[{'type':'volume','source':'objects','target':'/data'}]}},
 'volumes':{'objects':{'name':'custom_object_volume'}}})); sys.exit(0)
if 'ps' in args:
 print('opencitadel-api\nopencitadel-execution-kernel\nopencitadel-minio\nopencitadel-postgres'); sys.exit(0)
if args[:2] == ['volume','inspect']:
 sys.exit(1 if os.environ.get('MISSING_OBJECTS') else 0)
if 'start' in args and os.environ.get('FAIL_RESTART'): sys.exit(1)
if any('test -z' in x for x in args) and os.environ.get('NONEMPTY'): sys.exit(1)
if 'pg_restore' in args and os.environ.get('FAIL_RESTORE'): sys.exit(1)
if 'tar' in args and os.environ.get('FAIL_ARCHIVE'): sys.exit(1)
if 'pg_dump' in args:
 if os.environ.get('FAIL_DUMP'): sys.exit(1)
 sys.stdout.buffer.write(b'PGDMPfixture'); sys.exit(0)
if 'pg_dumpall' in args: print('CREATE ROLE custom_admin;'); sys.exit(0)
if 'psql' in args:
 sys.stdin.buffer.read()
 print('public.users\t2'); sys.exit(0)
if 'tar' in args and '-czf' in args:
 target=args[args.index('-czf')+1]
 mounts=[args[i+1] for i,x in enumerate(args) if x=='--mount']
 bind=next(x for x in mounts if 'target=/backup' in x)
 directory=next(x[7:] for x in bind.split(',') if x.startswith('source='))
 with tarfile.open(pathlib.Path(directory)/pathlib.Path(target).name,'w:gz') as archive:
  data=b'object-payload'; member=tarfile.TarInfo('bucket/file');member.size=len(data);archive.addfile(member,io.BytesIO(data))
 sys.exit(0)
sys.exit(0)
"""


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        docker = self.path / "docker"
        docker.write_text(FAKE)
        docker.chmod(0o700)
        self.env = dict(
            os.environ,
            PATH=f"{self.path}:{os.environ['PATH']}",
            DOCKER_LOG=str(self.path / "docker.log"),
        )
        self.output = self.path / "backup"

    def run_tool(self, mode, *args, **env):
        return subprocess.run(
            ["python3", str(ROOT / "scripts/backup_tool.py"), mode, *map(str, args)],
            env={**self.env, **env},
            text=True,
            capture_output=True,
        )

    def calls(self):
        path = self.path / "docker.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def backup(self, **env):
        return self.run_tool("backup", self.output, **env)

    def test_full_backup_uses_configured_names_and_restarts_writers(self):
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        dump = next(c for c in self.calls() if "pg_dump" in c)
        self.assertIn("custom_admin", dump)
        self.assertIn("custom_db", dump)
        self.assertTrue(any("custom_object_volume" in " ".join(c) for c in self.calls()))
        stop = next(i for i, c in enumerate(self.calls()) if "stop" in c)
        self.assertLess(stop, self.calls().index(dump))
        self.assertTrue(any("start" in c for c in self.calls()))
        self.assertEqual(self.run_tool("verify", self.output).returncode, 0)

    def test_configured_archive_image_is_used_for_backup_and_restore(self):
        result = self.backup(OPENCITADEL_BACKUP_ARCHIVE_IMAGE="redis:7.4-alpine")
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = next(c for c in self.calls() if "-czf" in c)
        self.assertIn("redis:7.4-alpine", archive)
        result = self.run_tool(
            "restore",
            self.output,
            "restore_fresh",
            OPENCITADEL_BACKUP_ARCHIVE_IMAGE="redis:7.4-alpine",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        helpers = [c for c in self.calls() if "-czf" in c or "-ec" in c]
        self.assertTrue(all("redis:7.4-alpine" in c for c in helpers))

    def test_dump_failure_marks_partial_and_always_restarts(self):
        result = self.backup(FAIL_DUMP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads((self.output / "manifest.json").read_text())["status"], "partial"
        )
        self.assertTrue(any("start" in c for c in self.calls()))
        self.assertNotEqual(self.run_tool("verify", self.output).returncode, 0)

    def test_archive_or_restart_failure_never_reports_complete(self):
        for failure in ("FAIL_ARCHIVE", "FAIL_RESTART"):
            with self.subTest(failure=failure):
                destination = self.path / failure
                result = self.run_tool("backup", destination, **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    json.loads((destination / "manifest.json").read_text())["status"], "partial"
                )
                self.assertTrue(any("start" in c for c in self.calls()))

    def test_unsafe_archive_is_rejected_even_with_updated_payload_checksum(self):
        self.assertEqual(self.backup().returncode, 0)
        archive_path = self.output / "minio-data.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            member = tarfile.TarInfo("../../escape")
            member.size = 4
            archive.addfile(member, io.BytesIO(b"evil"))
        import hashlib

        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["files"]["minio-data.tar.gz"] = {
            "size": archive_path.stat().st_size,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        }
        (self.output / "manifest.json").write_text(json.dumps(manifest))
        (self.path / "docker.log").write_text("")
        self.assertNotEqual(self.run_tool("restore", self.output, "restore_fresh").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_objects_and_external_storage_fail_closed(self):
        for env in ({"MISSING_OBJECTS": "1"}, {"PROVIDER": "cos"}):
            with self.subTest(env=env):
                self.assertNotEqual(self.backup(**env).returncode, 0)
                self.assertFalse(any("stop" in c for c in self.calls()))

    def test_tamper_is_rejected_before_any_restore_write(self):
        self.assertEqual(self.backup().returncode, 0)
        (self.output / "postgres.dump").write_bytes(b"tampered")
        (self.path / "docker.log").write_text("")
        self.assertNotEqual(self.run_tool("restore", self.output, "restore_fresh").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_existing_destination_is_refused_without_mutation(self):
        self.assertEqual(self.backup().returncode, 0)
        (self.path / "docker.log").write_text("")
        self.assertNotEqual(
            self.run_tool("restore", self.output, "restore_existing", EXISTING="1").returncode, 0
        )
        self.assertFalse(any("create" in c or "run" in c for c in self.calls()))

    def test_nonempty_recovery_volume_is_refused_before_database_start(self):
        self.assertEqual(self.backup().returncode, 0)
        (self.path / "docker.log").write_text("")
        result = self.run_tool("restore", self.output, "restore_fresh", NONEMPTY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("-d" in c and "run" in c for c in self.calls()))

    def test_database_restore_failure_stops_container_and_keeps_partial_report(self):
        self.assertEqual(self.backup().returncode, 0)
        (self.path / "docker.log").write_text("")
        result = self.run_tool("restore", self.output, "restore_fresh", FAIL_RESTORE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(c[0] == "stop" for c in self.calls()))
        report = json.loads((self.output / "restore-restore_fresh.json").read_text())
        self.assertEqual(report["status"], "partial")

    def test_restore_verifies_database_and_object_payload_in_new_volumes(self):
        self.assertEqual(self.backup().returncode, 0)
        result = self.run_tool("restore", self.output, "restore_fresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("pg_restore" in c for c in self.calls()))
        self.assertFalse(any("-p" in c or "--publish" in c for c in self.calls()))
        report = json.loads((self.output / "restore-restore_fresh.json").read_text())
        self.assertEqual(report["status"], "payload_verified")
        self.assertFalse(report["application_smoke_verified"])


if __name__ == "__main__":
    unittest.main()

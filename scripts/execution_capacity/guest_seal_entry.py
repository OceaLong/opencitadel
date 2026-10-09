"""Provision verbatim at /opt/opencitadel-capacity/guest_seal_entry.py.

Run with the pinned self-contained observer Python and -I, outside all producer
containers. Only this closed dispatcher imports the fixed preprovisioned source.
"""

import base64
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

SOURCE = Path("/opt/opencitadel-capacity/source")
RUNTIME = Path("/opt/opencitadel-capacity/observer")
CONFIG = Path("/etc/opencitadel-capacity-seal.json")


def parse_evidence_limits(value):
    """Producer configuration only; never adopt limits from retained evidence."""
    names = {"bytes_limit", "rows_limit", "row_limit", "index_bytes"}
    if (
        type(value) is not dict
        or set(value) != names
        or any(type(value[name]) is not int or not 0 < value[name] <= 2**63 - 1 for name in names)
        or value["row_limit"] > min(value["bytes_limit"], 32 * 1024 * 1024)
        or value["index_bytes"] > value["bytes_limit"]
        or value["index_bytes"] % 4096
        or not 8 <= value["index_bytes"] // 4096 <= 2**31 - 1
    ):
        raise ValueError("exact supported immutable evidence limits required")
    return dict(value)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError("private root-owned seal configuration required")
        raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("seal configuration bound")
        return json.loads(raw)


def code_tree(root):
    """Exhaustive code/runtime tree fingerprint, before importing that code."""
    if root.resolve() != root or root.stat().st_uid != 0:
        raise ValueError("fixed root-owned source/runtime required")
    names = sorted(root.rglob("*"))
    hashed = hashlib.sha256()
    for path in names:
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("writable observer source/runtime")
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError("external observer source/runtime link")
            item = [name, "link", os.readlink(path)]
        elif path.is_dir():
            item = [name, "dir", stat.S_IMODE(info.st_mode)]
        elif path.is_file():
            data = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    data.update(block)
            item = [name, stat.S_IMODE(info.st_mode), info.st_size, data.hexdigest()]
        else:
            raise ValueError("unknown observer tree node")
        after = path.lstat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("observer tree changed")
        hashed.update(encoded(item) + b"\n")
    if names != sorted(root.rglob("*")):
        raise ValueError("observer tree membership changed")
    return hashed.hexdigest()


def artifact_relative(name):
    """Closed logical names shared by guest transport and host request owner."""
    fixed = {
        "cleanup": "cleanup.json",
        "stop": "stop.json",
        "offline": "offline.json",
        "c2c-manifest": "c2c-originals/manifest.json",
        "c2c-ledger-plan": "c2c-ledger/plan.json",
        "c2c-ledger-rows": "c2c-ledger/attempt.jsonl",
        "c2c-body-000000": "c2c-originals/000000.bin",
        "c2c-occurrences": "c2c-originals/occurrences.jsonl",
    }
    if name in fixed:
        return fixed[name]
    native = (
        re.fullmatch(
            r"c2c-native-([0-9]{6})-([0-9a-f]{32})-(manifest|body-[0-9]{6}|log-[0-9]{6})", name
        )
        if type(name) is str
        else None
    )
    if native is not None:
        ordinal, identity, member = native.groups()
        filename = (
            "manifest.json"
            if member == "manifest"
            else (
                member[5:] + ".bin"
                if member.startswith("body-")
                else "occurrences.jsonl"
                if member == "log-000000"
                else member[4:] + ".jsonl"
            )
        )
        return f"c2c-originals/native-{ordinal}/original-{identity}/" + filename
    if (
        isinstance(name, str)
        and name.startswith("c2c-shard-")
        and len(name) == 16
        and all("0" <= char <= "9" for char in name[10:])
    ):
        return "c2c-originals/" + name[10:] + ".jsonl"
    for prefix, suffix in (("c2c-body-", ".bin"), ("c2c-log-", ".jsonl")):
        if (
            isinstance(name, str)
            and name.startswith(prefix)
            and len(name) == len(prefix) + 6
            and all("0" <= char <= "9" for char in name[len(prefix) :])
            and (prefix != "c2c-log-" or name[len(prefix) :] != "000000")
        ):
            return "c2c-originals/" + name[len(prefix) :] + suffix
    raise ValueError("closed private artifact name required")


def original_artifacts(manifest):
    """Closed transport inventory only; actual original replay remains required."""
    if type(manifest) is not dict or type(manifest.get("schema")) is not int:
        raise ValueError("invalid original manifest version")
    if manifest["schema"] == 2:
        chunk_bytes = manifest.get("chunk_bytes")
        if (
            manifest.get("encoding") != 2
            or type(manifest.get("encoding")) is not int
            or type(chunk_bytes) is not int
            or not 4096 <= chunk_bytes <= 1024 * 1024
        ):
            raise ValueError("invalid original body encoding")
        for stream in ("body", "log"):
            descriptors = manifest.get(stream + "_chunks")
            if type(descriptors) is not list or not 1 <= len(descriptors) <= 999999:
                raise ValueError("complete original segment inventory required")
            total = 0
            for ordinal, descriptor in enumerate(descriptors):
                if (
                    type(descriptor) is not dict
                    or set(descriptor) != {"ordinal", "bytes", "sha256"}
                    or type(descriptor["ordinal"]) is not int
                    or descriptor["ordinal"] != ordinal
                    or type(descriptor["bytes"]) is not int
                    or not 0 < descriptor["bytes"] <= chunk_bytes
                    or (ordinal < len(descriptors) - 1 and descriptor["bytes"] != chunk_bytes)
                    or type(descriptor["sha256"]) is not str
                    or len(descriptor["sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in descriptor["sha256"])
                ):
                    raise ValueError("invalid original segment descriptor")
                total += descriptor["bytes"]
                name = (
                    "c2c-occurrences"
                    if stream == "log" and ordinal == 0
                    else f"c2c-{stream}-{ordinal:06}"
                )
                yield name, {"size_bytes": descriptor["bytes"], "sha256": descriptor["sha256"]}
            if (
                type(manifest.get(stream + "_bytes")) is not int
                or total != manifest[stream + "_bytes"]
            ):
                raise ValueError("original segment total differs")
        imports = manifest.get("native_bodies")
        if type(imports) is not list or len(imports) > 999999:
            raise ValueError("closed native original inventory required")
        for ordinal, entry in enumerate(imports):
            if (
                type(entry) is not dict
                or set(entry) != {"namespace", "ordinal", "reference", "manifest", "manifest_bytes"}
                or entry["namespace"] != "native-local"
                or type(entry["ordinal"]) is not int
                or entry["ordinal"] != ordinal
                or type(entry["manifest"]) is not dict
                or entry["manifest"].get("native_bodies") != []
            ):
                raise ValueError("closed native original namespace required")
            reference, inner = entry["reference"], entry["manifest"]
            if (
                type(reference) is not dict
                or set(reference)
                != {"schema", "namespace", "directory", "manifest_sha256", "expanded_sha256"}
                or reference["schema"] != "opencitadel.environment-read.original.v2"
                or reference["namespace"] != "recovery-local"
                or type(reference["directory"]) is not str
                or re.fullmatch(r"original-[0-9a-f]{32}", reference["directory"]) is None
                or inner.get("schema") != 2
                or type(inner.get("schema")) is not int
            ):
                raise ValueError("closed native original reference required")
            raw = encoded(inner)
            if (
                type(entry["manifest_bytes"]) is not int
                or entry["manifest_bytes"] != len(raw)
                or hashlib.sha256(raw).hexdigest() != reference["manifest_sha256"]
            ):
                raise ValueError("native original manifest commitment differs")
            prefix = f"c2c-native-{ordinal:06}-" + reference["directory"][9:] + "-"
            yield (
                prefix + "manifest",
                {"size_bytes": len(raw), "sha256": reference["manifest_sha256"]},
            )
            for logical, descriptor in original_artifacts(inner):
                member = "log-000000" if logical == "c2c-occurrences" else logical[4:]
                if not member.startswith(("body-", "log-")):
                    raise ValueError("nonrecursive native original members required")
                yield prefix + member, descriptor
    elif manifest["schema"] == 1:
        shards = manifest.get("shards")
        if type(shards) is not list or not 1 <= len(shards) <= 999999:
            raise ValueError("invalid original shard coverage")
        for ordinal, descriptor in enumerate(shards):
            if type(descriptor) is not dict or descriptor.get("ordinal") != ordinal:
                raise ValueError("original shard ordering differs")
            yield (
                f"c2c-shard-{ordinal:06}",
                {"size_bytes": descriptor["bytes"], "sha256": descriptor["sha256"]},
            )
    else:
        raise ValueError("unsupported original manifest version")


def artifact_path(root, name):
    relative = artifact_relative(name)
    if (
        name.startswith(("c2c-shard-", "c2c-body-", "c2c-log-", "c2c-native-"))
        or name == "c2c-occurrences"
    ):
        try:
            manifest = private_json(root / "c2c-originals" / "manifest.json")
        except OSError as error:
            raise ValueError("original manifest absent") from error
        if not any(logical == name for logical, _ in original_artifacts(manifest)):
            raise ValueError("original artifact outside manifest")
    path = root / relative
    if path.resolve() != path.absolute():
        raise ValueError("private artifact path contains symlink")
    return path


def main():
    if len(sys.argv) != 2 or len(sys.argv[1].encode()) > 32768:
        raise ValueError("fixed seal request required")
    request = json.loads(sys.argv[1])
    config = private_json(CONFIG)
    evidence_limits = parse_evidence_limits(config.get("evidence_limits"))
    if not isinstance(config.get("protocol_id"), str) or not config["protocol_id"]:
        raise ValueError("required immutable seal protocol_id absent")
    if (
        hashlib.sha256(encoded(config)).hexdigest() != request["config_digest"]
        or config["identity"] != {k: v for k, v in request["identity"].items() if k != "boot_id"}
        or request["identity"]["boot_id"]
        != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    ):
        raise ValueError("seal request identity/config differs")
    if (
        hashlib.sha256(Path("/proc/self/exe").read_bytes()).hexdigest()
        != config["observer_python_sha256"]
    ):
        raise ValueError("actual observer executable differs")
    if request["phase"] == "read":
        if (
            set(request) != {"identity", "phase", "config_digest", "artifact", "offset"}
            or type(request["offset"]) is not int
            or request["offset"] < 0
        ):
            raise ValueError("fixed private artifact page required")
        path = artifact_path(Path(config["evidence_root"]), request["artifact"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or request["offset"] > info.st_size
            ):
                raise ValueError("private retained artifact differs")
            stream.seek(request["offset"])
            data = stream.read(128 * 1024)
            after = os.fstat(stream.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("private artifact changed while reading")
        value = {
            "identity": request["identity"],
            "phase": "read",
            "protocol_id": config["protocol_id"],
            "artifact": request["artifact"],
            "offset": request["offset"],
            "size_bytes": info.st_size,
            "data": base64.b64encode(data).decode(),
            "eof": request["offset"] + len(data) == info.st_size,
            "config_digest": request["config_digest"],
        }
    else:
        if (
            str(SOURCE) != config["source_root"]
            or code_tree(SOURCE) != config["observer_source_tree_digest"]
            or code_tree(RUNTIME) != config["observer_runtime_tree_digest"]
        ):
            raise ValueError("actual provisioned observer build differs")
        sys.dont_write_bytecode = True
        sys.path[:0] = [str(SOURCE / "api"), str(SOURCE)]
        from scripts.execution_capacity.guest_seal import phase

        value = phase(request, private_json(Path("/etc/opencitadel-capacity.json")), config)
    value["evidence_limits"] = evidence_limits
    value["seal_helper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    value["observer_python_sha256"] = config["observer_python_sha256"]
    sys.stdout.buffer.write(encoded(value))


if __name__ == "__main__":
    main()

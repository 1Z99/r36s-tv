"""Release trust policy, also embedded in the standalone generated bootstrap."""

import ast
import json
import re
import subprocess
import tempfile

REPOSITORY = "https://github.com/1Z99/r36s-tv"
LATEST_RELEASE = REPOSITORY + "/releases/latest/download/release.json"
TARGET = "debian13-arm64-darkosre"
METADATA_LIMIT = 64 * 1024


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value) or len(value) > 32:
        raise ValueError("Invalid stable release version")
    return tuple(map(int, value.split(".")))


def canonical_base(version):
    stable_version(version)
    return REPOSITORY + "/releases/download/v" + version


def validate_release(info, version=None, manager=True, canonical=True):
    if not isinstance(info, dict):
        raise ValueError("Invalid release metadata")
    stable_version(info.get("version"))
    if (type(info.get("format")) is not int or info["format"] != 1
            or info.get("target") != TARGET or (version is not None and info["version"] != version)):
        raise ValueError("Release format, target or version mismatch")
    if canonical and (not isinstance(info.get("base_url"), str)
                      or info["base_url"].rstrip("/") != canonical_base(info["version"])):
        raise ValueError("Release base must be the canonical repository/tag HTTPS directory")
    for key, filename, maximum in (
            ("bundle", "melkor227-tv-" + info["version"] + "-darkosre-arm64.tar.gz", 128*1024*1024),
            ("manager", "melkor227-tv-manager-" + info["version"] + ".tar.gz", 16*1024*1024)):
        if key == "manager" and not manager:
            continue
        entry = info.get(key)
        if (not isinstance(entry, dict) or entry.get("file") != filename
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", entry["sha256"])
                or type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= maximum):
            raise ValueError("Invalid pinned " + key + " metadata")
        if key == "bundle" and (type(entry.get("unpacked_bytes")) is not int
                                or not 0 < entry["unpacked_bytes"] <= 256*1024*1024):
            raise ValueError("Invalid unpacked bundle size")
    return info


def fetch_release(version=None):
    url = LATEST_RELEASE if version is None else canonical_base(version) + "/release.json"
    # Bound time, redirects, disk use and the amount parsed, including chunked responses.
    with tempfile.TemporaryFile() as output:
        subprocess.run(["curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                        "--max-redirs", "5", "--connect-timeout", "10", "--max-time", "30",
                        "--max-filesize", str(METADATA_LIMIT), "--silent", "--show-error", url],
                       stdout=output, stderr=subprocess.PIPE, check=True, timeout=35)
        output.seek(0)
        data = output.read(METADATA_LIMIT + 1)
    if len(data) > METADATA_LIMIT:
        raise ValueError("Release metadata exceeds its size limit")
    return validate_release(json.loads(data), version)


def source_version(path):
    """Recover shipped VERSION without importing or executing installed code."""
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError("Cannot identify installed TV version safely; no files were changed") from exc
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if len(values) != 1:
        raise ValueError("Cannot identify installed TV version safely; no files were changed")
    stable_version(values[0])
    return values[0]

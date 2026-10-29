
import json
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOGS_DIR = ROOT / "logs"
VERSIONS_DIR = ROOT / "versions"
STATE_PATH = LOGS_DIR / "document_state.json"
ACTIVE_PATH = VERSIONS_DIR / "active_version.json"
VERSION_INDEX = VERSIONS_DIR / "version_index.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def build_snapshot():
    state = read_json(STATE_PATH, {"documents": {}})
    documents = state.get("documents", {})

    snapshot = {}
    for name, record in sorted(documents.items()):
        if record.get("status") == "accepted":
            snapshot[name] = {
                "content_hash": record["content_hash"],
                "processed_text": record["processed_text"],
            }

    return snapshot


def create_version():
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    snapshot = build_snapshot()
    serialized = json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    index = read_json(VERSION_INDEX, {"versions": []})

    # Avoid creating duplicate versions for identical knowledge bases.
    for version in index["versions"]:
        if version["sha256"] == digest:
            print(f"Existing version: {version['version']}")
            return version["version"]

    version_name = (
        "v" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    )
    version_dir = VERSIONS_DIR / version_name
    version_dir.mkdir()

    snapshot_path = version_dir / "knowledge_base.json"
    write_json_atomic(snapshot_path, snapshot)

    metadata = {
        "version": version_name,
        "sha256": digest,
        "created_at": now(),
        "document_count": len(snapshot),
    }
    write_json_atomic(version_dir / "metadata.json", metadata)

    index["versions"].append(metadata)
    write_json_atomic(VERSION_INDEX, index)

    print(f"Created version: {version_name}")
    print(f"Documents: {len(snapshot)}")

    return version_name


def activate_version(version_name):
    version_dir = VERSIONS_DIR / version_name
    snapshot_path = version_dir / "knowledge_base.json"
    metadata_path = version_dir / "metadata.json"

    if not snapshot_path.is_file() or not metadata_path.is_file():
        raise ValueError("Unknown or incomplete version")

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    serialized = json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    if digest != metadata["sha256"]:
        raise ValueError("Version integrity check failed")

    previous = read_json(ACTIVE_PATH, {})
    active = {
        "version": version_name,
        "activated_at": now(),
        "previous_version": previous.get("version"),
    }
    write_json_atomic(ACTIVE_PATH, active)

    print(f"Active version: {version_name}")


def rollback():
    active = read_json(ACTIVE_PATH, {})
    previous_version = active.get("previous_version")

    if not previous_version:
        raise ValueError("No previous version is available to restore")

    version_dir = VERSIONS_DIR / previous_version
    metadata_path = version_dir / "metadata.json"
    snapshot_path = version_dir / "knowledge_base.json"

    if not metadata_path.is_file() or not snapshot_path.is_file():
        raise ValueError("Previous version is missing")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    serialized = json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    if digest != metadata["sha256"]:
        raise ValueError("Previous version integrity check failed")

    # Preserve the version being replaced as the rollback history.
    failed_version = active.get("version")
    write_json_atomic(ACTIVE_PATH, {
        "version": previous_version,
        "activated_at": now(),
        "previous_version": failed_version,
    })

    print(f"Rollback completed. Active version: {previous_version}")


def list_versions():
    index = read_json(VERSION_INDEX, {"versions": []})
    active = read_json(ACTIVE_PATH, {}).get("version")

    for item in index["versions"]:
        marker = " (ACTIVE)" if item["version"] == active else ""
        print(
            f'{item["version"]}{marker} | '
            f'documents={item["document_count"]} | '
            f'created={item["created_at"]}'
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["create", "activate", "rollback", "list"],
    )
    parser.add_argument("--version")
    args = parser.parse_args()

    if args.action == "create":
        create_version()
    elif args.action == "activate":
        if not args.version:
            parser.error("activate requires --version")
        activate_version(args.version)
    elif args.action == "rollback":
        rollback()
    elif args.action == "list":
        list_versions()


import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from multimodal_processor import extract_pdf_text


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"
DOCUMENTS_DIR = ROOT / "documents"
QUARANTINE_DIR = ROOT / "quarantine"
LOGS_DIR = ROOT / "logs"
STATE_PATH = LOGS_DIR / "document_state.json"
AUDIT_PATH = LOGS_DIR / "document_processing.jsonl"


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def log_event(event):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_state():
    if not STATE_PATH.exists():
        return {"documents": {}}

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)
        if isinstance(state, dict) and isinstance(
            state.get("documents"), dict
        ):
            return state
    except (OSError, json.JSONDecodeError):
        pass

    raise RuntimeError(
        "Document state is invalid. Restore a valid backup before processing."
    )


def save_state(state):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(STATE_PATH)


def mask_sensitive_data(text):
    # Common email addresses.
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL_REDACTED]",
        text,
    )

    # Common phone number formats.
    text = re.sub(
        r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)",
        "[PHONE_REDACTED]",
        text,
    )

    # Common API keys and bearer tokens.
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[SECRET_REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [TOKEN_REDACTED]",
        text,
    )

    return text


INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?system\s+instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"reveal\s+(all\s+)?(secret|confidential)\s+(data|information|keys)",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
]


def contains_prompt_injection(text):
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in INJECTION_PATTERNS
    )


def quarantine_file(path, reason):
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

    destination = QUARANTINE_DIR / path.name
    if destination.exists():
        destination = QUARANTINE_DIR / (
            f"{path.stem}_{sha256_text(str(path) + timestamp())[:8]}"
            f"{path.suffix}"
        )

    shutil.move(str(path), str(destination))

    log_event({
        "timestamp": timestamp(),
        "status": "quarantined",
        "file": path.name,
        "reason": reason,
    })


def process_documents():
    config = load_config()

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    known_documents = state["documents"]

    # Hashes of previously accepted document contents.
    known_hashes = {
        item["content_hash"]: name
        for name, item in known_documents.items()
        if isinstance(item, dict) and item.get("content_hash")
    }

    allowed_extensions = {
        extension.lower()
        for extension in config["allowed_extensions"]
    }
    max_bytes = int(config["max_file_size_mb"] * 1024 * 1024)

    results = []

    for path in sorted(DOCUMENTS_DIR.iterdir()):
        if not path.is_file():
            continue

        if path.name.startswith(".") or path.name.endswith(".tmp"):
            continue

        try:
            if path.suffix.lower() not in allowed_extensions:
                quarantine_file(path, "Unsupported file extension")
                results.append({
                    "file": path.name,
                    "status": "quarantined",
                })
                continue

            if path.stat().st_size > max_bytes:
                quarantine_file(path, "File exceeds maximum allowed size")
                results.append({
                    "file": path.name,
                    "status": "quarantined",
                })
                continue

            raw_bytes = path.read_bytes()

            try:
                if path.suffix.lower() == ".pdf":
                    raw_text = extract_pdf_text(path)
                elif path.suffix.lower() in {
                    ".png", ".jpg", ".jpeg", ".webp"
                }:
                    quarantine_file(
                        path,
                        "Image OCR is unavailable; cannot extract text safely"
                    )
                    results.append({
                        "file": path.name,
                        "status": "quarantined",
                    })
                    continue
                else:
                    raw_text = raw_bytes.decode("utf-8")
            except Exception:
                quarantine_file(
                    path,
                    "File text extraction failed"
                )
                results.append({
                    "file": path.name,
                    "status": "quarantined",
                })
                continue

            if not raw_text.strip():
                quarantine_file(path, "Empty document")
                results.append({
                    "file": path.name,
                    "status": "quarantined",
                })
                continue

            # Validate JSON documents before processing.
            if path.suffix.lower() == ".json":
                try:
                    json.loads(raw_text)
                except json.JSONDecodeError:
                    quarantine_file(path, "Invalid JSON")
                    results.append({
                        "file": path.name,
                        "status": "quarantined",
                    })
                    continue

            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            previous = known_documents.get(path.name)

            # Do not reprocess an unchanged, previously accepted file.
            if (
                previous
                and previous.get("source_hash") == source_hash
                and previous.get("status") == "accepted"
            ):
                results.append({
                    "file": path.name,
                    "status": "unchanged",
                })
                continue

            # Block suspicious instructions before adding content.
            if config.get("block_prompt_injection", True):
                if contains_prompt_injection(raw_text):
                    quarantine_file(
                        path,
                        "Possible prompt injection detected",
                    )
                    results.append({
                        "file": path.name,
                        "status": "quarantined",
                    })
                    continue

            # Mask sensitive data before calculating the content hash.
            processed_text = (
                mask_sensitive_data(raw_text)
                if config.get("mask_sensitive_data", True)
                else raw_text
            )

            content_hash = sha256_text(processed_text)

            # A different file with the same processed content is a duplicate.
            duplicate_of = known_hashes.get(content_hash)
            if duplicate_of and duplicate_of != path.name:
                quarantine_file(
                    path,
                    f"Duplicate content of accepted document: {duplicate_of}",
                )
                results.append({
                    "file": path.name,
                    "status": "duplicate",
                    "duplicate_of": duplicate_of,
                })
                continue

            record = {
                "source_hash": source_hash,
                "content_hash": content_hash,
                "processed_text": processed_text,
                "status": "accepted",
                "processed_at": timestamp(),
            }

            known_documents[path.name] = record
            known_hashes[content_hash] = path.name

            save_state(state)

            log_event({
                "timestamp": timestamp(),
                "status": "accepted",
                "file": path.name,
                "content_hash": content_hash,
            })

            results.append({
                "file": path.name,
                "status": "accepted",
            })

        except OSError as error:
            log_event({
                "timestamp": timestamp(),
                "status": "failed",
                "file": path.name,
                "reason": str(error),
            })
            results.append({
                "file": path.name,
                "status": "failed",
            })

    return results


if __name__ == "__main__":
    for result in process_documents():
        print(json.dumps(result, ensure_ascii=False))

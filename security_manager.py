
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"
LOGS_DIR = ROOT / "logs"
SECURITY_LOG = LOGS_DIR / "security.jsonl"


ROLE_PERMISSIONS = {
    "viewer": {"view"},
    "operator": {"view", "process_documents"},
    "deployer": {"view", "process_documents", "deploy", "rollback"},
    "admin": {
        "view",
        "process_documents",
        "deploy",
        "rollback",
        "manage_access",
    },
}


INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?system\s+instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
]


def log_security_event(event):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with SECURITY_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")


def authorize(role, action):
    allowed = action in ROLE_PERMISSIONS.get(role, set())

    log_security_event({
        "event": "authorization_check",
        "role": role,
        "action": action,
        "result": "allowed" if allowed else "denied",
    })

    if not allowed:
        raise PermissionError(
            f"Role '{role}' is not authorized to perform '{action}'"
        )

    return True


def scan_document(text):
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if config.get("block_prompt_injection", True):
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                log_security_event({
                    "event": "prompt_injection_detected",
                    "result": "blocked",
                })
                return False

    return True


def mask_text(text):
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not config.get("mask_sensitive_data", True):
        return text

    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL_REDACTED]",
        text,
    )

    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[SECRET_REDACTED]",
        text,
    )

    return text


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--action", required=True)
    args = parser.parse_args()

    try:
        authorize(args.role, args.action)
        print("Access granted.")
    except PermissionError as error:
        print(f"Access denied: {error}")
        raise SystemExit(1)


import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config" / "config.json"
SESSION_FILE = BASE_DIR / "conversation_sessions.json"


def load_settings():
    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)
    return config.get("conversation", {})


SETTINGS = load_settings()
SUPPORTED_LANGUAGES = SETTINGS.get(
    "supported_languages", ["en", "hi", "te", "ta"]
)
DEFAULT_LANGUAGE = SETTINGS.get("default_language", "en")
LANGUAGE_THRESHOLD = float(
    SETTINGS.get("language_confidence_threshold", 0.70)
)
INTENT_THRESHOLD = float(
    SETTINGS.get("intent_confidence_threshold", 0.65)
)
CONTEXT_LIMIT = int(SETTINGS.get("context_message_limit", 10))
SESSION_TIMEOUT = int(SETTINGS.get("session_timeout_minutes", 30))
RESTORE_HOURS = int(SETTINGS.get("session_restore_hours", 24))

LOCK = threading.RLock()


def now():
    return datetime.now().astimezone()


def parse_time(value):
    return datetime.fromisoformat(value)



def detect_language(text):
    """Heuristic language detection for supported languages."""
    if not text.strip():
        return DEFAULT_LANGUAGE, 0.0

    lower = text.lower()

    # Native-script detection
    if re.search(r"[\u0900-\u097F]", text):
        return ("hi", 0.95) if "hi" in SUPPORTED_LANGUAGES else (DEFAULT_LANGUAGE, 0.3)

    if re.search(r"[\u0C00-\u0C7F]", text):
        return ("te", 0.95) if "te" in SUPPORTED_LANGUAGES else (DEFAULT_LANGUAGE, 0.3)

    if re.search(r"[\u0B80-\u0BFF]", text):
        return ("ta", 0.95) if "ta" in SUPPORTED_LANGUAGES else (DEFAULT_LANGUAGE, 0.3)

    # Common transliterated words
    romanized = {
        "hi": r"\b(kya|hai|nahi|mera|meri|chahiye|kripya)\b",
        "te": r"\b(naaku|kavali|ledu|undi|cheppandi|ela)\b",
        "ta": r"\b(enakku|venum|illa|irukku|sollunga|eppadi)\b",
    }

    matches = [
        lang for lang, pattern in romanized.items()
        if lang in SUPPORTED_LANGUAGES
        and re.search(pattern, lower)
    ]

    if len(matches) == 1:
        return matches[0], 0.80

    if len(matches) > 1:
        return DEFAULT_LANGUAGE, 0.50

    # Basic English detection
    if re.search(r"[a-zA-Z]", text):
        return DEFAULT_LANGUAGE, 0.80

    return DEFAULT_LANGUAGE, 0.30


def extract_entities(text):
    """Extract order IDs, dates, product codes, names and identifiers."""

    order_ids = re.findall(
        r"\border\s*(?:id|no\.?)?\s*[:#-]?\s*"
        r"([A-Z0-9]+(?:-[A-Z0-9]+)*)\b",
        text,
        flags=re.IGNORECASE
    )

    dates = re.findall(
        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        text
    )

    product_codes = re.findall(
        r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*\b",
        text
    )

    names = re.findall(
        r"\b(?:my name is|I am|I'm)\s+"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        text,
        flags=re.IGNORECASE
    )

    identifiers = list(dict.fromkeys(order_ids + product_codes))

    return {
        "order_ids": list(dict.fromkeys(order_ids)),
        "dates": list(dict.fromkeys(dates)),
        "product_codes": list(dict.fromkeys(product_codes)),
        "names": list(dict.fromkeys(names)),
        "identifiers": identifiers,
    }

def split_requests(text):
    """Basic splitting for messages containing multiple requests."""
    parts = re.split(
        r"\s*(?:\band\b|\balso\b|;|\n)\s*",
        text,
        flags=re.IGNORECASE,
    )
    return [part.strip() for part in parts if part.strip()]



def intent_confidence(text):
    """Detect common intents and tolerate selected spelling mistakes."""
    lower = text.lower()

    if SETTINGS.get("spelling_tolerance", True):
        corrections = {
            "chek": "check",
            "chekc": "check",
            "oder": "order",
            "ordr": "order",
            "refnd": "refund",
            "refun": "refund",
            "paymnt": "payment",
            "pyment": "payment",
            "delivry": "delivery",
            "delvery": "delivery",
            "traking": "tracking",
            "trackng": "tracking",
            "pls": "please",
        }

        lower = re.sub(
            r"\b[\w'-]+\b",
            lambda match: corrections.get(
                match.group(0), match.group(0)
            ),
            lower,
        )

    patterns = {
        "order": [
            r"\border\b", r"\btracking\b", r"\bdelivery\b",
            r"\bshipment\b", r"ऑर्डर|कहाँ|कहा|डिलीवरी",
            r"ఆర్డర్|ఎక్కడ|డెలివరీ",
            r"ஆர்டர்|எங்கே|டெலிவரி",
            r"\bkahan\b|\bekkada\b|\benga\b",
            r"\bnaaku\b.*\border\b",
        ],
        "payment": [
            r"\bpayment\b", r"\bpaid\b", r"\bcharge\b",
            r"\binvoice\b", r"भुगतान|पेमेंट|पैसे",
            r"చెల్లింపు|డబ్బు|పేమెంట్",
            r"பணம்|கட்டணம்|பேமெண்ட்",
            r"\bpaisa\b|\bdabbu\b|\bpanam\b",
        ],
        "refund": [
            r"\brefund\b", r"\breturn\b", r"\bmoney back\b",
            r"रिफंड|वापसी", r"రీఫండ్|తిరిగి",
            r"ரீஃபண்ட்|திரும்ப",
        ],
        "account": [
            r"\baccount\b", r"\blogin\b", r"\bpassword\b",
            r"खाता|पासवर्ड", r"ఖాతా|పాస్‌వర్డ్",
            r"கணக்கு|கடவுச்சொல்",
        ],
        "product": [
            r"\bproduct\b", r"\bitem\b", r"\bmodel\b",
            r"\bproduct code\b", r"उत्पाद|वस्तु",
            r"వస్తువు|ఉత్పత్తి", r"பொருள்|தயாரிப்பு",
        ],
    }

    matched = [
        intent
        for intent, regex_list in patterns.items()
        if any(
            re.search(pattern, lower, re.IGNORECASE)
            for pattern in regex_list
        )
    ]

    if len(matched) == 1:
        return matched[0], 0.80

    if len(matched) > 1:
        return "multiple", 0.75

    return "unknown", 0.30

def read_sessions():
    if not SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_sessions(sessions):
    temporary = SESSION_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(SESSION_FILE)


def build_summary(session):
    messages = session.get("messages", [])[-CONTEXT_LIMIT:]
    if not messages:
        return session.get("summary", "")

    lines = []
    for message in messages:
        role = message.get("role", "customer")
        content = message.get("text", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)



def handle_message(customer_id, text, current_time=None):
    """Store one message and return session/context/clarification data."""
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("A non-empty customer_id is required.")

    if not isinstance(text, str) or not text.strip():
        return {
            "status": "clarification_needed",
            "reply": "Please enter your message."
        }

    current_time = current_time or now()
    stamp = current_time.isoformat()

    with LOCK:
        sessions = read_sessions()
        customer = sessions.get(customer_id, {})
        last_active = customer.get("last_active")
        restored = False

        if last_active:
            inactive = current_time - parse_time(last_active)

            if inactive > timedelta(minutes=SESSION_TIMEOUT):
                old_summary = build_summary(customer)
                if not old_summary:
                    old_summary = customer.get("summary", "")

                can_restore = (
                    inactive <= timedelta(hours=RESTORE_HOURS)
                )

                customer["messages"] = []
                customer["active"] = False

                if can_restore and old_summary:
                    customer["summary"] = old_summary
                    customer["summary_time"] = last_active
                    restored = True
                else:
                    customer["summary"] = ""
                    customer["summary_time"] = None
                    customer["entities"] = {}
                    restored = False

        summary_time = customer.get("summary_time")

        if not customer or (
            summary_time
            and current_time - parse_time(summary_time)
            > timedelta(hours=RESTORE_HOURS)
        ):
            customer = {
                "messages": [],
                "summary": "",
                "summary_time": None,
                "entities": {},
                "language": DEFAULT_LANGUAGE,
            }
            restored = False

        language, language_score = detect_language(text)
        intent, intent_score = intent_confidence(text)

        entities = customer.get("entities", {})
        incoming = extract_entities(text)

        for field, values in incoming.items():
            if values:
                entities[field] = list(dict.fromkeys(
                    entities.get(field, []) + values
                ))

        customer["entities"] = entities
        customer["language"] = language

        customer.setdefault("messages", []).append({
            "role": "customer",
            "text": text,
            "language": language,
            "timestamp": stamp,
        })

        customer["messages"] = customer["messages"][-CONTEXT_LIMIT:]
        customer["last_active"] = stamp
        customer["active"] = True

        summary = customer.get("summary", "")
        context = (
            (summary + "\n") if summary else ""
        ) + build_summary(customer)

        clarification = (
            language_score < LANGUAGE_THRESHOLD
            or intent_score < INTENT_THRESHOLD
        )

        result = {
            "status": "clarification_needed" if clarification else "ok",
            "customer_id": customer_id,
            "language": language,
            "language_confidence": round(language_score, 2),
            "intent": intent,
            "intent_confidence": round(intent_score, 2),
            "clarification_needed": clarification,
            "reply": (
                "Could you clarify your language or what you need help with?"
                if clarification else None
            ),
            "requests": split_requests(text),
            "entities": entities,
            "context": context,
            "restored_summary": restored,
        }

        sessions[customer_id] = customer
        write_sessions(sessions)

        return result

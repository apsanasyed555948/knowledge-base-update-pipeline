
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ============================================================
# 1. CONFIGURATION
# ============================================================

TIMEZONE = "Asia/Kolkata"
BUSINESS_DAYS = {0, 1, 2, 3, 4}
BUSINESS_START = time(9, 0)
BUSINESS_END = time(18, 0)

NEGATIVE_ESCALATION_MINUTES = 15
REPEATED_NEGATIVE_THRESHOLD = 3

AUDIT_FILE = Path("escalation_audit.jsonl")

# ============================================================
# 2. SENTIMENT AND DETECTION RULES
# ============================================================

NEGATIVE_TERMS = [
    "angry", "frustrated", "terrible", "awful",
    "unacceptable", "disappointed", "useless",
    "not working", "still waiting", "fed up",
    "ridiculous", "poor service", "never works",
    "bad service", "hate this", "worst experience",
    "खराब", "परेशान", "गुस्सा", "निराश",
    "மோசம்", "பிரச்சனை", "muy mal", "enojado",
    "déçu", "inaceptable", "schlecht", "wütend",
    "unzufrieden", "mauvais", "furioso",
]

POSITIVE_TERMS = [
    "thank you", "thanks", "great", "excellent",
    "happy", "appreciate", "helpful", "well done",
    "धन्यवाद", "अच्छा", "நன்றி", "நல்லது",
    "gracias", "excelente", "merci", "super",
    "danke", "ausgezeichnet", "good service",
]

URGENT_TERMS = [
    "urgent", "immediately", "asap", "right now",
    "emergency", "can't access", "cannot access",
    "locked out", "security breach", "unauthorized login",
    "तुरंत", "अविलंब", "அவசரம்",
    "urgente", "ahora mismo", "sofort",
]

FRUSTRATION_TERMS = [
    "fed up", "furious", "annoyed", "frustrated",
    "ridiculous", "unacceptable", "wasting my time",
    "again and again", "still waiting",
]

SARCASM_PATTERNS = [
    r"\b(yeah|sure|great|wonderful|perfect)\b.*"
    r"\b(again|job|service|waiting|broken)\b",
    r"\b(thanks|thank you)\b.*\b(for nothing)\b",
    r"\bwhat a (great|wonderful|fantastic) service\b",
    r"\b(sure|great|wonderful|perfect),?\s+that helps\b",
    r"\b(oh|wow),?\s+(great|wonderful|fantastic)\b",
    r"\b(love|just love)\b.{0,40}\b(waiting|charged|broken|again)\b",
    r"\b(just what i needed|exactly what i wanted)\b",
    r"\b(another|yet another)\b.{0,40}\b(problem|issue|charge|failure)\b",
    r"\b(thanks|thank you)\b.{0,30}\b(again)\b.{0,20}\b(not)\b",
    r"वाह.*(बहुत अच्छा|शानदार)",
]

HIGH_RISK_PATTERNS = {
    "account_compromise": [
        r"\b(account|email|password)\b.{0,50}"
        r"\b(hacked|compromised|taken over)\b",
        r"\b(unauthori[sz]ed login|someone accessed my account|"
        r"account takeover|account was stolen)\b",
        r"\b(someone|somebody|a stranger)\b.{0,35}"
        r"\b(accessed|logged into|took over)\b.{0,25}\b(account)\b",
        r"मेरा अकाउंट.{0,30}(हैक|चोरी)",
    ],
    "duplicate_payment": [
        r"\b(charged twice|duplicate payment|paid twice|double charged)\b",
        r"\b(two charges|same payment twice|billed twice)\b",
        r"दो बार.{0,30}(पेमेंट|पैसे|चार्ज)",
    ],
    "legal_threat": [
        r"\b(lawyer|legal action|sue you|court case|"
        r"regulator|file a lawsuit)\b",
        r"\b(consumer protection|legal notice|take legal action)\b",
    ],
}

# ============================================================
# 3. DATA STRUCTURES
# ============================================================

@dataclass
class Analysis:
    sentiment: str
    sentiment_confidence: float
    frustration: float
    urgency: float
    sarcasm: float
    language: str
    risk_flags: list[str] = field(default_factory=list)
    negative: bool = False


@dataclass
class TriageResult:
    analysis: dict
    action: str
    queue: Optional[str]
    response_tone: str
    response_draft: str
    escalation: Optional[dict]
    next_working_day_at: Optional[str] = None


# ============================================================
# 4. TEXT AND LANGUAGE HELPERS
# ============================================================

def contains_any(text: str, terms: list[str]) -> bool:
    lower = text.casefold()

    for term in terms:
        pattern = r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)"
        if re.search(pattern, lower):
            return True

    return False


def detect_language_hint(text: str) -> str:
    """Identify a writing-system hint, not definitive language."""
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-or-related"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "zh"
    if re.search(r"[\u3040-\u30FF]", text):
        return "ja"
    if re.search(r"[A-Za-z]", text):
        return "en-or-latin-script"
    return "unknown"


def normalize_datetime(dt: datetime) -> datetime:
    tz = ZoneInfo(TIMEZONE)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)

    return dt.astimezone(tz)


def parse_timestamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return normalize_datetime(value)

    if isinstance(value, str):
        try:
            return normalize_datetime(datetime.fromisoformat(value))
        except ValueError:
            return None

    return None


# ============================================================
# 5. OPTIONAL MULTILINGUAL ML SENTIMENT
# ============================================================

_sentiment_pipeline = None


def optional_ml_sentiment(text: str) -> Optional[tuple[str, float]]:
    """Use a configured Transformers model when available."""
    global _sentiment_pipeline

    model_name = os.getenv("SENTIMENT_MODEL", "").strip()

    if not model_name:
        return None

    try:
        from transformers import pipeline

        if _sentiment_pipeline is None:
            _sentiment_pipeline = pipeline(
                "text-classification",
                model=model_name,
                tokenizer=model_name,
            )

        prediction = _sentiment_pipeline(
            text[:2000],
            truncation=True,
        )[0]

        label = str(prediction["label"]).casefold()
        confidence = float(prediction["score"])

        # Confirm the selected model's label mapping before use.
        if "negative" in label or label in {"label_0", "neg"}:
            sentiment = "negative"
        elif "positive" in label or label in {"label_2", "pos"}:
            sentiment = "positive"
        elif "neutral" in label or label in {"label_1", "neu"}:
            sentiment = "neutral"
        else:
            return None

        return sentiment, max(0.0, min(1.0, confidence))

    except Exception as exc:
        print(
            "Optional sentiment model unavailable; "
            f"using fallback rules. Details: {exc}"
        )
        return None


# ============================================================
# 6. MESSAGE ANALYSIS
# ============================================================

def analyze_message(message: str) -> Analysis:
    text = message.strip()

    sarcasm_hit = any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in SARCASM_PATTERNS
    )

    negative_hit = contains_any(text, NEGATIVE_TERMS)
    positive_hit = contains_any(text, POSITIVE_TERMS)
    urgent_hit = contains_any(text, URGENT_TERMS)
    frustration_hit = contains_any(text, FRUSTRATION_TERMS)

    risk_flags = [
        category
        for category, patterns in HIGH_RISK_PATTERNS.items()
        if any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in patterns
        )
    ]

    ml_result = optional_ml_sentiment(text)

    if ml_result is not None:
        sentiment, confidence = ml_result

        if sarcasm_hit and sentiment == "positive":
            sentiment = "negative"
            confidence = min(confidence, 0.75)

    else:
        if sarcasm_hit or negative_hit:
            sentiment = "negative"
            confidence = 0.72 if sarcasm_hit else 0.68
        elif positive_hit:
            sentiment = "positive"
            confidence = 0.70
        else:
            sentiment = "neutral"
            confidence = 0.55

    frustration = (
        0.90 if frustration_hit
        else 0.82 if (
            negative_hit or sarcasm_hit or sentiment == "negative"
        )
        else 0.18
    )

    urgency = (
        0.96 if risk_flags
        else 0.88 if urgent_hit
        else 0.15
    )

    sarcasm = 0.78 if sarcasm_hit else 0.10

    return Analysis(
        sentiment=sentiment,
        sentiment_confidence=round(confidence, 2),
        frustration=frustration,
        urgency=urgency,
        sarcasm=sarcasm,
        language=detect_language_hint(text),
        risk_flags=risk_flags,
        negative=(
            sentiment == "negative" or frustration >= 0.65
        ),
    )


# ============================================================
# 7. BUSINESS HOURS AND SCHEDULING
# ============================================================

def is_business_time(dt: datetime) -> bool:
    local = normalize_datetime(dt)

    return (
        local.weekday() in BUSINESS_DAYS
        and BUSINESS_START <= local.time().replace(tzinfo=None) < BUSINESS_END
    )


def next_working_open(dt: datetime) -> datetime:
    local = normalize_datetime(dt)

    candidate = local.replace(
        hour=BUSINESS_START.hour,
        minute=BUSINESS_START.minute,
        second=0,
        microsecond=0,
    )

    if (
        local.weekday() in BUSINESS_DAYS
        and local.time().replace(tzinfo=None) < BUSINESS_START
    ):
        return candidate

    candidate += timedelta(days=1)

    while candidate.weekday() not in BUSINESS_DAYS:
        candidate += timedelta(days=1)

    return candidate


# ============================================================
# 8. AUDIT LOGGING
# ============================================================

def make_audit_record(
    reason: str,
    condition: str,
    summary: str,
    now: datetime,
    analysis: Analysis,
    conversation_id: str,
) -> dict:
    return {
        "timestamp": normalize_datetime(now).isoformat(),
        "conversation_id": conversation_id,
        "reason": reason,
        "activated_condition": condition,
        "conversation_summary": summary[:1000],
        "analysis": asdict(analysis),
    }


def append_audit(
    record: dict,
    audit_path: Path = AUDIT_FILE,
) -> None:
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize_conversation(
    history: list[dict],
    current_message: str,
) -> str:
    customer_messages = [
        str(item.get("text", ""))
        for item in history
        if item.get("role", "customer") == "customer"
        and item.get("text")
    ]

    if not customer_messages or customer_messages[-1] != current_message:
        customer_messages.append(current_message)

    recent = customer_messages[-5:]
    return "Recent customer messages: " + " | ".join(recent)


# ============================================================
# 9. MAIN TRIAGE FUNCTION
# ============================================================

def triage(
    message: str,
    history: list[dict],
    conversation_started_at: datetime,
    now: datetime,
    conversation_id: str = "demo-001",
    unresolved: bool = True,
    write_audit: bool = False,
) -> TriageResult:

    now = normalize_datetime(now)
    conversation_started_at = normalize_datetime(
        conversation_started_at
    )

    analysis = analyze_message(message)

    customer_messages = [
        item for item in history
        if item.get("role", "customer") == "customer"
        and item.get("text")
    ]

    prior_analyses = [
        (item, analyze_message(str(item["text"])))
        for item in customer_messages
    ]

    negative_count = (
        sum(1 for _, a in prior_analyses if a.negative)
        + int(analysis.negative)
    )

    negative_timestamps = []

    for item, item_analysis in prior_analyses:
        if item_analysis.negative:
            timestamp = parse_timestamp(item.get("timestamp"))
            if timestamp is not None:
                negative_timestamps.append(timestamp)

    if analysis.negative:
        negative_timestamps.append(now)

    if negative_timestamps:
        negative_started_at = min(negative_timestamps)
    else:
        negative_started_at = None

    negative_elapsed_minutes = (
        max(
            0.0,
            (now - negative_started_at).total_seconds() / 60,
        )
        if negative_started_at is not None
        else 0.0
    )

    after_hours = not is_business_time(now)

    action = "respond"
    queue = None
    condition = None
    reason = None

    # Escalation priority:
    # 1. High risk
    # 2. Repeated negative messages
    # 3. Negative unresolved for more than 15 minutes
    # 4. Urgent after-hours complaint
    # 5. Other after-hours complaint

    if analysis.risk_flags:
        action = "escalate_now"
        queue = "priority_risk"
        condition = "high_risk:" + ",".join(analysis.risk_flags)
        reason = "High-risk issue detected regardless of sentiment"

    elif negative_count >= REPEATED_NEGATIVE_THRESHOLD:
        action = "escalate_now"
        queue = "customer_support_escalations"
        condition = f"repeated_negative_messages:{negative_count}"
        reason = "Repeated negative customer messages"

    elif (
        unresolved
        and negative_started_at is not None
        and negative_elapsed_minutes > NEGATIVE_ESCALATION_MINUTES
    ):
        action = "escalate_now"
        queue = "customer_support_escalations"
        condition = (
            "negative_unresolved_over_15_minutes:"
            f"{negative_elapsed_minutes:.1f}"
        )
        reason = (
            "Negative conversation remained unresolved "
            "beyond 15 minutes"
        )

    elif after_hours and analysis.urgency >= 0.75:
        action = "queue_on_call"
        queue = "on_call"
        condition = "urgent_outside_business_hours"
        reason = "Urgent complaint received outside business hours"

    elif after_hours:
        action = "schedule_next_working_day"
        queue = "next_working_day"
        condition = "normal_complaint_outside_business_hours"
        reason = "Non-urgent request received outside business hours"

    # Response tone does not alter business policies.

    if analysis.negative or analysis.frustration >= 0.65:
        tone = "empathetic, calm, accountable"
        draft = (
            "I'm sorry this has been frustrating. "
            "I'll review the details and help you "
            "with the next appropriate step."
        )
    elif analysis.sentiment == "positive":
        tone = "warm and concise"
        draft = "Thanks for reaching out! I'm happy to help."
    else:
        tone = "clear, neutral, helpful"
        draft = (
            "Thanks for contacting us. "
            "I'll help you review this."
        )

    escalation = None
    next_at = None

    if condition is not None:
        summary = summarize_conversation(history, message)

        escalation = make_audit_record(
            reason=reason or "Escalation",
            condition=condition,
            summary=summary,
            now=now,
            analysis=analysis,
            conversation_id=conversation_id,
        )

        if write_audit:
            append_audit(escalation)

    if action == "schedule_next_working_day":
        next_at = next_working_open(now).isoformat()

    return TriageResult(
        analysis=asdict(analysis),
        action=action,
        queue=queue,
        response_tone=tone,
        response_draft=draft,
        escalation=escalation,
        next_working_day_at=next_at,
    )


# ============================================================
# 10. TEST HELPERS
# ============================================================

def run_test(
    name: str,
    message: str,
    now: datetime,
    history: Optional[list[dict]] = None,
    started_at: Optional[datetime] = None,
) -> TriageResult:

    history = history or []
    started_at = started_at or (now - timedelta(minutes=2))

    result = triage(
        message=message,
        history=history,
        conversation_started_at=started_at,
        now=now,
        conversation_id=name,
        write_audit=False,
    )

    print("\n" + "=" * 65)
    print(f"TEST: {name}")
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))

    return result


# ============================================================
# 11. DEMO AND TESTS
# ============================================================

def demo() -> None:
    tz = ZoneInfo(TIMEZONE)

    after_hours = datetime.fromisoformat(
        "2026-10-22T20:30:00+05:30"
    ).astimezone(tz)

    business_hours = datetime.fromisoformat(
        "2026-10-22T10:30:00+05:30"
    ).astimezone(tz)

    run_test(
        "demo-sarcasm-risk",
        "Wonderful service, charged twice again. Please fix this.",
        after_hours,
        history=[{
            "role": "customer",
            "text": "I have been waiting for a solution",
            "timestamp": (
                after_hours - timedelta(minutes=18)
            ).isoformat(),
        }],
        started_at=after_hours - timedelta(minutes=20),
    )

    run_test(
        "demo-calm-risk",
        "I noticed two charges for the same payment. "
        "Could you please investigate?",
        business_hours,
    )

    run_test(
        "demo-after-hours-urgent",
        "URGENT: I cannot access my account right now.",
        after_hours,
    )

    run_test(
        "demo-after-hours-normal",
        "Could you please explain my latest invoice?",
        after_hours,
    )

    run_test(
        "demo-negative-timeout",
        "Can someone please review this?",
        business_hours,
        history=[{
            "role": "customer",
            "text": "This service is unacceptable.",
            "timestamp": (
                business_hours - timedelta(minutes=20)
            ).isoformat(),
        }],
        started_at=business_hours - timedelta(minutes=25),
    )

    run_test(
        "demo-repeated-negative",
        "This is still unacceptable.",
        business_hours,
        history=[
            {"role": "customer", "text": "Terrible service"},
            {"role": "customer", "text": "I am very frustrated"},
        ],
    )

    run_test(
        "demo-positive",
        "Thank you for the excellent help!",
        business_hours,
    )

    # Sarcasm without a high-risk issue.
    run_test(
        "demo-sarcasm",
        "What a wonderful service. I love waiting again.",
        business_hours,
    )

    # Multilingual keyword checks.
    run_test(
        "demo-hindi",
        "यह सेवा बहुत खराब है।",
        business_hours,
    )

    run_test(
        "demo-spanish",
        "El servicio es muy mal.",
        business_hours,
    )


if __name__ == "__main__":
    demo()

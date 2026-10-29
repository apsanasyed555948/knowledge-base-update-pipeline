
"""
Support Ticket Workflow
Python 3.10+

Run:
    python support_ticket_workflow.py --serve

The service accepts one JSON object at a time.
Finish multiline JSON with a blank line or END.
Type exit to stop.
"""

import argparse
import difflib
import json
import re
import threading
import time
import uuid
from datetime import datetime, date, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
TICKET_FILE = BASE_DIR / "tickets.jsonl"
AUDIT_FILE = BASE_DIR / "escalation_audit.jsonl"
CONFIG_FILE = BASE_DIR / "workflow_config.json"

DEFAULT_CONFIG = {
    "timezone": "Asia/Kolkata",
    "business_days": [0, 1, 2, 3, 4],
    "business_start": "09:00",
    "business_end": "18:00",
    "holidays": [],
    "sla_minutes": {
        "critical": 15,
        "high": 60,
        "medium": 240,
        "low": 720,
    },
    "warning_fraction": 0.75,
    "duplicate_similarity": 0.86,
    "related_similarity": 0.58,
    "monitor_interval": 30,
}

CONFIG = DEFAULT_CONFIG.copy()
CONFIG["sla_minutes"] = DEFAULT_CONFIG["sla_minutes"].copy()

LOCK = threading.RLock()
STOP_EVENT = threading.Event()
TICKETS = []
AGENTS = [
    {
        "id": "TECH",
        "name": "Technical Team",
        "team": "technical",
        "skills": ["account", "technical", "login", "security"],
        "available": True,
        "workload": 0,
    },
    {
        "id": "BILL",
        "name": "Billing Team",
        "team": "billing",
        "skills": ["payment", "billing", "refund", "order"],
        "available": True,
        "workload": 0,
    },
    {
        "id": "SHIP",
        "name": "Shipping Team",
        "team": "shipping",
        "skills": ["delivery", "shipping", "order"],
        "available": True,
        "workload": 0,
    },
    {
        "id": "GEN",
        "name": "General Support",
        "team": "general",
        "skills": ["general", "refund", "order", "account"],
        "available": True,
        "workload": 0,
    },
    {
        "id": "ONCALL",
        "name": "On-call Team",
        "team": "on_call",
        "skills": ["account", "technical", "security", "payment",
                   "billing", "refund", "delivery", "shipping",
                   "order", "general"],
        "available": True,
        "workload": 0,
    },
]

RISK_PATTERNS = {
    "account_compromise": [
        "account hacked",
        "account has been hacked",
        "hacked account",
        "hacked my account",
        "account compromised",
        "account was compromised",
        "unauthorized access",
        "someone accessed my account",
        "account was stolen",
        "stolen account",
        "someone hacked",
        "my account was hacked",
    ],
    "duplicate_payment": [
        "charged twice",
        "charged two times",
        "duplicate payment",
        "paid twice",
        "double charged",
        "same payment twice",
        "deducted twice",
        "debited twice",
        "payment taken twice",
        "charged me twice",
        "two payments",
    ],
    "legal_threat": [
        "legal action",
        "take legal action",
        "sue you",
        "sue the company",
        "my lawyer",
        "my attorney",
        "court case",
        "take you to court",
        "report you to court",
    ],
}

URGENT_TERMS = [
    "urgent", "immediately", "emergency", "right now",
    "cannot access", "can't access", "hacked",
    "fraud", "stolen", "charged twice",
]

NEGATIVE_TERMS = [
    "angry", "unacceptable", "terrible", "worst",
    "disappointed", "complaint", "frustrated",
    "not working", "still waiting", "failed", "delay",
    "problem", "issue", "never arrived", "charged twice",
]

POSITIVE_TERMS = [
    "thank you", "thanks", "excellent", "great",
    "wonderful", "happy", "resolved", "appreciate",
]

SARCASM_TERMS = [
    "great job", "wonderful service", "just perfect",
    "yeah right", "what a joke", "thanks for nothing",
]


def now():
    return datetime.now(ZoneInfo(CONFIG["timezone"]))


def parse_datetime(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(CONFIG["timezone"]))
    return parsed.astimezone(ZoneInfo(CONFIG["timezone"]))


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def audit_event(ticket, event, reason, condition):
    append_jsonl(AUDIT_FILE, {
        "timestamp": now().isoformat(),
        "ticket_id": ticket.get("ticket_id"),
        "event": event,
        "reason": reason,
        "condition": condition,
        "summary": ticket.get("handoff_summary", ""),
    })


def save_tickets():
    """Persist the latest version of every ticket."""
    with LOCK:
        temp = TICKET_FILE.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as file:
            for ticket in TICKETS:
                file.write(json.dumps(ticket, ensure_ascii=False) + "\n")
        temp.replace(TICKET_FILE)


def load_config():
    global CONFIG

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as file:
                saved = json.load(file)
            merged = dict(DEFAULT_CONFIG)
            merged.update(saved)
            merged["sla_minutes"] = {
                **DEFAULT_CONFIG["sla_minutes"],
                **saved.get("sla_minutes", {}),
            }
            validate_config(merged)
            CONFIG = merged
        except (OSError, ValueError, TypeError, KeyError):
            CONFIG = dict(DEFAULT_CONFIG)
            CONFIG["sla_minutes"] = DEFAULT_CONFIG["sla_minutes"].copy()


def validate_config(config):
    ZoneInfo(config["timezone"])

    start = dt_time.fromisoformat(config["business_start"])
    end = dt_time.fromisoformat(config["business_end"])
    if start >= end:
        raise ValueError("business_start must be before business_end")

    if not config["business_days"]:
        raise ValueError("business_days cannot be empty")

    if any(day not in range(7) for day in config["business_days"]):
        raise ValueError("business_days must contain values from 0 to 6")

    if not 0 < float(config["warning_fraction"]) < 1:
        raise ValueError("warning_fraction must be between 0 and 1")

    for level in ("critical", "high", "medium", "low"):
        value = int(config["sla_minutes"][level])
        if value <= 0:
            raise ValueError(f"SLA for {level} must be positive")

    for holiday in config["holidays"]:
        date.fromisoformat(holiday)

    if int(config["monitor_interval"]) <= 0:
        raise ValueError("monitor_interval must be positive")


def update_config(changes):
    global CONFIG

    if not isinstance(changes, dict):
        raise ValueError("config must be a JSON object")

    candidate = dict(CONFIG)
    candidate["sla_minutes"] = dict(CONFIG["sla_minutes"])

    for key, value in changes.items():
        if key == "sla_minutes":
            if not isinstance(value, dict):
                raise ValueError("sla_minutes must be an object")
            candidate["sla_minutes"].update(value)
        elif key in DEFAULT_CONFIG:
            candidate[key] = value
        else:
            raise ValueError(f"Unknown configuration setting: {key}")

    validate_config(candidate)

    with LOCK:
        CONFIG = candidate
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(CONFIG, file, indent=2)

        # Apply new SLA limits to existing tickets.
        for ticket in TICKETS:
            ticket["sla_limit_minutes"] = CONFIG["sla_minutes"][
                ticket["priority"]
            ]
            ticket["sla_warning"] = False
            ticket["sla_breached"] = False
            ticket["sla_elapsed_minutes"] = calculate_elapsed(ticket)

        save_tickets()

    return {"message": "Configuration updated", "config": CONFIG}


def is_business_day(day):
    return (
        day.weekday() in CONFIG["business_days"]
        and day.isoformat() not in CONFIG["holidays"]
    )


def business_minutes_between(start, end):
    """Count minutes inside configured business hours only."""
    if end <= start:
        return 0.0

    zone = ZoneInfo(CONFIG["timezone"])
    start = start.astimezone(zone)
    end = end.astimezone(zone)
    total = 0.0
    day = start.date()

    while day <= end.date():
        if is_business_day(day):
            opening = datetime.combine(
                day, dt_time.fromisoformat(CONFIG["business_start"]), tzinfo=zone
            )
            closing = datetime.combine(
                day, dt_time.fromisoformat(CONFIG["business_end"]), tzinfo=zone
            )

            segment_start = max(start, opening)
            segment_end = min(end, closing)

            if segment_end > segment_start:
                total += (segment_end - segment_start).total_seconds() / 60

        day += timedelta(days=1)

    return total


def next_business_open(moment=None):
    zone = ZoneInfo(CONFIG["timezone"])
    moment = (moment or now()).astimezone(zone)
    opening_time = dt_time.fromisoformat(CONFIG["business_start"])
    closing_time = dt_time.fromisoformat(CONFIG["business_end"])

    for offset in range(0, 370):
        day = moment.date() + timedelta(days=offset)
        if not is_business_day(day):
            continue

        opening = datetime.combine(day, opening_time, tzinfo=zone)
        closing = datetime.combine(day, closing_time, tzinfo=zone)

        if offset == 0:
            if moment < opening:
                return opening
            if opening <= moment < closing:
                return moment

        else:
            return opening

    raise RuntimeError("No business opening found in the next 370 days")


def is_business_hours(moment=None):
    moment = (moment or now()).astimezone(ZoneInfo(CONFIG["timezone"]))
    if not is_business_day(moment.date()):
        return False

    start = dt_time.fromisoformat(CONFIG["business_start"])
    end = dt_time.fromisoformat(CONFIG["business_end"])
    return start <= moment.timetz().replace(tzinfo=None) < end


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def text_similarity(first, second):
    first = normalize(first)
    second = normalize(second)
    if not first or not second:
        return 0.0
    return difflib.SequenceMatcher(None, first, second).ratio()


def mask_contact(contact):
    contact = str(contact or "")
    if "@" in contact:
        name, domain = contact.split("@", 1)
        return (name[:1] + "***@" + domain) if name else "***@" + domain
    if len(contact) <= 4:
        return "*" * len(contact)
    return contact[:2] + "*" * (len(contact) - 4) + contact[-2:]


def extract_fields(data):
    """Accept explicit fields and attempt basic extraction from a message."""
    result = dict(data)
    message = str(data.get("message", "") or data.get("conversation", ""))

    if not result.get("contact"):
        match = re.search(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            message,
            re.IGNORECASE,
        )
        if match:
            result["contact"] = match.group(0)

    if not result.get("order_id"):
        match = re.search(r"\b(?:ORD|ORDER)[-_ ]?[A-Z0-9]{3,}\b", message, re.I)
        if match:
            result["order_id"] = match.group(0)

    result["message"] = message or str(result.get("issue", ""))
    result["issue"] = str(result.get("issue", "") or message)

    for field in ("customer", "order_id", "product", "issue", "contact"):
        result[field] = str(result.get(field, "") or "").strip()

    result["evidence"] = str(result.get("evidence", "") or "")
    return result


def analyze(data):
    text = normalize(
        " ".join([
            str(data.get("issue", "")),
            str(data.get("message", "")),
        ])
    )

    risk_flags = []
    for flag, phrases in RISK_PATTERNS.items():
        if any(phrase in text for phrase in phrases):
            risk_flags.append(flag)

    sarcasm = any(term in text for term in SARCASM_TERMS)
    urgent = any(term in text for term in URGENT_TERMS) or bool(risk_flags)

    if sarcasm or any(term in text for term in NEGATIVE_TERMS):
        sentiment = "negative"
        confidence = 0.78 if sarcasm else 0.72
    elif any(term in text for term in POSITIVE_TERMS):
        sentiment = "positive"
        confidence = 0.75
    else:
        sentiment = "neutral"
        confidence = 0.60

    severity = "low"
    impact = "low"

    if "account_compromise" in risk_flags:
        severity, impact = "critical", "high"
    elif "duplicate_payment" in risk_flags:
        severity, impact = "critical", "high"
    elif "legal_threat" in risk_flags:
        severity, impact = "critical", "high"
    elif urgent:
        severity = "high"
    elif sentiment == "negative":
        severity = "medium"

    if any(term in text for term in ("many customers", "all users", "everyone affected")):
        impact = "high"

    frustration = 0.82 if sentiment == "negative" else 0.18

    return {
        "sentiment": sentiment,
        "sentiment_confidence": confidence,
        "severity": severity,
        "impact": impact,
        "urgent": urgent,
        "sarcasm": sarcasm,
        "frustration": frustration,
        "risk_flags": risk_flags,
    }


def calculate_priority(analysis, created_at):
    """Combine severity, sentiment, urgency, and wait time."""
    severity_scores = {
        "critical": 100,
        "high": 65,
        "medium": 40,
        "low": 20,
    }

    score = severity_scores.get(analysis["severity"], 20)

    if analysis["sentiment"] == "negative":
        score += 10
    if analysis["urgent"]:
        score += 10
    if analysis["impact"] == "high":
        score += 10

    elapsed = business_minutes_between(created_at, now())
    score += min(20, int(elapsed // 15))

    if analysis["risk_flags"]:
        score = 100

    if score >= 85:
        priority = "critical"
    elif score >= 60:
        priority = "high"
    elif score >= 35:
        priority = "medium"
    else:
        priority = "low"

    return min(score, 100), priority


def required_skills(data, analysis):
    product = normalize(data.get("product", ""))
    issue = normalize(data.get("issue", ""))

    if "account_compromise" in analysis["risk_flags"]:
        return ["security"]

    if "duplicate_payment" in analysis["risk_flags"]:
        return ["payment"]

    if any(word in product + " " + issue for word in
           ("payment", "billing", "refund", "charged", "payment")):
        return ["billing"]

    if any(word in product + " " + issue for word in
           ("delivery", "shipping", "package", "shipment")):
        return ["shipping"]

    if any(word in product + " " + issue for word in
           ("account", "login", "hacked", "password", "technical")):
        return ["technical"]

    return ["general"]


def choose_regular_team(skills):
    skill = skills[0]
    if skill in ("security", "technical"):
        return "technical"
    if skill in ("payment", "billing"):
        return "billing"
    if skill in ("shipping", "delivery"):
        return "shipping"
    return "general"


def route_ticket(ticket, analysis, skills):
    outside_hours = not is_business_hours()
    needs_on_call = outside_hours and (
        analysis["urgent"]
        or analysis["severity"] in ("high", "critical")
        or bool(analysis["risk_flags"])
    )

    team = "on_call" if needs_on_call else choose_regular_team(skills)

    candidates = [
        agent for agent in AGENTS
        if agent["available"]
        and any(skill in agent["skills"] for skill in skills)
        and (
            (team == "on_call" and agent["id"] == "ONCALL")
            or (team != "on_call" and agent["id"] != "ONCALL"
                and agent["team"] == team)
        )
    ]

    # No incorrect fallback to General Support or Billing for on-call tickets.
    if not candidates:
        ticket["assigned_team"] = team
        ticket["assigned_agent"] = None
        ticket["status"] = "unassigned"
    else:
        agent = min(candidates, key=lambda item: item["workload"])
        agent["workload"] += 1
        ticket["assigned_team"] = team
        ticket["assigned_agent"] = agent["name"]
        ticket["status"] = "open"

    if outside_hours and not needs_on_call:
        ticket["status"] = "scheduled"
        ticket["scheduled_for"] = next_business_open().isoformat()
    else:
        ticket["scheduled_for"] = None

    if analysis["risk_flags"]:
        ticket["escalation_reason"] = (
            "High-risk issue detected: " + ", ".join(analysis["risk_flags"])
        )
        ticket["escalation_condition"] = "high_risk:" + ",".join(
            analysis["risk_flags"]
        )
    elif needs_on_call:
        ticket["escalation_reason"] = "Urgent complaint received outside business hours"
        ticket["escalation_condition"] = "urgent_after_hours"

    return ticket


def make_handoff_summary(ticket):
    return (
        f"Ticket {ticket['ticket_id']}: "
        f"customer={ticket['customer']}; "
        f"order={ticket['order_id']}; "
        f"product={ticket['product']}; "
        f"issue={ticket['issue']}; "
        f"priority={ticket['priority']}; "
        f"sentiment={ticket['sentiment']}; "
        f"contact={mask_contact(ticket['contact'])}; "
        f"risks={','.join(ticket['risk_flags']) or 'none'}"
    )




def find_duplicate(data):
    for old in reversed(TICKETS):
        if old.get("status") == "duplicate":
            continue

        old_order = normalize(old.get("order_id", ""))
        new_order = normalize(data.get("order_id", ""))

        old_customer = normalize(old.get("customer", ""))
        new_customer = normalize(data.get("customer", ""))

        same_order = bool(new_order) and old_order == new_order
        same_customer = bool(new_customer) and old_customer == new_customer

        same_issue = (
            text_similarity(
                data.get("issue", ""),
                old.get("issue", "")
            ) >= CONFIG["duplicate_similarity"]
        )

        # Only flag a duplicate when customer, order ID,
        # and issue all match.
        if same_order and same_customer and same_issue:
            return old

    return None




def related_group(data):
    for old in reversed(TICKETS):
        if old.get("status") == "duplicate":
            continue
        similarity = text_similarity(data.get("issue"), old.get("issue"))
        if similarity >= CONFIG["related_similarity"]:
            return old.get("related_group") or f"RG-{old['ticket_id']}"
    return None


def calculate_elapsed(ticket):
    try:
        created = parse_datetime(ticket["created_at"])
        return round(business_minutes_between(created, now()), 2)
    except (KeyError, ValueError, TypeError):
        return 0.0


def process_ticket(raw_data):
    if not isinstance(raw_data, dict):
        raise ValueError("Ticket input must be a JSON object")

    if raw_data.get("action") == "config_update":
        return update_config(raw_data.get("config", {
            key: value for key, value in raw_data.items()
            if key not in ("action",)
        }))

    data = extract_fields(raw_data)
    missing = [
        field for field in ("customer", "order_id", "product", "issue", "contact")
        if not data.get(field)
    ]

    created = now()
    ticket_id = "T-" + created.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()

    duplicate = find_duplicate(data)
    if duplicate:
        result = {
            "ticket_id": ticket_id,
            "customer": data["customer"],
            "order_id": data["order_id"],
            "product": data["product"],
            "issue": data["issue"],
            "evidence": data["evidence"],
            "contact": mask_contact(data["contact"]),
            "message": data["message"],
            "created_at": created.isoformat(),
            "status": "duplicate",
            "missing_fields": missing,
            "duplicate_of": duplicate["ticket_id"],
            "assigned_team": duplicate.get("assigned_team"),
            "assigned_agent": duplicate.get("assigned_agent"),
            "priority": duplicate.get("priority"),
            "risk_flags": duplicate.get("risk_flags", []),
        }
        append_jsonl(AUDIT_FILE, {
            "timestamp": created.isoformat(),
            "ticket_id": ticket_id,
            "event": "duplicate_detected",
            "reason": "Matching existing ticket",
            "condition": f"duplicate_of:{duplicate['ticket_id']}",
            "summary": f"Duplicate of {duplicate['ticket_id']}",
        })
        return result

    analysis = analyze(data)
    score, priority = calculate_priority(analysis, created)

    ticket = {
        "ticket_id": ticket_id,
        "customer": data["customer"],
        "order_id": data["order_id"],
        "product": data["product"],
        "issue": data["issue"],
        "evidence": data["evidence"],
        "contact": mask_contact(data["contact"]),
        "message": data["message"],
        "created_at": created.isoformat(),
        "status": "open",
        "missing_fields": missing,
        "sentiment": analysis["sentiment"],
        "sentiment_confidence": analysis["sentiment_confidence"],
        "severity": analysis["severity"],
        "impact": analysis["impact"],
        "urgent": analysis["urgent"],
        "sarcasm": analysis["sarcasm"],
        "frustration": analysis["frustration"],
        "risk_flags": analysis["risk_flags"],
        "priority_score": score,
        "priority": priority,
        "assigned_team": None,
        "assigned_agent": None,
        "scheduled_for": None,
        "duplicate_of": None,
        "related_group": related_group(data),
        "sla_limit_minutes": CONFIG["sla_minutes"][priority],
        "sla_elapsed_minutes": 0,
        "sla_warning": False,
        "sla_breached": False,
        "escalation_reason": None,
        "escalation_condition": None,
    }

    skills = required_skills(data, analysis)
    route_ticket(ticket, analysis, skills)
    ticket["handoff_summary"] = make_handoff_summary(ticket)

    with LOCK:
        TICKETS.append(ticket)
        save_tickets()

    if ticket.get("escalation_reason"):
        audit_event(
            ticket,
            "escalation_triggered",
            ticket["escalation_reason"],
            ticket.get("escalation_condition", "routing_escalation"),
        )

    if missing:
        ticket["status"] = "needs_information"
        ticket["missing_fields"] = missing

    return ticket


def monitor_sla():
    """Warn at the configured threshold and escalate when SLA is breached."""
    while not STOP_EVENT.wait(int(CONFIG["monitor_interval"])):
        with LOCK:
            changed = False

            for ticket in TICKETS:
                if ticket.get("status") in ("duplicate", "closed", "resolved"):
                    continue

                elapsed = calculate_elapsed(ticket)
                ticket["sla_elapsed_minutes"] = elapsed
                limit = float(ticket.get("sla_limit_minutes") or 0)
                if limit <= 0:
                    continue

                warning_at = limit * float(CONFIG["warning_fraction"])

                if elapsed >= warning_at and not ticket.get("sla_warning"):
                    ticket["sla_warning"] = True
                    audit_event(
                        ticket,
                        "sla_warning",
                        f"SLA warning at {elapsed:.1f} business minutes",
                        "sla_warning_threshold",
                    )
                    changed = True

                if elapsed >= limit and not ticket.get("sla_breached"):
                    ticket["sla_breached"] = True
                    ticket["status"] = "escalated"
                    ticket["escalation_reason"] = (
                        f"Automatic escalation: SLA breached after {elapsed:.1f} "
                        "business minutes"
                    )
                    ticket["escalation_condition"] = "sla_breach"
                    audit_event(
                        ticket,
                        "sla_breach",
                        ticket["escalation_reason"],
                        "sla_breach",
                    )
                    changed = True

            if changed:
                save_tickets()


def load_tickets():
    if not TICKET_FILE.exists():
        return

    loaded = {}
    with open(TICKET_FILE, encoding="utf-8") as file:
        for line in file:
            try:
                ticket = json.loads(line)
                if ticket.get("ticket_id"):
                    loaded[ticket["ticket_id"]] = ticket
            except json.JSONDecodeError:
                continue

    with LOCK:
        TICKETS.clear()
        TICKETS.extend(loaded.values())

        for agent in AGENTS:
            agent["workload"] = sum(
                1 for ticket in TICKETS
                if ticket.get("assigned_agent") == agent["name"]
                and ticket.get("status") not in ("closed", "resolved", "duplicate")
            )


def read_json_object():
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            return None

        if line.strip().lower() == "exit" and not lines:
            return "exit"

        if line.strip() == "END":
            break

        if not line.strip():
            if lines:
                break
            continue

        lines.append(line)

        # Support a complete one-line JSON object without waiting for a blank line.
        if len(lines) == 1:
            try:
                json.loads(lines[0])
                break
            except json.JSONDecodeError:
                pass

    if not lines:
        return {}

    return json.loads("\n".join(lines))


def serve():
    load_config()
    load_tickets()

    monitor = threading.Thread(target=monitor_sla, daemon=True)
    monitor.start()

    print("Support Ticket Workflow is running. Enter customer conversations as JSON.")
    print("Type exit to stop.")
    print("Enter JSON. Finish multi-line JSON with a blank line or END.")

    try:
        while True:
            print("Customer conversation: ", end="", flush=True)
            try:
                data = read_json_object()
            except (json.JSONDecodeError, ValueError) as exc:
                print(json.dumps({"error": f"Invalid input: {exc}"}))
                continue

            if data is None or data == "exit":
                break
            if not data:
                continue

            try:
                result = process_ticket(data)
                print(json.dumps(result, indent=2, ensure_ascii=False))
            except Exception as exc:
                print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    finally:
        STOP_EVENT.set()
        monitor.join(timeout=2)
        print("Support Ticket Workflow stopped.")


def main():
    parser = argparse.ArgumentParser(description="Support Ticket Workflow")
    parser.add_argument("--serve", action="store_true", help="Run interactive JSON service")
    args = parser.parse_args()

    if args.serve:
        serve()
    else:
        load_config()
        load_tickets()
        print("Run with --serve to start the interactive service.")


if __name__ == "__main__":
    main()

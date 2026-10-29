
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from complaint_triage import (
    TIMEZONE,
    NEGATIVE_ESCALATION_MINUTES,
    analyze_message,
    append_audit,
    make_audit_record,
    normalize_datetime,
    summarize_conversation,
)


# ============================================================
# 1. CONVERSATION STATE
# ============================================================

@dataclass
class TrackedConversation:
    conversation_id: str
    started_at: datetime
    last_message: str
    history: list[dict] = field(default_factory=list)
    unresolved: bool = True
    escalated: bool = False
    negative_started_at: Optional[datetime] = None
    negative_message: Optional[str] = None


# ============================================================
# 2. ESCALATION SCHEDULER
# ============================================================

class EscalationScheduler:

    def __init__(
        self,
        interval_seconds: int = 60,
        audit_path: Path = Path("escalation_audit.jsonl"),
    ):
        self.interval_seconds = interval_seconds
        self.audit_path = audit_path

        self._lock = threading.RLock()
        self._conversations: dict[str, TrackedConversation] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --------------------------------------------------------
    # Start tracking a conversation
    # --------------------------------------------------------

    def start_conversation(
        self,
        conversation_id: str,
        message: str,
        timestamp: datetime,
        history: Optional[list[dict]] = None,
    ) -> None:

        timestamp = normalize_datetime(timestamp)
        previous_messages = list(history or [])

        state = TrackedConversation(
            conversation_id=conversation_id,
            started_at=timestamp,
            last_message=message,
            history=previous_messages,
        )

        # Find the earliest negative customer message in history.
        for item in previous_messages:
            if item.get("role", "customer") != "customer":
                continue

            text = str(item.get("text", ""))
            message_time = item.get("timestamp")

            if not text or not analyze_message(text).negative:
                continue

            try:
                if isinstance(message_time, str):
                    message_time = datetime.fromisoformat(message_time)

                if isinstance(message_time, datetime):
                    message_time = normalize_datetime(message_time)
                else:
                    continue

            except (TypeError, ValueError):
                continue

            if (
                state.negative_started_at is None
                or message_time < state.negative_started_at
            ):
                state.negative_started_at = message_time
                state.negative_message = text

        state.history.append({
            "role": "customer",
            "text": message,
            "timestamp": timestamp.isoformat(),
        })

        if analyze_message(message).negative:
            if state.negative_started_at is None:
                state.negative_started_at = timestamp
                state.negative_message = message

        with self._lock:
            self._conversations[conversation_id] = state

    # --------------------------------------------------------
    # Add a new message
    # --------------------------------------------------------

    def add_message(
        self,
        conversation_id: str,
        message: str,
        timestamp: datetime,
        role: str = "customer",
    ) -> None:

        timestamp = normalize_datetime(timestamp)

        with self._lock:
            state = self._conversations.get(conversation_id)

            if state is None:
                raise KeyError(
                    f"Conversation not found: {conversation_id}"
                )

            if not state.unresolved:
                raise ValueError(
                    "Cannot add messages to a resolved conversation."
                )

            state.history.append({
                "role": role,
                "text": message,
                "timestamp": timestamp.isoformat(),
            })

            state.last_message = message

            if role == "customer":
                analysis = analyze_message(message)

                if analysis.negative:
                    if (
                        state.negative_started_at is None
                        or timestamp < state.negative_started_at
                    ):
                        state.negative_started_at = timestamp
                        state.negative_message = message

    # --------------------------------------------------------
    # Resolve a conversation
    # --------------------------------------------------------

    def resolve_conversation(self, conversation_id: str) -> None:
        with self._lock:
            state = self._conversations.get(conversation_id)

            if state is None:
                raise KeyError(
                    f"Conversation not found: {conversation_id}"
                )

            state.unresolved = False

    # --------------------------------------------------------
    # Check simulated or real time
    # --------------------------------------------------------

    def check_timeouts(
        self,
        now: Optional[datetime] = None,
    ) -> list[dict]:

        if now is None:
            now = datetime.now(ZoneInfo(TIMEZONE))
        else:
            now = normalize_datetime(now)

        triggered = []

        with self._lock:
            for state in self._conversations.values():

                if not state.unresolved or state.escalated:
                    continue

                if state.negative_started_at is None:
                    continue

                elapsed_minutes = (
                    now - state.negative_started_at
                ).total_seconds() / 60

                # Escalate only when more than 15 minutes have passed.
                if elapsed_minutes <= NEGATIVE_ESCALATION_MINUTES:
                    continue

                negative_message = (
                    state.negative_message or state.last_message
                )
                analysis = analyze_message(negative_message)

                reason = (
                    "Automatic escalation: negative conversation "
                    f"unresolved for {elapsed_minutes:.1f} minutes"
                )

                condition = "scheduler_timeout_over_15_minutes"

                summary = summarize_conversation(
                    state.history,
                    state.last_message,
                )

                record = make_audit_record(
                    reason=reason,
                    condition=condition,
                    summary=summary,
                    now=now,
                    analysis=analysis,
                    conversation_id=state.conversation_id,
                )

                record["elapsed_minutes"] = round(elapsed_minutes, 2)
                record["queue"] = "customer_support_escalations"

                # Persist the record before marking it escalated.
                append_audit(record, self.audit_path)

                state.escalated = True
                triggered.append(record)

        return triggered

    # --------------------------------------------------------
    # Background polling
    # --------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                records = self.check_timeouts()

                for record in records:
                    print(
                        "AUTO-ESCALATED:",
                        record["conversation_id"],
                        record["reason"],
                    )

            except Exception as exc:
                print("Scheduler check failed:", exc)

            self._stop_event.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._run_loop,
            name="complaint-escalation-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 2)


# ============================================================
# 3. SIMULATED-TIME DEMO
# ============================================================

def demo() -> None:
    tz = ZoneInfo(TIMEZONE)

    audit_path = Path("scheduler_demo_audit.jsonl")

    # Use a fresh audit file for each demo run.
    audit_path.write_text("", encoding="utf-8")

    scheduler = EscalationScheduler(
        interval_seconds=60,
        audit_path=audit_path,
    )

    start = datetime.fromisoformat(
        "2026-10-22T10:00:00+05:30"
    ).astimezone(tz)

    scheduler.start_conversation(
        conversation_id="demo-timeout-001",
        message="I am still waiting and this is unacceptable.",
        timestamp=start,
    )

    # Exactly 15 minutes: must not escalate.
    at_15_minutes = start + timedelta(minutes=15)
    result_15 = scheduler.check_timeouts(at_15_minutes)

    print("At 15 minutes:", len(result_15), "escalations")

    # At 16 minutes: should escalate once.
    at_16_minutes = start + timedelta(minutes=16)
    result_16 = scheduler.check_timeouts(at_16_minutes)

    print("At 16 minutes:", len(result_16), "escalations")

    for record in result_16:
        print("Conversation:", record["conversation_id"])
        print("Reason:", record["reason"])
        print("Condition:", record["activated_condition"])
        print("Summary:", record["conversation_summary"])

    # Further checks must not create duplicate escalations.
    result_again = scheduler.check_timeouts(
        start + timedelta(minutes=20)
    )
    print("Duplicate escalations:", len(result_again))

    # Resolved conversations must not be escalated.
    scheduler.start_conversation(
        conversation_id="demo-resolved-002",
        message="This is unacceptable.",
        timestamp=start,
    )

    scheduler.resolve_conversation("demo-resolved-002")

    resolved_result = scheduler.check_timeouts(
        start + timedelta(minutes=20)
    )
    print(
        "Resolved conversation escalations:",
        len(resolved_result),
    )

    print("Audit file:", audit_path.resolve())


if __name__ == "__main__":
    demo()

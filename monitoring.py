
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"
LOGS_DIR = ROOT / "logs"
METRICS_PATH = LOGS_DIR / "metrics.jsonl"


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def record_metric(
    latency_ms,
    success,
    confidence,
    escalated=False,
):
    metric = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_ms": float(latency_ms),
        "success": bool(success),
        "confidence": float(confidence),
        "escalated": bool(escalated),
    }

    if not 0 <= metric["confidence"] <= 1:
        raise ValueError("Confidence must be between 0 and 1")

    if metric["latency_ms"] < 0:
        raise ValueError("Latency cannot be negative")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    with METRICS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(metric) + "\n")

    return metric


def read_metrics():
    if not METRICS_PATH.exists():
        return []

    metrics = []

    with METRICS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                metrics.append(json.loads(line))

    return metrics


def summarize_metrics():
    metrics = read_metrics()

    if not metrics:
        return {
            "sample_count": 0,
            "status": "NO_DATA",
        }

    count = len(metrics)
    failures = sum(not item["success"] for item in metrics)
    escalations = sum(item["escalated"] for item in metrics)

    summary = {
        "sample_count": count,
        "average_latency_ms": round(
            mean(item["latency_ms"] for item in metrics), 2
        ),
        "failure_rate": failures / count,
        "average_confidence": round(
            mean(item["confidence"] for item in metrics), 3
        ),
        "escalation_rate": escalations / count,
    }

    config = load_config()

    problems = []

    if summary["average_latency_ms"] > config["max_latency_ms"]:
        problems.append("latency_threshold_exceeded")

    if summary["failure_rate"] > config["max_failure_rate"]:
        problems.append("failure_rate_threshold_exceeded")

    if summary["escalation_rate"] > config["max_escalation_rate"]:
        problems.append("escalation_rate_threshold_exceeded")

    if summary["average_confidence"] < config["minimum_confidence"]:
        problems.append("confidence_threshold_not_met")

    summary["status"] = "HEALTHY" if not problems else "UNHEALTHY"
    summary["problems"] = problems

    return summary


def timed_operation(operation):
    start = time.perf_counter()

    try:
        result = operation()
        elapsed_ms = (time.perf_counter() - start) * 1000

        record_metric(
            latency_ms=elapsed_ms,
            success=True,
            confidence=1.0,
        )

        return result

    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000

        record_metric(
            latency_ms=elapsed_ms,
            success=False,
            confidence=0.0,
        )

        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        record_metric(120, True, 0.95, False)
        record_metric(250, True, 0.85, True)
        record_metric(500, False, 0.30, False)

    print(json.dumps(summarize_metrics(), indent=2))


import json
import time
import argparse
from monitoring import summarize_metrics
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quality_checker import evaluate_version
from version_manager import activate_version, rollback
from security_manager import authorize


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"
LOGS_DIR = ROOT / "logs"
DEPLOYMENT_LOG = LOGS_DIR / "deployment.jsonl"


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def log_event(event):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = datetime.now(
        ZoneInfo(load_config()["timezone"])
    ).isoformat()

    with DEPLOYMENT_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")


def in_maintenance_window(now=None):
    config = load_config()
    zone = ZoneInfo(config["timezone"])
    now = now or datetime.now(zone)

    start_hour, start_minute = map(
        int, config["maintenance_window_start"].split(":")
    )
    end_hour, end_minute = map(
        int, config["maintenance_window_end"].split(":")
    )

    current = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute

    if start <= end:
        return start <= current < end

    return current >= start or current < end


def next_scheduled_time():
    config = load_config()
    zone = ZoneInfo(config["timezone"])
    now = datetime.now(zone)

    hour, minute = map(
        int, config["scheduled_update_time"].split(":")
    )
    scheduled = now.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )

    if scheduled <= now:
        scheduled += timedelta(days=1)

    return scheduled



def run_health_checks(config, simulate_failure=False):
    """Monitor deployment health for the configured period."""
    duration = config["health_check_duration_minutes"] * 60
    interval = config["health_check_interval_seconds"]
    deadline = time.monotonic() + duration

    while True:
        if simulate_failure:
            print("Simulated health-check failure.")
            return False

        metrics = summarize_metrics()

        if metrics.get("status") == "UNHEALTHY":
            print("Health check failed:", metrics.get("problems", []))
            return False

        if metrics.get("status") == "NO_DATA":
            print("Health check failed: no monitoring data available.")
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True

        time.sleep(min(interval, remaining))

def deploy(version_name, simulate_failure=False):
    config = load_config()
    authorize("deployer", "deploy")

    if not in_maintenance_window() and not simulate_failure:
        log_event({
            "status": "deferred",
            "version": version_name,
            "reason": "Outside maintenance window",
        })
        print("Deployment deferred: outside maintenance window.")
        print("Next configured schedule:", next_scheduled_time())
        return False

    if not evaluate_version(version_name):
        log_event({
            "status": "rejected",
            "version": version_name,
            "reason": "Quality checks failed",
        })
        print("Deployment rejected by quality checks.")
        return False

    retry_delays = config["retry_delays_minutes"]

    for attempt in range(len(retry_delays) + 1):
        previous_version = None

        try:
            active_file = ROOT / "versions" / "active_version.json"
            if active_file.exists():
                with active_file.open("r", encoding="utf-8") as file:
                    previous_version = json.load(file).get("version")

            activate_version(version_name)

            log_event({
                "status": "activated",
                "version": version_name,
                "previous_version": previous_version,
                "attempt": attempt + 1,
            })
            print("Version activated. Running health checks...")

            healthy = run_health_checks(
                config, simulate_failure=simulate_failure
            )

            if not healthy:
                raise RuntimeError("Deployment health check failed")

            log_event({
                "status": "healthy",
                "version": version_name,
                "attempt": attempt + 1,
            })
            print("Deployment activated and health checks passed.")
            return True

        except Exception as error:
            rollback_ok = False

            # Roll back only if this attempt changed the active version.
            try:
                active_file = ROOT / "versions" / "active_version.json"
                current_version = None

                if active_file.exists():
                    with active_file.open("r", encoding="utf-8") as file:
                        current_version = json.load(file).get("version")

                if current_version == version_name:
                    if previous_version:
                        rollback()
                        rollback_ok = True
                    else:
                        log_event({
                            "status": "rollback_unavailable",
                            "version": version_name,
                            "reason": "No previous version exists",
                        })
                elif current_version != version_name:
                    # Activation may have failed before changing the version.
                    rollback_ok = True

            except Exception as rollback_error:
                log_event({
                    "status": "rollback_failed",
                    "version": version_name,
                    "reason": str(rollback_error),
                })
                print("Rollback failed:", rollback_error)

            log_event({
                "status": "failed",
                "version": version_name,
                "attempt": attempt + 1,
                "reason": str(error),
                "rollback_completed": rollback_ok,
            })
            print(f"Attempt {attempt + 1} failed: {error}")

            if rollback_ok and previous_version:
                print("Previous version restored.")
            elif not rollback_ok:
                print("WARNING: Previous version restoration is not confirmed.")

            # A failed health check should not repeatedly redeploy the same
            # unhealthy version. Stop after rolling it back.
            if "health check failed" in str(error).lower():
                print("Stopped after health-check failure.")
                return False

            if attempt < len(retry_delays):
                delay = retry_delays[attempt]
                print(f"Retrying after {delay} minutes.")
                time.sleep(delay * 60)

    print("All deployment attempts failed.")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--simulate-failure", action="store_true")
    args = parser.parse_args()

    deploy(args.version, args.simulate_failure)

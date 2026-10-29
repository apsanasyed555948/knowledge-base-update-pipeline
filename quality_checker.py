
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSIONS_DIR = ROOT / "versions"
CONFIG_PATH = ROOT / "config" / "config.json"
TESTS_DIR = ROOT / "tests"


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def score_answers(questions, knowledge_base):
    """Evaluate expected answers against candidate knowledge content."""
    if not questions:
        raise ValueError("Evaluation dataset is empty")

    all_content = "\n".join(
        item.get("processed_text", "")
        for item in knowledge_base.values()
    ).lower()

    correct = 0
    grounded = 0
    total = len(questions)

    for question in questions:
        expected = question["expected_answer"].strip().lower()
        terms = [
            term.strip().lower()
            for term in question.get("grounding_terms", [])
            if term.strip()
        ]

        # The expected answer must be supported by the candidate KB.
        if expected and expected in all_content:
            correct += 1

        # Every required grounding term must be present in the KB.
        if terms and all(term in all_content for term in terms):
            grounded += 1

    return {
        "accuracy": correct / total,
        "grounding": grounded / total,
        "total_questions": total,
    }


def evaluate_version(version_name):
    config = load_json(CONFIG_PATH)
    tests_path = TESTS_DIR / "evaluation.json"

    if not tests_path.exists():
        raise FileNotFoundError(
            "Create tests/evaluation.json before running quality checks"
        )

    questions = load_json(tests_path)

    current_active = load_json(VERSIONS_DIR / "active_version.json")
    current_name = current_active["version"]

    candidate_dir = VERSIONS_DIR / version_name
    candidate_snapshot = candidate_dir / "knowledge_base.json"

    if not candidate_snapshot.is_file():
        raise ValueError(f"Unknown or incomplete version: {version_name}")

    current_kb = load_json(
        VERSIONS_DIR / current_name / "knowledge_base.json"
    )
    candidate_kb = load_json(candidate_snapshot)

    baseline = score_answers(questions, current_kb)
    candidate = score_answers(questions, candidate_kb)

    print("Baseline:", json.dumps(baseline))
    print("Candidate:", json.dumps(candidate))

    accuracy_drop = baseline["accuracy"] - candidate["accuracy"]
    grounding_drop = baseline["grounding"] - candidate["grounding"]

    approved = (
        candidate["accuracy"] >= config["minimum_accuracy"]
        and candidate["grounding"] >= config["minimum_grounding"]
        and accuracy_drop <= config["negative_accuracy_tolerance"]
        and grounding_drop <= config["negative_grounding_tolerance"]
    )

    result = {
        "version": version_name,
        "baseline_version": current_name,
        "baseline": baseline,
        "candidate": candidate,
        "approved": approved,
    }

    print(json.dumps(result, indent=2))
    print("QUALITY RESULT:", "APPROVED" if approved else "REJECTED")

    return approved


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    evaluate_version(args.version)

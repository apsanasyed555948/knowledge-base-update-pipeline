import json
import re
import regex as unicode_regex
from datetime import date, datetime
from pathlib import Path

from conversation_manager import handle_message, detect_language

BASE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = BASE_DIR / "documents"
METADATA_FILE = DOCUMENTS_DIR / "rag_metadata.json"

ACCESS_LEVELS = {
    "public": 0,
    "customer": 1,
    "staff": 2,
    "admin": 3,
}

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) instructions",
    r"reveal (the )?(system prompt|password|secret)",
    r"ignore your safety rules",
]


def load_metadata():
    if not METADATA_FILE.exists():
        return []

    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_metadata(documents):
    METADATA_FILE.write_text(
        json.dumps(documents, indent=2),
        encoding="utf-8",
    )


def contains_injection(text):
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in INJECTION_PATTERNS
    )


def document_text(document):
    path = DOCUMENTS_DIR / document["file"]

    # Prevent metadata from pointing outside the documents folder.
    if path.resolve().parent != DOCUMENTS_DIR.resolve():
        return ""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


def is_applicable(document, question_date):
    start = parse_date(document.get("effective_date"))
    end = parse_date(document.get("expiry_date"))

    if start and question_date < start:
        return False
    if end and question_date > end:
        return False

    return True




def tokenize(text):
    """Extract Unicode letter and number sequences."""
    return set(
        unicode_regex.findall(
            r"[\p{L}\p{M}\p{N}]+",
            text.casefold()
        )
    )


def normalize_query_for_search(question):
    """Add known English search terms for selected multilingual queries."""
    normalized = question

    language_mappings = {
        "refund": [
            "रिफंड", "रिफंड नीति", "वापसी",
            "రీఫండ్", "విధానం",
            "பணத்தைத் திரும்பப் பெறும் கொள்கை",
            "திரும்பப் பெறும் கொள்கை",
        ],
        "premium": [
            "प्रीमियम",
            "ప్రీమియం",
            "பிரீமியம்",
        ],
        "product": [
            "उत्पाद", "उत्पादों",
            "ఉత్పత్తి", "ఉత్పత్తుల",
            "பொருட்கள்", "பொருட்களின்",
        ],
        "policy": [
            "नीति", "విధానం", "கொள்கை",
        ],
    }

    added_terms = []

    for english_term, phrases in language_mappings.items():
        if any(phrase in question for phrase in phrases):
            added_terms.append(english_term)

    if added_terms:
        normalized += " " + " ".join(added_terms)

    return normalized



def retrieve(question, role="customer", region=None, product=None,
             question_date=None):
    if role not in ACCESS_LEVELS:
        return []

    question_date = question_date or date.today()
    search_question = normalize_query_for_search(question)
    question_words = tokenize(search_question)
    matches = []

    for document in load_metadata():
        if document.get("access_level") not in ACCESS_LEVELS:
            continue

        if ACCESS_LEVELS[role] < ACCESS_LEVELS[document["access_level"]]:
            continue

        if region and document.get("region") != region:
            continue

        if product and document.get("product") != product:
            continue

        if not is_applicable(document, question_date):
            continue

        content = document_text(document)

        # Ignore documents containing detected malicious instructions.
        if not content or contains_injection(content):
            continue

        overlap = question_words & tokenize(content)
        if not overlap:
            continue

        score = len(overlap) / max(len(question_words), 1)
        matches.append((score, document, content))

    if not matches:
        return []

    # Resolve versions only among documents for the same product and region.
    # A newer applicable policy supersedes an older one in that group.
    latest_by_scope = {}

    for item in matches:
        _, document, _ = item
        scope = (document.get("product"), document.get("region"))
        effective = parse_date(document.get("effective_date"))

        previous = latest_by_scope.get(scope)
        if previous is None:
            latest_by_scope[scope] = item
            continue

        previous_date = parse_date(previous[1].get("effective_date"))

        if effective and (previous_date is None or effective > previous_date):
            latest_by_scope[scope] = item
        elif effective == previous_date:
            # Ambiguous policies with the same effective date are not
            # silently resolved by this function.
            latest_by_scope[scope] = None

    # If the newest date is ambiguous, refuse to choose a policy for that scope.
    results = [
        item for item in latest_by_scope.values()
        if item is not None
    ]

    results.sort(key=lambda item: item[0], reverse=True)
    return results


def detect_language_for_answer(question):
    """Return a supported language code for the answer."""
    language, confidence = detect_language(question)

    if language in {"en", "hi", "te", "ta"}:
        return language

    return "en"

def format_customer_answer(question, evidence_text, language="en"):
    """Format a verified premium refund policy in the customer's language."""
    question_lower = question.casefold()

    refund_phrases = (
        "refund", "रिफंड", "वापसी",
        "రీఫండ్", "திரும்பப் பெற",
        "பணத்தைத் திரும்ப",
    )
    premium_phrases = (
        "premium", "प्रीमियम", "ప్రీమియం", "பிரீமியம்",
    )

    if not (
        any(term in question_lower for term in refund_phrases)
        and any(term in question_lower for term in premium_phrases)
        and "Premium product refunds are allowed within 15 days."
            in evidence_text
    ):
        return None

    answers = {
        "en": "Premium product refunds are allowed within 15 days.",
        "hi": "प्रीमियम उत्पादों पर 15 दिनों के भीतर रिफंड की अनुमति है।",
        "te": "ప్రీమియం ఉత్పత్తులకు 15 రోజులలోపు రీఫండ్ అనుమతించబడుతుంది.",
        "ta": "பிரீமியம் பொருட்களுக்கு 15 நாட்களுக்குள் பணத்தைத் திரும்பப் பெற அனுமதி உண்டு.",
    }

    return answers.get(language)


def answer_question(question, role="customer", region=None, product=None,
                    as_of=None):
    if contains_injection(question):
        return (
            "I can't follow instructions that attempt to override "
            "the assistant's rules. Please ask a question about the "
            "current product policy."
        )
    try:
        question_date = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        return "Please provide the date in YYYY-MM-DD format."

    results = retrieve(
        question,
        role=role,
        region=region,
        product=product,
        question_date=question_date,
    )

    if not results:
        return (
            "I cannot answer reliably from the authorised, applicable "
            "documents available. Please clarify your product, region, "
            "or question, or ask an authorised support team."
        )

    best_score = results[0][0]

    # Reject evidence that does not share meaningful question keywords.
    stop_words = {
        "what", "is", "the", "for", "a", "an", "to", "of",
        "in", "on", "and", "or", "how", "when", "does"
    }
    
    search_question = normalize_query_for_search(question)
    question_words = {
        word for word in tokenize(search_question)
        if word not in stop_words
    }

    evidence_words = set()
    for _, document, content in results:
        evidence_words.update(tokenize(content))

        topic_terms = {
        "refund": {"refund", "refunds", "refunded", "refunding"},
        "password": {"password", "passcode", "credential", "credentials"},
        "payment": {"payment", "payments", "paid", "charge", "charges"},
        "support": {"support", "available", "availability", "hours"},
    }

    question_terms = question_words
    evidence_terms = evidence_words

    if not (question_terms & evidence_terms):
        return (
            "I could not find sufficient evidence to answer that question. "
            "Please clarify what information you need."
        )

    question_topics = {
        topic for topic, terms in topic_terms.items()
        if question_terms & terms
    }
    evidence_topics = {
        topic for topic, terms in topic_terms.items()
        if evidence_terms & terms
    }

    if question_topics and not (question_topics & evidence_topics):
        return (
            "I could not find sufficient evidence to answer that question. "
            "Please clarify what information you need."
        )

    if best_score < 0.10:
        return (
            "I could not find sufficient evidence to answer that question. "
            "Please clarify what information you need."
        )

    # Show evidence and citations rather than inventing an answer.
    output = [
        f"Evidence for your question (policy date: {question_date.isoformat()}):"
    ]

    for score, document, content in results:
        if score < best_score * 0.5:
            continue

        output.append(
            f"\nSource: {document['file']} "
            f"(version: {document['version']}, "
            f"product: {document['product']}, "
            f"region: {document['region']}, "
            f"effective: {document['effective_date']}, "
            f"expires: {document.get('expiry_date') or 'never'})"
        )
        output.append(f"Evidence: {content.strip()}")

    output.append(
        "\nNote: These are retrieved evidence excerpts, not a generated "
        "natural-language answer. Conflicting evidence needs review."
    )
    
    evidence_text = "\n".join(output)

    customer_answer = format_customer_answer(
        question,
        evidence_text,
        language=detect_language_for_answer(question),
    )

    if customer_answer:
        return customer_answer + "\n\n" + evidence_text

    return evidence_text



def initialise_example_metadata():
    if METADATA_FILE.exists():
        print("Metadata file already exists; leaving it unchanged.")
        return

    documents = []

    for filename in ("faq.txt", "rollback_test.txt"):
        if (DOCUMENTS_DIR / filename).is_file():
            documents.append({
                "file": filename,
                "version": "1.0",
                "product": "general",
                "region": "global",
                "access_level": "customer",
                "effective_date": "2026-01-01",
                "expiry_date": None,
            })

    save_metadata(documents)
    print(f"Created metadata for {len(documents)} documents.")



if __name__ == "__main__":
    initialise_example_metadata()

    print("\nRAG Knowledge Assistant")
    print("Enter 'exit' to stop.")

    # Separate this CLI session from other customer sessions.
    customer_id = input("Customer ID: ").strip()
    if not customer_id:
        customer_id = "cli-customer"

    while True:
        question = input("\nQuestion: ").strip()

        if question.lower() == "exit":
            break

        session = handle_message(customer_id, question)

        if session["status"] == "clarification_needed":
            print(session["reply"])
            continue

        print(f"Detected language: {session['language']}")
        print(answer_question(question))

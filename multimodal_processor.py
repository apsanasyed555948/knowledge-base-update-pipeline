
import re
from pathlib import Path
from pypdf import PdfReader

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

def mask_sensitive_data(text):
    """Mask common personal and payment information before logging."""
    text = re.sub(
        r"\b(?:\d[ -]*?){13,19}\b",
        "[CARD REDACTED]", text
    )
    text = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[EMAIL REDACTED]", text
    )
    text = re.sub(
        r"\b(?:\+?\d[\d\s()-]{7,}\d)\b",
        "[PHONE REDACTED]", text
    )
    return text

def validate_file(file_path):
    """Check basic file safety before processing."""
    path = Path(file_path)

    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, "Unsupported file type."

    if not path.is_file():
        return False, "File does not exist."

    if path.stat().st_size > MAX_FILE_SIZE:
        return False, "File exceeds the 10 MB limit."

    if path.stat().st_size == 0:
        return False, "File is empty."

    return True, "File passed basic checks."

def extract_pdf_text(file_path):
    """Extract selectable text from a PDF."""
    valid, reason = validate_file(file_path)
    if not valid:
        raise ValueError(reason)

    if Path(file_path).suffix.lower() != ".pdf":
        raise ValueError("This function accepts PDF files only.")

    reader = PdfReader(file_path, strict=True)

    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported.")

    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")

    text = "\n".join(pages).strip()
    if not text:
        raise ValueError(
            "No selectable text found. The PDF may need OCR."
        )

    return text

def extract_evidence(text):
    """Extract common order IDs, dates, amounts, and error codes."""
    return {
                "order_ids": re.findall(
            r"\border\s*(?:id|no\.?)?\s*(?:is|:|#|-)?\s*([A-Z0-9-]{4,})",
            text,
            re.IGNORECASE
        ),
        "dates": re.findall(
            r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
            text
        ),
        "amounts": re.findall(
    r"(?:\u20B9|Rs\.?\s*|INR\s*)\s*\d[\d,]*(?:\.\d{1,2})?",
    text,
    re.IGNORECASE
),
        "error_codes": re.findall(
            r"\b(?:error|err)\s*[-:#]?\s*[A-Z0-9_-]+\b",
            text, re.IGNORECASE
        ),
    }

def compare_message_with_evidence(message, evidence):
    """Flag only explicit conflicts; missing values need clarification."""
    findings = []

    message_evidence = extract_evidence(message)

    for field in ("order_ids", "dates", "amounts", "error_codes"):
        mentioned = set(message_evidence[field])
        attached = set(evidence[field])

        if mentioned and attached and mentioned.isdisjoint(attached):
            findings.append({
                "field": field,
                "status": "conflict",
                "message_values": sorted(mentioned),
                "file_values": sorted(attached),
            })
        elif not attached:
            findings.append({
                "field": field,
                "status": "missing_from_file",
            })

    return findings

import time

RETENTION_SECONDS = 24 * 60 * 60  # 24 hours

def cleanup_expired_files(upload_dir, retention_seconds=RETENTION_SECONDS):
    """Delete expired files from the dedicated upload folder."""
    folder = Path(upload_dir).resolve()

    if not folder.exists() or not folder.is_dir():
        return []

    now = time.time()
    deleted = []

    for path in folder.iterdir():
        try:
            resolved = path.resolve()

            # Never traverse outside the configured upload folder.
            if resolved.parent != folder or not path.is_file():
                continue

            if now - path.stat().st_mtime >= retention_seconds:
                path.unlink()
                deleted.append(path.name)
        except OSError:
            # Keep processing other files if one cannot be removed.
            continue

    return deleted

import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

BACKGROUND_QUEUE = queue.Queue()
BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=2)
PROCESSING_TIMEOUT_SECONDS = 30

def process_with_timeout(
    process_function,
    *args,
    timeout_seconds=PROCESSING_TIMEOUT_SECONDS,
    **kwargs
):
    """Run processing in the background if it exceeds 30 seconds."""
    request_id = str(uuid.uuid4())

    future = BACKGROUND_EXECUTOR.submit(
        process_function, *args, **kwargs
    )

    try:
        result = future.result(timeout=timeout_seconds)
        return {
            "request_id": request_id,
            "status": "completed",
            "result": result,
            "notification": "Your file has been processed."
        }
    except TimeoutError:
        BACKGROUND_QUEUE.put({
            "request_id": request_id,
            "future": future
        })
        return {
            "request_id": request_id,
            "status": "queued",
            "result": None,
            "notification": (
                f"Processing exceeded {timeout_seconds} seconds. "
                "Your request is continuing in the background."
            )
        }

def get_background_status(request_id):
    """Check whether a background request has finished."""
    for item in list(BACKGROUND_QUEUE.queue):
        if item["request_id"] == request_id:
            future = item["future"]

            if not future.done():
                return {"status": "processing"}

            try:
                return {
                    "status": "completed",
                    "result": future.result()
                }
            except Exception:
                return {"status": "failed", "result": None}

    return {"status": "not_found"}

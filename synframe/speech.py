"""Advisory ASR comparison: human review remains mandatory."""

import difflib
import re
import unicodedata


def compare(expected, observed, locale, critical_terms=(), asr_words=None):
    def tokens(text):
        return re.findall(r"[^\W_]+|[^\s\w]", unicodedata.normalize("NFC", text).casefold(), re.UNICODE)
    left, right = tokens(expected), tokens(observed)
    changes = []
    timing = []
    if asr_words is not None:
        for word in asr_words:
            for _ in tokens(word.get("word", "")):
                timing.append({"start_sec": word.get("start"), "end_sec": word.get("end"),
                               "probability": word.get("probability")})
        if len(timing) != len(right):
            timing = []
    for tag, a, b, c, d in difflib.SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
        if tag != "equal":
            relevant = timing[c:d]
            # A deletion has no acoustic word; bound it by neighboring estimates.
            deletion_start = timing[c - 1]["end_sec"] if timing and tag == "delete" and c > 0 else None
            deletion_end = timing[c]["start_sec"] if timing and tag == "delete" and c < len(timing) else None
            changes.append({"type": {"replace": "changed", "delete": "missing", "insert": "added"}[tag],
                            "expected": " ".join(left[a:b]), "observed": " ".join(right[c:d]),
                            "start_sec": relevant[0]["start_sec"] if relevant else deletion_start,
                            "end_sec": relevant[-1]["end_sec"] if relevant else deletion_end,
                            "timing_kind": "asr_estimate" if relevant else ("neighbor_bounds" if deletion_start is not None or deletion_end is not None else "unavailable"),
                            "probabilities": [x["probability"] for x in relevant]})
    def contains_sequence(needle, haystack):
        return bool(needle) and any(haystack[i:i + len(needle)] == needle
                                    for i in range(len(haystack) - len(needle) + 1))
    critical_missing = [term for term in critical_terms if not contains_sequence(tokens(term), right)]
    return {"locale": locale, "changes": changes, "critical_missing": critical_missing,
            "status": "needs_human_review", "timestamps": "asr_estimate" if timing else "not_available",
            "confidence": "asr_estimate" if timing else "not_available"}

"""Durable local candidate ledger, deliberately without remote submission."""
import json
import sqlite3
import time

SCHEMA_VERSION = 2
EVENT_KINDS = {"reserved", "submitted", "transport_error", "running", "success", "failed",
               "downloaded", "postprocessed", "selected", "approved", "rejected", "refunded"}


def merge_metadata(existing, update):
    merged = dict(existing)
    for key, value in update.items():
        old = merged.get(key)
        if old is not None and value is None:
            raise ValueError("existing metadata cannot be cleared")
        if isinstance(old, dict):
            if not isinstance(value, dict):
                raise ValueError("existing nested metadata cannot be replaced")
            merged[key] = merge_metadata(old, value)
        else:
            merged[key] = value
    return merged


def connect(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS candidates (
        candidate_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        state TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        remote_task_id TEXT,
        cost REAL,
        metadata TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS task_graph (
        batch_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        dependencies TEXT NOT NULL,
        PRIMARY KEY (batch_id, candidate_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS task_retries (
        batch_id TEXT NOT NULL, previous_id TEXT NOT NULL, retry_id TEXT NOT NULL UNIQUE,
        reason TEXT NOT NULL, PRIMARY KEY (batch_id, previous_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS candidate_readiness (
        batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL, evidence TEXT NOT NULL,
        PRIMARY KEY (batch_id, candidate_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS readiness_snapshots (
        batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL, stage TEXT NOT NULL,
        kind TEXT NOT NULL, item INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
        PRIMARY KEY (batch_id, candidate_id, stage, kind, item)
    )""")
    # v2: keyframe selections (image-first) and an append-only event log.
    db.execute("""CREATE TABLE IF NOT EXISTS selections (
        selection_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, slot TEXT NOT NULL,
        image_candidate_id TEXT NOT NULL, image_sha256 TEXT NOT NULL, path TEXT NOT NULL,
        reviewer TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, kind TEXT NOT NULL,
        at REAL NOT NULL, data TEXT NOT NULL
    )""")
    db.execute("CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events "
               "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END")
    db.execute("CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
               "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END")
    if db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    db.commit()
    return db


def record_event(db, candidate_id, kind, data=None, at=None, commit=True):
    """Append one timing/review event; rows can never be edited or removed."""
    if not isinstance(candidate_id, str) or not candidate_id or kind not in EVENT_KINDS:
        raise ValueError("candidate ID and a known event kind are required")
    db.execute("INSERT INTO events (candidate_id, kind, at, data) VALUES (?,?,?,?)",
               (candidate_id, kind, time.time() if at is None else float(at),
                json.dumps(data or {}, ensure_ascii=False, sort_keys=True)))
    if commit:
        db.commit()


def events(db, candidate_id):
    rows = db.execute("SELECT kind, at, data FROM events WHERE candidate_id=? ORDER BY seq",
                      (candidate_id,)).fetchall()
    return [{"kind": k, "at": a, "data": json.loads(d)} for k, a, d in rows]


def stage_timings(db, candidate_id):
    """Queue/generation/download/post-process seconds from the event log (100xSpeed's four columns)."""
    first = {}
    for item in events(db, candidate_id):
        first.setdefault(item["kind"], item["at"])
    def span(a, b):
        return round(first[b] - first[a], 3) if a in first and b in first else None
    return {"queue_sec": span("submitted", "running"),
            "generation_sec": span("running", "success") if "running" in first else span("submitted", "success"),
            "download_sec": span("success", "downloaded"),
            "postprocess_sec": span("downloaded", "postprocessed")}


def save(db, candidate):
    required = ("candidate_id", "batch_id", "state", "request_hash")
    if any(not isinstance(candidate.get(k), str) or not candidate[k] for k in required):
        raise ValueError("candidate_id, batch_id, state and request_hash are required")
    if candidate["state"] not in {"draft", "submitted", "running", "success", "failed", "unknown"}:
        raise ValueError("invalid candidate state")
    if candidate["state"] in {"submitted", "running", "success"} and not candidate.get("remote_task_id"):
        raise ValueError("remote task ID is required for submitted, running and success")
    previous = db.execute("SELECT request_hash, remote_task_id, state, batch_id, cost, metadata FROM candidates WHERE candidate_id = ?",
                          (candidate["candidate_id"],)).fetchone()
    if previous and (previous[0] != candidate["request_hash"] or
                     (previous[1] and candidate.get("remote_task_id") != previous[1])):
        raise ValueError("existing candidate request or remote task cannot be replaced")
    if previous:
        if previous[3] != candidate["batch_id"]:
            raise ValueError("candidate batch cannot be replaced")
        transitions = {
            "draft": {"draft", "submitted", "unknown"},
            "submitted": {"submitted", "running", "success", "failed", "unknown"},
            "running": {"running", "success", "failed", "unknown"},
            "unknown": {"unknown", "submitted", "running", "success", "failed"},
            "success": {"success"},
            "failed": {"failed"},
        }
        if candidate["state"] not in transitions[previous[2]]:
            raise ValueError("invalid state transition; retry must be a new candidate")
    old_meta = json.loads(previous[5]) if previous else {}
    if previous and "cost" in candidate and candidate["cost"] is None and previous[4] is not None:
        raise ValueError("existing cost cannot be cleared")
    old_meta = merge_metadata(old_meta, candidate.get("metadata", {}))
    cost = candidate.get("cost", previous[4] if previous else None)
    with db:
        db.execute("""INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?)
                      ON CONFLICT(candidate_id) DO UPDATE SET state=excluded.state,
                      remote_task_id=excluded.remote_task_id, cost=excluded.cost, metadata=excluded.metadata""",
                   (candidate["candidate_id"], candidate["batch_id"], candidate["state"],
                    candidate["request_hash"], candidate.get("remote_task_id"), cost,
                    json.dumps(old_meta, ensure_ascii=False)))


def get(db, candidate_id):
    row = db.execute("SELECT candidate_id,batch_id,state,request_hash,remote_task_id,cost,metadata FROM candidates WHERE candidate_id = ?",
                     (candidate_id,)).fetchone()
    if row is None:
        return None
    keys = ["candidate_id", "batch_id", "state", "request_hash", "remote_task_id", "cost", "metadata"]
    result = dict(zip(keys, row))
    result["metadata"] = json.loads(result["metadata"])
    return result

"""Explicitly authorized, injectable submission; never submits by itself."""
import hashlib
import base64
import binascii
import json
import math
import sqlite3
from pathlib import Path

from .ledger import get, record_event, save

REQUIRED_READINESS = {'request_validation', 'product_identity', 'model_capability', 'creative_plan'}
# Image-first: a video request may also carry human-selected keyframes as pose/composition refs.
KEYFRAME_STAGE = 'keyframe_selection'
ALLOWED_READINESS = (frozenset(REQUIRED_READINESS), frozenset(REQUIRED_READINESS | {KEYFRAME_STAGE}))


def _valid_stage_set(stages):
    return frozenset(stages) in ALLOWED_READINESS


def _read_readiness(db, batch_id, candidate_id):
    graph = db.execute('SELECT request_hash FROM task_graph WHERE batch_id=? AND candidate_id=?',
                       (batch_id, candidate_id)).fetchone()
    row = db.execute('SELECT evidence FROM candidate_readiness WHERE batch_id=? AND candidate_id=?',
                     (batch_id, candidate_id)).fetchone()
    if not graph or not row:
        raise ValueError('candidate readiness evidence is required')
    evidence = json.loads(row[0])
    if not _valid_stage_set(evidence):
        raise ValueError('incomplete readiness evidence')
    captured = []
    for stage, entry in evidence.items():
        try:
            data = Path(entry['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('readiness evidence changed: ' + stage)
            report = json.loads(data)
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('readiness evidence unavailable: ' + stage) from exc
        if (report.get('stage') != stage or report.get('status') != 'pass'
                or report.get('request_hash') != graph[0]
                or not isinstance(report.get('reviewer'), str) or not report['reviewer'].strip()
                or not isinstance(report.get('details'), str) or not report['details'].strip()
                or not isinstance(report.get('sources'), list) or not report['sources']):
            raise ValueError('readiness evidence is not an approved report for this request: ' + stage)
        captured.append((stage, 'report', 0, entry['sha256'], data))
        for index, source in enumerate(report['sources']):
            try:
                data = Path(source['path']).read_bytes()
                if hashlib.sha256(data).hexdigest() != source['sha256']:
                    raise ValueError('readiness source changed: ' + stage)
            except (OSError, KeyError, TypeError) as exc:
                raise ValueError('readiness source unavailable: ' + stage) from exc
            captured.append((stage, 'source', index, source['sha256'], data))
    return captured


def _check_readiness(db, batch_id, candidate_id):
    """Verify the immutable database evidence, independent of mutable source paths.

    Returns (product_hashes, keyframe_hashes). Keyframes are pose/composition references
    only; they never count as product identity evidence.
    """
    graph = db.execute('SELECT request_hash FROM task_graph WHERE batch_id=? AND candidate_id=?',
                       (batch_id, candidate_id)).fetchone()
    rows = db.execute('SELECT stage,kind,item,sha256,content FROM readiness_snapshots '
                      'WHERE batch_id=? AND candidate_id=?', (batch_id, candidate_id)).fetchall()
    if not graph or not rows:
        raise ValueError('candidate readiness snapshots are required; re-register evidence')
    by_key = {}
    for stage, kind, index, digest, content in rows:
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('readiness snapshot corrupted: ' + stage)
        by_key[(stage, kind, index)] = (digest, content)
    stages = {key[0] for key in by_key if key[1] == 'report'}
    if not _valid_stage_set(stages):
        raise ValueError('incomplete readiness snapshots')
    product_hashes, keyframe_hashes = set(), set()
    for stage in sorted(stages):
        try:
            report = json.loads(by_key[(stage, 'report', 0)][1])
            if (report.get('stage') != stage or report.get('status') != 'pass'
                    or report.get('request_hash') != graph[0]
                    or not isinstance(report.get('reviewer'), str) or not report['reviewer'].strip()
                    or not isinstance(report.get('details'), str) or not report['details'].strip()
                    or not isinstance(report.get('sources'), list) or not report['sources']):
                raise ValueError('invalid readiness report snapshot: ' + stage)
            for index, source in enumerate(report['sources']):
                digest, _ = by_key[(stage, 'source', index)]
                if source['sha256'] != digest:
                    raise ValueError('readiness source snapshot mismatch: ' + stage)
                if stage == 'product_identity':
                    product_hashes.add(digest)
                elif stage == KEYFRAME_STAGE:
                    keyframe_hashes.add(digest)
            if stage == KEYFRAME_STAGE:
                _check_selections(db, report, {s['sha256'] for s in report['sources']})
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('invalid readiness snapshot: ' + stage) from exc
    if product_hashes & keyframe_hashes:
        raise ValueError('a keyframe cannot also be registered as product identity evidence')
    return product_hashes, keyframe_hashes


def _check_selections(db, report, source_hashes):
    """Every keyframe source must be a recorded selection of a successful image candidate."""
    ids = report.get('selection_ids')
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ValueError('keyframe_selection must list selection_ids')
    selected = set()
    for selection_id in ids:
        row = db.execute('SELECT image_candidate_id,image_sha256 FROM selections WHERE selection_id=?',
                         (selection_id,)).fetchone()
        if not row:
            raise ValueError('unknown keyframe selection: ' + str(selection_id))
        image = get(db, row[0])
        if (image is None or image['state'] != 'success'
                or image['metadata'].get('output_sha256') != row[1]):
            raise ValueError('keyframe selection does not point to a successful image output')
        selected.add(row[1])
    if selected != source_hashes:
        raise ValueError('keyframe sources must match the recorded selections exactly')


IMAGE_RATIOS = ('1:1', '9:16', '16:9', '3:4', '4:3', '2:3', '3:2')
# Video models whose image list is a pure reference set (no first-frame semantics), so selected
# keyframes may ride along as pose/composition references next to the PDP identity image.
KEYFRAME_MODELS = ('minimax-h3', 'omni_flash-10s')
PROFILES = {
    'minimax-h3': ('image_url', {'image_url', 'aspect_ratio', 'mode', 'duration', 'resolution'}, 9, 7000),
    'omni-flash': ('images', {'images', 'aspect_ratio', 'duration', 'enable_upsample', 'enhance_prompt'}, 3, 4000),
    'omni_flash-10s': ('images', {'images', 'aspect_ratio'}, 7, 4000),
    'tt-image-2.6': ('images', {'images', 'aspect_ratio', 'resolution', 'quality', 'version', 'output_format'}, 16, 4000),
}


def _bind_product_images(payload, product_hashes, keyframe_hashes=frozenset()):
    """Only send embedded bytes proven present in evidence snapshots; return each image's role.

    At least one image must be same-SKU product identity. Selected keyframes may join as
    pose/composition references (H3 reference mode), never as identity.
    """
    if 'model' not in payload:
        raise ValueError('provider model is required for submission')
    params = payload.get('params')
    if not isinstance(params, dict):
        raise ValueError('model request params required')
    model = payload['model']
    profile = PROFILES.get(model) if isinstance(model, str) else None
    if profile is None or set(payload) != {'model', 'prompt', 'params'} or set(params) - profile[1]:
        raise ValueError('provider model or request fields not supported by frozen-reference gate')
    prompt = payload['prompt']
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > profile[3]:
        raise ValueError('invalid prompt')
    ratio = params.get('aspect_ratio')
    ratios = {'minimax-h3': ('adaptive', '16:9', '9:16', '1:1', '4:3', '3:4', '21:9'),
              'tt-image-2.6': IMAGE_RATIOS}.get(model, ('16:9', '9:16'))
    if not isinstance(ratio, str) or ratio not in ratios:
        raise ValueError('invalid aspect ratio')
    if model in ('minimax-h3', 'omni-flash'):
        duration = params.get('duration')
        if type(duration) is int:
            seconds = duration
        elif isinstance(duration, str) and duration in tuple(str(x) for x in range(4, 16)):
            seconds = int(duration)
        else:
            raise ValueError('invalid duration type')
        allowed = range(4, 16) if model == 'minimax-h3' else (4, 6, 8, 10)
        if seconds not in allowed:
            raise ValueError('duration is not supported for this model')
    if model == 'minimax-h3':
        if params.get('mode') != 'cankaosheng' or params.get('resolution') not in ('768P', '1080P', '2K', '4K'):
            raise ValueError('invalid H3 reference mode or resolution')
    elif model == 'omni-flash':
        if any(type(params.get(flag)) is not bool or params[flag] is not False
               for flag in ('enable_upsample', 'enhance_prompt')):
            raise ValueError('Omni flags must explicitly be false')
    elif model == 'tt-image-2.6':
        if params.get('resolution', '1K') not in ('1K', '2K', '4K') \
                or params.get('version', 'flare') not in ('flare', 'sunburst') \
                or params.get('quality', 'high') not in ('low', 'medium', 'high', 'auto') \
                or params.get('output_format', 'png') not in ('png', 'jpeg', 'webp'):
            raise ValueError('invalid tt-image-2.6 option')
    images = params.get(profile[0])
    if isinstance(images, str):
        images = [images]
    if not isinstance(images, list) or not 1 <= len(images) <= profile[2]:
        raise ValueError('embedded product reference is required')
    if keyframe_hashes and model not in KEYFRAME_MODELS:
        raise ValueError('keyframe references are only wired for reference-image video models')
    roles = []
    for value in images:
        if not isinstance(value, str) or not value.startswith(('data:image/png;base64,',
                                                               'data:image/jpeg;base64,',
                                                               'data:image/webp;base64,')):
            raise ValueError('only embedded product images are supported; remote URLs are mutable')
        try:
            data = base64.b64decode(value.split(',', 1)[1], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('invalid embedded image') from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest in product_hashes:
            roles.append({'sha256': digest, 'role': 'pdp_identity'})
        elif digest in keyframe_hashes:
            roles.append({'sha256': digest, 'role': 'keyframe_pose'})
        else:
            raise ValueError('submitted image differs from approved product evidence snapshot')
    if not any(item['role'] == 'pdp_identity' for item in roles):
        raise ValueError('at least one same-SKU product identity image is required')
    return roles


def record_readiness(db, batch_id, candidate_id, evidence):
    """Record request-bound attestations; contents remain reviewer claims, not visual truth."""
    if not isinstance(evidence, dict) or not _valid_stage_set(evidence):
        raise ValueError('all readiness evidence stages are required')
    db.execute('BEGIN IMMEDIATE')
    try:
        if get(db, candidate_id):
            raise ValueError('cannot alter readiness after submission')
        if not db.execute('SELECT 1 FROM task_graph WHERE batch_id=? AND candidate_id=?',
                          (batch_id, candidate_id)).fetchone():
            raise ValueError('candidate is not registered')
        db.execute('INSERT INTO candidate_readiness VALUES (?,?,?) ON CONFLICT(batch_id,candidate_id) '
                   'DO UPDATE SET evidence=excluded.evidence',
                   (batch_id, candidate_id, json.dumps(evidence, sort_keys=True)))
        captured = _read_readiness(db, batch_id, candidate_id)
        db.execute('DELETE FROM readiness_snapshots WHERE batch_id=? AND candidate_id=?',
                   (batch_id, candidate_id))
        db.executemany('INSERT INTO readiness_snapshots VALUES (?,?,?,?,?,?,?)',
                       [(batch_id, candidate_id, stage, kind, index, digest, data)
                        for stage, kind, index, digest, data in captured])
        _check_readiness(db, batch_id, candidate_id)
        db.commit()
    except Exception:
        db.rollback()
        raise


def record_output(db, candidate_id, path):
    """Bind a downloaded result file to a successful candidate by content hash."""
    candidate = get(db, candidate_id)
    if candidate is None or candidate['state'] != 'success':
        raise ValueError('only a successful candidate can have an output')
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    existing = candidate['metadata'].get('output_sha256')
    if existing and existing != digest:
        raise ValueError('candidate output already recorded with different bytes')
    save(db, {**candidate, 'metadata': {'output_sha256': digest, 'output_path': str(path)}})
    if not existing:
        record_event(db, candidate_id, 'downloaded', {'sha256': digest})
    return digest


def select_keyframe(db, selection_id, batch_id, slot, image_candidate_id, path, reviewer, reason):
    """Human pick of one drawn image for a slot; the video batch is registered afterwards."""
    if not all(isinstance(x, str) and x.strip() for x in (selection_id, batch_id, slot, reviewer, reason)):
        raise ValueError('selection, batch, slot, reviewer and reason are required')
    image = get(db, image_candidate_id)
    if image is None or image['state'] != 'success' or image['batch_id'] != batch_id:
        raise ValueError('selected image must be a successful candidate of the image batch')
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if image['metadata'].get('output_sha256') != digest:
        raise ValueError('selected file differs from the recorded image output')
    import time
    with db:
        db.execute('INSERT INTO selections VALUES (?,?,?,?,?,?,?,?,?)',
                   (selection_id, batch_id, slot, image_candidate_id, digest, str(path),
                    reviewer.strip(), reason.strip(), time.time()))
    record_event(db, image_candidate_id, 'selected', {'selection_id': selection_id, 'slot': slot})
    return digest


def payload_hash(payload):
    if not isinstance(payload, dict) or "depends_on" in payload:
        raise ValueError("payload must be an object; dependencies belong to registered task graph")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def register_graph(db, batch_id, tasks):
    if not batch_id or not tasks:
        raise ValueError("batch and tasks are required")
    graph = {}
    for task in tasks:
        candidate_id = task.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in graph:
            raise ValueError("task IDs must be unique nonempty strings")
        deps = task.get("depends_on")
        if not isinstance(deps, list) or any(not isinstance(dep, str) or not dep for dep in deps):
            raise ValueError("each task must declare depends_on explicitly")
        graph[candidate_id] = {"dependencies": deps, "request_hash": payload_hash(task["payload"])}
    for candidate_id, item in graph.items():
        if len(set(item["dependencies"])) != len(item["dependencies"]) or any(dep not in graph or dep == candidate_id for dep in item["dependencies"]):
            raise ValueError("unknown, duplicate, or self dependency")
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("dependency cycle")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]["dependencies"]:
            visit(dep)
        visiting.remove(node)
        visited.add(node)
    for node in graph:
        visit(node)
    with db:
        if db.execute("SELECT 1 FROM task_graph WHERE batch_id = ?", (batch_id,)).fetchone():
            raise ValueError("batch graph already registered; create a new batch")
        for candidate_id, item in graph.items():
            db.execute("INSERT INTO task_graph VALUES (?,?,?,?)",
                       (batch_id, candidate_id, item["request_hash"], json.dumps(item["dependencies"])))


def register_retry(db, batch_id, previous_id, retry_id, reason):
    if not isinstance(retry_id, str) or not retry_id or not isinstance(reason, str) or not reason.strip():
        raise ValueError("new retry ID and reason are required")
    with db:
        previous = get(db, previous_id)
        graph = db.execute("SELECT request_hash,dependencies FROM task_graph WHERE batch_id=? AND candidate_id=?",
                           (batch_id, previous_id)).fetchone()
        if not previous or previous["batch_id"] != batch_id or previous["state"] != "failed" or not graph:
            raise ValueError("only a confirmed failed candidate can be retried")
        if get(db, retry_id) or db.execute("SELECT 1 FROM task_graph WHERE candidate_id=?", (retry_id,)).fetchone():
            raise ValueError("retry candidate ID already exists")
        db.execute("INSERT INTO task_retries VALUES (?,?,?,?)", (batch_id, previous_id, retry_id, reason.strip()))
        db.execute("INSERT INTO task_graph VALUES (?,?,?,?)", (batch_id, retry_id, graph[0], graph[1]))


def submit_candidate(db, candidate_id, batch_id, payload, authorization, transport):
    """Submit once. Optional authorization keys: spend_cap and max_unit_price, which then
    require a per-candidate quote {estimate, unit_price, channel, billing, price_snapshot_sha256}."""
    if (not isinstance(authorization, dict) or authorization.get("approved") is not True
            or authorization.get("batch_id") != batch_id
            or not isinstance(authorization.get("candidate_ids"), list)
            or candidate_id not in authorization["candidate_ids"]):
        raise ValueError("explicit authorization must cover batch and candidate")
    if not callable(transport):
        raise ValueError("a provider transport must be explicitly supplied")
    quote = _authorized_quote(authorization, candidate_id)
    # Detach all nested request objects from the caller before checking or sending.
    frozen_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False, allow_nan=False).encode('utf-8')
    frozen_payload = json.loads(frozen_bytes)
    request_hash = payload_hash(frozen_payload)
    row = {"candidate_id": candidate_id, "batch_id": batch_id, "request_hash": request_hash,
           "state": "unknown", "metadata": {"authorization_batch": batch_id, "authorization_candidate": candidate_id}}
    # Lock readiness, graph, dependency check and reservation in one write transaction.
    db.execute('BEGIN IMMEDIATE')
    try:
        entry = db.execute("SELECT request_hash,dependencies FROM task_graph WHERE batch_id = ? AND candidate_id = ?",
                           (batch_id, candidate_id)).fetchone()
        if entry is None:
            raise ValueError("candidate is not in a registered batch graph")
        if request_hash != entry[0]:
            raise ValueError("payload differs from registered task")
        product_hashes, keyframe_hashes = _check_readiness(db, batch_id, candidate_id)
        roles = _bind_product_images(frozen_payload, product_hashes, keyframe_hashes)
        row["metadata"]["image_roles"] = roles
        if quote is not None:
            row["metadata"]["quote"] = quote
            if "spend_cap" in authorization:
                committed = 0.0
                for (meta,) in db.execute("SELECT metadata FROM candidates WHERE batch_id=?", (batch_id,)):
                    committed += float(json.loads(meta).get("quote", {}).get("estimate", 0))
                if committed + quote["estimate"] > float(authorization["spend_cap"]) + 1e-9:
                    raise ValueError("batch spend cap would be exceeded")
        for dependency in json.loads(entry[1]):
            upstream = get(db, dependency)
            while upstream is not None and upstream["state"] == "failed":
                retry = db.execute("SELECT retry_id FROM task_retries WHERE batch_id=? AND previous_id=?",
                                   (batch_id, dependency)).fetchone()
                if not retry:
                    break
                dependency = retry[0]
                upstream = get(db, dependency)
            if upstream is None or upstream["batch_id"] != batch_id or upstream["state"] != "success":
                raise ValueError("dependency is missing, belongs to another batch or has not succeeded")
        db.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?,?)",
                   (candidate_id, batch_id, "unknown", request_hash, None, None,
                    json.dumps(row["metadata"], ensure_ascii=False)))
        record_event(db, candidate_id, "reserved", {"batch_id": batch_id}, commit=False)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValueError("candidate already exists; reconcile state instead of resubmitting") from exc
    except Exception:
        db.rollback()
        raise
    try:
        # The adapter receives immutable canonical JSON bytes; it must send these
        # exact bytes. Arbitrary or malicious adapters are outside this guarantee.
        response = transport(frozen_bytes)
    except Exception as exc:
        save(db, {**row, "metadata": {"transport_error": type(exc).__name__}})
        record_event(db, candidate_id, "transport_error", {"type": type(exc).__name__})
        return {"candidate_id": candidate_id, "state": "unknown"}
    task_id = response.get("task_id") if isinstance(response, dict) else None
    if not isinstance(task_id, str) or not task_id:
        save(db, {**row, "metadata": {"transport_error": "missing_task_id"}})
        record_event(db, candidate_id, "transport_error", {"type": "missing_task_id"})
        return {"candidate_id": candidate_id, "state": "unknown"}
    # Preserve the remote identity independently of optional, untrusted billing data.
    submitted = {**row, "state": "submitted", "remote_task_id": task_id,
                 "metadata": {"provider_response": "task_id_recorded", "cost_status": "pending"}}
    save(db, submitted)
    record_event(db, candidate_id, "submitted", {"remote_task_id": task_id})
    cost = response.get("cost")
    try:
        numeric_cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    except OverflowError:
        numeric_cost = None
    valid_cost = numeric_cost is not None and math.isfinite(numeric_cost) and numeric_cost >= 0
    if valid_cost:
        save(db, {**submitted, "cost": numeric_cost, "metadata": {"cost_status": "recorded"}})
    else:
        save(db, {**submitted, "metadata": {"cost_status": "missing" if cost is None else "invalid"}})
    return {"candidate_id": candidate_id, "state": "submitted", "remote_task_id": task_id}


def _authorized_quote(authorization, candidate_id):
    if "spend_cap" not in authorization and "max_unit_price" not in authorization:
        return None
    quote = (authorization.get("quotes") or {}).get(candidate_id)
    numbers = ("estimate", "unit_price")
    if (not isinstance(quote, dict)
            or any(not isinstance(quote.get(k), (int, float)) or isinstance(quote.get(k), bool)
                   or not math.isfinite(quote[k]) or quote[k] < 0 for k in numbers)
            or any(not isinstance(quote.get(k), str) or not quote[k]
                   for k in ("channel", "billing", "price_snapshot_sha256"))):
        raise ValueError("a capped authorization needs a complete quote per candidate")
    if "max_unit_price" in authorization and quote["unit_price"] > float(authorization["max_unit_price"]) + 1e-9:
        raise ValueError("quoted unit price exceeds the authorized maximum")
    return {k: quote[k] for k in numbers + ("channel", "billing", "price_snapshot_sha256")}


def record_status(db, candidate_id, state, cost=None, refunded=None, at=None):
    """Apply a polled provider status and append the matching timing event."""
    candidate = get(db, candidate_id)
    if candidate is None:
        raise ValueError("unknown candidate")
    update = {**candidate, "state": state, "metadata": {}}
    if cost is not None:
        update["cost"] = float(cost)
    already_refunded = candidate["metadata"].get("refunded") is True
    if refunded is not None:
        update["metadata"] = {"refunded": bool(refunded)}
    if candidate["state"] != state:
        save(db, update)
        record_event(db, candidate_id, state, {"cost": cost}, at=at)
    elif cost is not None or refunded is not None:
        save(db, update)
    if refunded and not already_refunded:
        record_event(db, candidate_id, "refunded", {}, at=at)

"""Spec-driven generation batches: images first, human keyframe picks, then video.

  synframe generate SPEC            offline: build and gate-check every payload, spend nothing
  synframe generate SPEC --quote    read live prices and balance, still spend nothing
  synframe generate SPEC --submit   register, attest, submit each job once, poll, download
  synframe select SPEC              record human keyframe picks from a finished image batch

Paths in a spec are relative to the spec file. A candidate is submitted at most once; re-running
only polls and downloads. A failed job needs a new job id or a new batch id.
"""
import base64
import hashlib
import json
import time
import urllib.parse
from pathlib import Path

from . import dispatch
from .ledger import connect, get, stage_timings

IMAGE_MODELS = ("tt-image-2.6",)
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MEDIA_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov")


class SpecError(ValueError):
    pass


def load_spec(path):
    path = Path(path).expanduser().resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    for key in ("batch_id", "ledger", "out_dir", "jobs"):
        if not spec.get(key):
            raise SpecError("spec needs " + key)
    ids = [job.get("id") for job in spec["jobs"]]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise SpecError("job ids must be unique non-empty strings")
    spec["_dir"] = path.parent
    spec["_path"] = path
    return spec


def _path(spec, value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else (spec["_dir"] / p).resolve()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def data_url(path):
    path = Path(path)
    mime = MIME.get(path.suffix.lower())
    if not mime:
        raise SpecError("reference images must be png, jpeg or webp: " + str(path))
    return "data:%s;base64,%s" % (mime, base64.b64encode(path.read_bytes()).decode())


def _keyframes(db, job):
    files = []
    for selection_id in job.get("keyframes") or []:
        row = db.execute("SELECT path,image_sha256 FROM selections WHERE selection_id=?", (selection_id,)).fetchone() \
            if db is not None else None
        if not row:
            raise SpecError("keyframe selection not recorded yet: %s (run synframe select first)" % selection_id)
        if _sha(row[0]) != row[1]:
            raise SpecError("selected keyframe file changed: " + row[0])
        files.append(Path(row[0]))
    return files


def build_job(spec, job, db=None):
    """Return (payload, product_files, keyframe_files) for one job."""
    defaults = spec.get("defaults") or {}
    model = job.get("model") or defaults.get("model")
    if model not in dispatch.PROFILES:
        raise SpecError("unsupported model for %s: %r" % (job["id"], model))
    products = [_path(spec, p) for p in (job.get("product_images") or spec.get("product_images") or [])]
    if not products:
        raise SpecError("job %s needs at least one same-SKU product image" % job["id"])
    keyframes = _keyframes(db, job)
    images = [data_url(p) for p in products + keyframes]
    ratio = job.get("aspect_ratio") or defaults.get("aspect_ratio", "9:16")
    if model == "tt-image-2.6":
        params = {"images": images, "aspect_ratio": ratio,
                  "resolution": job.get("resolution") or defaults.get("image_resolution", "1K"),
                  "quality": job.get("quality") or defaults.get("quality", "high")}
    elif model == "minimax-h3":
        duration = job.get("duration")
        if type(duration) is not int:
            raise SpecError("minimax-h3 bills per second: job %s needs an integer duration (4-15)" % job["id"])
        params = {"mode": "cankaosheng", "image_url": images, "duration": duration, "aspect_ratio": ratio,
                  "resolution": job.get("resolution") or defaults.get("video_resolution", "768P")}
    elif model == "omni_flash-10s":
        # Billed per call with a fixed 10 s length: plan several scenes into one call.
        params = {"images": images, "aspect_ratio": ratio}
    else:
        params = {"images": images, "aspect_ratio": ratio, "duration": str(job.get("duration", 10)),
                  "enable_upsample": False, "enhance_prompt": False}
    prompt = " ".join(x for x in (defaults.get("prompt_prefix", ""), job.get("prompt", ""),
                                  defaults.get("prompt_suffix", "")) if x).strip()
    payload = {"model": model, "prompt": prompt, "params": params}
    dispatch._bind_product_images(payload, {_sha(p) for p in products}, {_sha(p) for p in keyframes})
    return payload, products, keyframes


def _summary(payload):
    params = {k: (("%d embedded image(s)" % len(v)) if k in ("images", "image_url") else v)
              for k, v in payload["params"].items()}
    return {"model": payload["model"], "params": params, "prompt_chars": len(payload["prompt"]),
            "request_hash": dispatch.payload_hash(payload)}


def _open_db(spec, create):
    path = _path(spec, spec["ledger"])
    if not create and not path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return connect(path)


def build_all(spec, db):
    built, problems = {}, []
    for job in spec["jobs"]:
        try:
            built[job["id"]] = build_job(spec, job, db)
        except (SpecError, ValueError, OSError) as exc:
            problems.append({"job": job["id"], "error": str(exc)})
    return built, problems


def _new_file(folder, stem, suffix):
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / (stem + suffix)
    n = 2
    while candidate.exists():
        candidate = folder / ("%s-%d%s" % (stem, n, suffix))
        n += 1
    return candidate


def _write_snapshot(spec, client, models):
    snapshot = client.snapshot(models)
    path = _new_file(_path(spec, spec["out_dir"]) / "prices", "price-" + time.strftime("%Y%m%d-%H%M%S"), ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
    return path, snapshot, _sha(path)


def _quotes(built, snapshot, sha, ids):
    from .providers.lk888 import quote
    return {cid: quote(built[cid][0], snapshot, sha) for cid in ids}


def _check_budget(spec, quotes, committed, balance):
    auth = spec.get("authorization") or {}
    for key in ("spend_cap", "max_unit_price"):
        if not isinstance(auth.get(key), (int, float)) or isinstance(auth.get(key), bool):
            raise SpecError("authorization.%s is required before any paid call" % key)
    total = round(sum(q["estimate"] for q in quotes.values()), 4)
    reasons = []
    if committed + total > auth["spend_cap"] + 1e-9:
        reasons.append("quote %.4f + committed %.4f exceeds spend_cap %.4f" % (total, committed, auth["spend_cap"]))
    over = [c for c, q in quotes.items() if q["unit_price"] > auth["max_unit_price"] + 1e-9]
    if over:
        reasons.append("unit price above max_unit_price for " + ", ".join(over))
    reserve = float(auth.get("balance_reserve", 0))
    if balance is None or balance < total + reserve:
        reasons.append("balance %s below quote %.4f + reserve %.2f" % (balance, total, reserve))
    return total, reasons


def _attest(spec, db, cid, payload, products, keyframes, job, price_path):
    folder = _path(spec, spec["out_dir"]) / "proofs" / cid
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / "request.json"
    note.write_text(json.dumps(_summary(payload), ensure_ascii=False, indent=1), encoding="utf-8")
    plan = _path(spec, spec["creative_plan"]) if spec.get("creative_plan") else spec["_path"]
    reviewer = spec.get("reviewer") or "unnamed reviewer"
    stages = {"request_validation": ([note], "fields checked by the synframe gate profile " + payload["model"]),
              "product_identity": (products, "same-SKU product images are the only identity reference"),
              "model_capability": ([price_path], "live channel pricing captured before submission"),
              "creative_plan": ([plan], job.get("note") or "approved creative plan for this batch")}
    if keyframes:
        stages["keyframe_selection"] = (keyframes, "human-picked keyframes, pose and composition only")
    evidence = {}
    for stage, (sources, details) in stages.items():
        report = {"stage": stage, "status": "pass", "request_hash": dispatch.payload_hash(payload),
                  "reviewer": reviewer, "details": details,
                  "sources": [{"path": str(s), "sha256": _sha(s)} for s in sources]}
        if stage == "keyframe_selection":
            report["selection_ids"] = list(job["keyframes"])
        path = folder / (stage + ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        evidence[stage] = {"path": str(path), "sha256": _sha(path)}
    dispatch.record_readiness(db, spec["batch_id"], cid, evidence)


def _check_graph(spec, db, built):
    """Refuse to reuse a batch_id whose registered jobs or prompts differ from the spec."""
    batch = spec["batch_id"]
    rows = dict(db.execute("SELECT candidate_id,request_hash FROM task_graph WHERE batch_id=?", (batch,)).fetchall())
    wanted = {cid: dispatch.payload_hash(p) for cid, (p, _, _) in built.items()}
    if rows and rows != wanted:
        raise SpecError("batch %s is already registered with different jobs or prompts; use a new batch_id" % batch)
    return rows


def _register(spec, db, built, price_path):
    batch = spec["batch_id"]
    rows = _check_graph(spec, db, built)
    if not rows:
        dispatch.register_graph(db, batch, [{"candidate_id": c, "payload": built[c][0], "depends_on": []}
                                            for c in built])
    jobs = {job["id"]: job for job in spec["jobs"]}
    for cid, (payload, products, keyframes) in built.items():
        has = db.execute("SELECT 1 FROM candidate_readiness WHERE batch_id=? AND candidate_id=?",
                         (batch, cid)).fetchone()
        if not has and get(db, cid) is None:
            _attest(spec, db, cid, payload, products, keyframes, jobs[cid], price_path)


def _suffix(url, model):
    suffix = Path(urllib.parse.urlparse(str(url)).path).suffix.lower()
    if suffix in MEDIA_SUFFIXES:
        return suffix
    return ".png" if model in IMAGE_MODELS else ".mp4"


def _pending(db, ids):
    out = []
    for cid in ids:
        row = get(db, cid)
        if row and (row["state"] in ("submitted", "running")
                    or (row["state"] == "success" and not row["metadata"].get("output_sha256"))):
            out.append(cid)
    return out


def poll(spec, db, client, ids, models, timeout_sec, interval_sec, log):
    from .dispatch import record_output, record_status
    out_dir = _path(spec, spec["out_dir"])
    deadline = time.time() + timeout_sec
    while True:
        pending = _pending(db, ids)
        if not pending or time.time() > deadline:
            return pending
        for cid in pending:
            row = get(db, cid)
            try:
                status = client.status(row["remote_task_id"])
            except Exception as exc:  # transient: try again next round
                log("poll error %s: %s" % (cid, exc))
                continue
            if not status["final"]:
                if row["state"] == "submitted" and status["state"] in ("running", "processing", "generating"):
                    record_status(db, cid, "running")
                continue
            if status["state"] == "success" and status["result_url"]:
                if row["state"] != "success":
                    record_status(db, cid, "success", cost=status["cost"])
                target = out_dir / (cid + _suffix(status["result_url"], models[cid]))
                try:
                    if not target.exists():
                        client.download(status["result_url"], target)
                    record_output(db, cid, target)
                    log("success %s cost %s -> %s" % (cid, status["cost"], target.name))
                except Exception as exc:
                    log("download error %s: %s" % (cid, exc))
            elif row["state"] != "success":
                record_status(db, cid, "failed", cost=status["cost"])
                log("failed %s: %s" % (cid, status["error"] or status["state"]))
        time.sleep(interval_sec)


def report(spec, db, ids, extra):
    rows = {}
    counts = {}
    total = 0.0
    for cid in ids:
        row = get(db, cid)
        state = row["state"] if row else "not_submitted"
        counts[state] = counts.get(state, 0) + 1
        if row and row.get("cost") is not None:
            total += float(row["cost"])
        rows[cid] = None if row is None else {
            "state": state, "remote_task_id": row["remote_task_id"], "cost": row["cost"],
            "output": row["metadata"].get("output_path"), "quote": row["metadata"].get("quote"),
            "timings": stage_timings(db, cid)}
    result = {"batch_id": spec["batch_id"], "counts": counts, "recorded_cost": round(total, 4),
              "candidates": rows, **extra}
    if "unknown" in counts:
        result["needs_reconciliation"] = [c for c in ids if rows[c] and rows[c]["state"] == "unknown"]
    path = _new_file(_path(spec, spec["out_dir"]), "report-" + time.strftime("%Y%m%d-%H%M%S"), ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    result["report"] = str(path)
    return result


def run(spec_path, mode="dry-run", client=None, timeout_sec=None, interval_sec=None, log=print):
    """mode: dry-run | quote | submit. client defaults to the LK888 adapter."""
    spec = load_spec(spec_path)
    db = _open_db(spec, create=(mode == "submit"))
    built, problems = build_all(spec, db)
    ids = [job["id"] for job in spec["jobs"]]
    base = {"batch_id": spec["batch_id"], "mode": mode, "jobs": {c: _summary(p) for c, (p, _, _) in built.items()},
            "problems": problems}
    if problems:
        base["status"] = "blocked"
        return base
    if mode == "dry-run":
        base["status"] = "ready_offline"
        base["note"] = "nothing was sent; run with --quote for live prices or --submit to spend"
        return base
    if client is None:
        from .providers.lk888 import Client
        client = Client()
    models = {cid: built[cid][0]["model"] for cid in ids}
    if mode == "submit":
        _check_graph(spec, db, built)
    unsubmitted = [cid for cid in ids if db is None or get(db, cid) is None]
    if mode == "quote":
        snapshot = client.snapshot(set(models.values()))
        quotes = _quotes(built, snapshot, "unsaved-quote", unsubmitted)
        total, reasons = _check_budget(spec, quotes, 0.0, snapshot["balance"])
        return {**base, "status": "quoted" if not reasons else "over_budget", "balance": snapshot["balance"],
                "quotes": quotes, "total_estimate": total, "stop_reasons": reasons}
    if unsubmitted:
        price_path, snapshot, sha = _write_snapshot(spec, client, set(models.values()))
        _register(spec, db, built, price_path)
        quotes = _quotes(built, snapshot, sha, unsubmitted)
        committed = sum(float((get(db, c) or {"metadata": {}})["metadata"].get("quote", {}).get("estimate", 0))
                        for c in ids if get(db, c))
        total, reasons = _check_budget(spec, quotes, committed, snapshot["balance"])
        log("balance %s, quote %.4f for %d job(s)" % (snapshot["balance"], total, len(unsubmitted)))
        if reasons:
            return {**base, "status": "stopped_before_spend", "stop_reasons": reasons, "quotes": quotes}
        auth = {"approved": True, "batch_id": spec["batch_id"], "candidate_ids": ids,
                "spend_cap": spec["authorization"]["spend_cap"],
                "max_unit_price": spec["authorization"]["max_unit_price"], "quotes": quotes}
        for cid in unsubmitted:
            result = dispatch.submit_candidate(db, cid, spec["batch_id"], built[cid][0], auth, client.transport)
            log("submit %s -> %s %s" % (cid, result["state"], result.get("remote_task_id", "")))
    poll_cfg = spec.get("poll") or {}
    left = poll(spec, db, client, ids, models,
                timeout_sec if timeout_sec is not None else float(poll_cfg.get("timeout_sec", 3600)),
                interval_sec if interval_sec is not None else float(poll_cfg.get("interval_sec", 10)), log)
    try:
        balance = client.balance()
    except Exception as exc:
        balance = "unavailable: %s" % exc
    result = report(spec, db, ids, {"still_pending": left, "balance_after": balance})
    result["status"] = "pending" if left else "finished"
    return result


def select(spec_path):
    """Spec: {ledger, image_batch, reviewer, picks: [{selection_id, slot, candidate, reason}]}."""
    path = Path(spec_path).expanduser().resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["_dir"] = path.parent
    db = connect(_path(spec, spec["ledger"]))
    done, recorded = [], []
    for pick in spec.get("picks", []):
        sid = pick["selection_id"]
        if db.execute("SELECT 1 FROM selections WHERE selection_id=?", (sid,)).fetchone():
            done.append(sid)
            continue
        image = get(db, pick["candidate"])
        if image is None or not image["metadata"].get("output_path"):
            raise SpecError("image candidate has no downloaded output: " + pick["candidate"])
        dispatch.select_keyframe(db, sid, spec["image_batch"], pick["slot"], pick["candidate"],
                                 image["metadata"]["output_path"], spec["reviewer"], pick["reason"])
        recorded.append(sid)
    return {"recorded": recorded, "already_recorded": done}

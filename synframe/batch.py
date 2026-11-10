"""Pure batch operations; no paid submission occurs here."""

from copy import deepcopy


def instantiate(template, batch_id):
    rows = []
    for row in template["rows"]:
        rows.append({k: deepcopy(v) for k, v in row.items()
                     if k not in ("task_id", "result", "error", "status", "candidates")})
    return {"batch_id": batch_id, "rows": rows}


def retryable_candidates(candidates):
    return [c["id"] for c in candidates if c.get("status") == "failed"]


def ready_tasks(tasks):
    by_id = {t["id"]: t for t in tasks}
    ready = []
    for task in tasks:
        dependencies = task.get("depends_on", [])
        if any(d not in by_id for d in dependencies):
            raise ValueError("unknown dependency")
        if task.get("status") == "draft" and all(by_id[d].get("status") == "success" for d in dependencies):
            ready.append(task["id"])
    return ready

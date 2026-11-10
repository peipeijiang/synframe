"""Asset selection retains identity, permissions and review status."""
import json
import sqlite3
import hashlib
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def select(assets, sku, persona=None):
    return [a for a in assets
            if a.get("sku") == sku
            and (persona is None or a.get("persona") == persona)
            and a.get("rights") == "approved"
            and a.get("creative_qc") == "pass"
            and a.get("technical_qc") == "pass"]


def open_catalog(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS assets (
        id TEXT PRIMARY KEY, sku TEXT NOT NULL, persona TEXT,
        rights TEXT NOT NULL, technical_qc TEXT NOT NULL,
        creative_qc TEXT NOT NULL, path TEXT NOT NULL,
        source TEXT NOT NULL, metadata TEXT NOT NULL
    )""")
    db.commit()
    return db


def record(db, asset):
    required = ("id", "sku", "rights", "technical_qc", "creative_qc", "path", "source")
    if any(not isinstance(asset.get(k), str) or not asset[k] for k in required):
        raise ValueError("asset identity, path, source, rights and both QC statuses are required")
    if not Path(asset["path"]).is_file():
        raise ValueError("asset path does not exist")
    digest = file_hash(asset["path"])
    if asset.get("sha256") and asset["sha256"] != digest:
        raise ValueError("asset hash does not match file")
    metadata = dict(asset.get("metadata", {}))
    metadata["sha256"] = digest
    with db:
        db.execute("""INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET sku=excluded.sku, persona=excluded.persona,
                      rights=excluded.rights, technical_qc=excluded.technical_qc,
                      creative_qc=excluded.creative_qc, path=excluded.path,
                      source=excluded.source, metadata=excluded.metadata""",
                   (asset["id"], asset["sku"], asset.get("persona"), asset["rights"],
                    asset["technical_qc"], asset["creative_qc"], asset["path"],
                    asset["source"], json.dumps(metadata, ensure_ascii=False)))


def query(db, sku, persona=None):
    rows = db.execute("""SELECT id,sku,persona,rights,technical_qc,creative_qc,path,source,metadata
                         FROM assets WHERE sku = ? AND rights = 'approved'
                         AND technical_qc = 'pass' AND creative_qc = 'pass'
                         AND (? IS NULL OR persona = ?)""", (sku, persona, persona)).fetchall()
    keys = ("id", "sku", "persona", "rights", "technical_qc", "creative_qc", "path", "source", "metadata")
    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item["metadata"] = json.loads(item["metadata"])
        if Path(item["path"]).is_file():
            digest = file_hash(item["path"])
            if digest == item["metadata"].get("sha256"):
                result.append(item)
    return result

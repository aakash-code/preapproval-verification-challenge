"""SQLite audit trail for reviews and evidence captures.

Every completed review is recorded in a small SQLite database (stdlib only, no
new dependency) so there is a proper, queryable audit trail beyond the files on
disk. A DB failure must never crash a review — all writes are wrapped and log a
warning on failure.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from .logging_config import REPO_ROOT
from .models import VerificationResult

logger = logging.getLogger("preapproval.db")

DEFAULT_DB_PATH = REPO_ROOT / "data" / "preapproval.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_name TEXT UNIQUE NOT NULL,
    pdf_filename TEXT NOT NULL,
    engine TEXT NOT NULL,
    category TEXT NOT NULL,
    participant_name TEXT,
    participant_age INTEGER,
    provider_name TEXT,
    requested_item TEXT,
    url TEXT,
    fee_stated TEXT,
    fee_on_website TEXT,
    rate_verdict TEXT,
    cap_check TEXT,
    summary TEXT,
    review_timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    criterion_id TEXT NOT NULL,
    criterion_text TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    evidence_url TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    finding_id INTEGER REFERENCES findings(id) ON DELETE SET NULL,
    criterion_id TEXT,
    kind TEXT NOT NULL,
    file_name TEXT NOT NULL,
    url TEXT,
    label TEXT,
    sha256 TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the audit DB. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _mtime_iso(path: Path) -> str:
    ts = datetime.datetime.fromtimestamp(
        path.stat().st_mtime, tz=datetime.timezone.utc
    )
    return ts.isoformat()


def _criterion_id_from_filename(file_name: str, valid_ids: List[str]) -> Optional[str]:
    """Best-effort recovery of a criterion id from an evidence-*.png filename.

    The browser names files ``evidence-<criterion_slug>-<label_slug>.png`` and
    slugifies each part (lowercased, non-alphanumerics -> '-'). We match the
    part after ``evidence-`` back to a known criterion id by longest prefix.
    """
    low = file_name.lower()
    if not low.startswith("evidence-"):
        return None
    remainder = low[len("evidence-"):]
    # Longest matching id first, so e.g. 'published_fees' wins over 'published'.
    for cid in sorted(valid_ids, key=len, reverse=True):
        cid_slug = re.sub(r"[^a-z0-9]+", "-", cid.lower()).strip("-")
        if remainder == cid_slug or remainder.startswith(cid_slug + "-"):
            return cid
    return None


def save_review(
    result: VerificationResult, out_dir: Path, review_name: str, engine: str
) -> int:
    """Upsert one review plus its findings and evidence rows.

    Re-running a review with the same ``review_name`` replaces its rows rather
    than duplicating them. Never raises — a DB failure logs a warning and the
    review continues.
    """
    try:
        out_dir = Path(out_dir)
        conn = init_db(DEFAULT_DB_PATH)
        try:
            with conn:  # transaction
                # Upsert: delete an existing review of this name (cascades to
                # its findings/evidence) then insert fresh.
                existing = conn.execute(
                    "SELECT id FROM reviews WHERE review_name = ?", (review_name,)
                ).fetchone()
                if existing is not None:
                    conn.execute("DELETE FROM reviews WHERE id = ?", (existing["id"],))

                r = result.request
                rc = result.rate_comparison
                created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                pdf_filename = f"{review_name}.pdf"
                cur = conn.execute(
                    """INSERT INTO reviews
                       (review_name, pdf_filename, engine, category, participant_name,
                        participant_age, provider_name, requested_item, url, fee_stated,
                        fee_on_website, rate_verdict, cap_check, summary,
                        review_timestamp, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        review_name,
                        pdf_filename,
                        engine,
                        r.category.value,
                        r.participant_name,
                        r.participant_age,
                        r.provider_name,
                        r.requested_item,
                        r.url,
                        r.fee_stated,
                        rc.fee_on_website,
                        rc.verdict,
                        rc.cap_check,
                        result.summary,
                        result.review_timestamp,
                        created_at,
                    ),
                )
                review_id = int(cur.lastrowid)

                # One findings row per finding; remember id per criterion so
                # evidence can be linked back.
                finding_id_by_criterion = {}
                for f in result.findings:
                    fcur = conn.execute(
                        """INSERT INTO findings
                           (review_id, criterion_id, criterion_text, status, note, evidence_url)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            review_id,
                            f.criterion_id,
                            f.criterion_text,
                            f.status.value,
                            f.note,
                            f.evidence_url,
                        ),
                    )
                    finding_id_by_criterion[f.criterion_id] = int(fcur.lastrowid)

                valid_ids = list(finding_id_by_criterion.keys())
                evidence_dir = out_dir / "evidence"
                if evidence_dir.exists():
                    for p in sorted(evidence_dir.iterdir()):
                        if not p.is_file():
                            continue
                        name = p.name
                        if name.startswith("fullpage-"):
                            kind = "fullpage"
                            criterion_id = None
                            finding_id = None
                        elif name.startswith("evidence-"):
                            kind = "targeted"
                            criterion_id = _criterion_id_from_filename(name, valid_ids)
                            finding_id = (
                                finding_id_by_criterion.get(criterion_id)
                                if criterion_id
                                else None
                            )
                        else:
                            # PDFs / anything else associated with a page record.
                            kind = "fullpage" if name.startswith("fullpage") else "other"
                            criterion_id = None
                            finding_id = None
                        conn.execute(
                            """INSERT INTO evidence
                               (review_id, finding_id, criterion_id, kind, file_name,
                                url, label, sha256, captured_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (
                                review_id,
                                finding_id,
                                criterion_id,
                                kind,
                                name,
                                r.url,
                                None,
                                _sha256(p),
                                _mtime_iso(p),
                            ),
                        )
                return review_id
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - a DB failure must never crash a review
        logger.warning("Could not record review %r in the audit DB: %s", review_name, exc)
        return -1


def list_reviews() -> List[dict]:
    """Return all recorded reviews (newest first), as plain dicts."""
    try:
        conn = init_db(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT * FROM reviews ORDER BY id DESC"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list reviews from the audit DB: %s", exc)
        return []


def get_evidence_for_review(review_name: str) -> List[dict]:
    """Return every evidence row for a given review name, as plain dicts."""
    try:
        conn = init_db(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                """SELECT e.* FROM evidence e
                   JOIN reviews r ON r.id = e.review_id
                   WHERE r.review_name = ?
                   ORDER BY e.id""",
                (review_name,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read evidence for review %r: %s", review_name, exc)
        return []

#!/usr/bin/env python3
"""Local state and decision engine for a Codex-operated Wellfound applier."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".wellfound"
DB_PATH = STATE_DIR / "applications.sqlite3"
EVENTS_PATH = STATE_DIR / "events.jsonl"
PRIVATE_PROFILE_PATH = ROOT / "candidate_profile.json"
EXAMPLE_PROFILE_PATH = ROOT / "candidate_profile.example.json"
PROFILE_PATH = Path(os.environ.get("CANDIDATE_PROFILE_PATH", PRIVATE_PROFILE_PATH))
if not PROFILE_PATH.exists():
    PROFILE_PATH = EXAMPLE_PROFILE_PATH
VALID_STATUSES = {
    "discovered", "considering", "rejected", "review_required",
    "approved", "applying", "applied", "failed", "human_required",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    os.chmod(DB_PATH, 0o600)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY,
          source_id TEXT,
          url TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          company TEXT NOT NULL,
          location TEXT,
          description TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'discovered',
          match_score INTEGER,
          rationale TEXT,
          answers_json TEXT,
          discovered_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
        CREATE TABLE IF NOT EXISTS transitions (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          from_status TEXT,
          to_status TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL
        );
        """
    )
    return db


def emit(event: str, **payload: Any) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"time": now(), "event": event, **payload}
    with EVENTS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(EVENTS_PATH, 0o600)
    print(json.dumps(record, ensure_ascii=False))


def load_profile() -> dict[str, Any]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def inferred_score(job: dict[str, str], profile: dict[str, Any]) -> tuple[int, str]:
    text = " ".join(str(job.get(k, "")) for k in ("title", "location", "description")).lower()
    title = job["title"].lower()
    excluded = [term for term in profile["exclude_title_terms"] if term.lower() in title]
    if excluded:
        return 0, f"Excluded title term: {', '.join(excluded)}"

    required_years = [int(n) for n in re.findall(r"(\d+)\+?\s*(?:years?|yrs?)", text)]
    if required_years and min(required_years) > profile["maximum_required_experience_years"]:
        return 15, f"Requires at least {min(required_years)} years; configured maximum is {profile['maximum_required_experience_years']}"

    skill_hits = [
        skill for skill in profile["preferred_skills"]
        if re.search(rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])", text)
    ]
    role_hits = [role for role in profile["target_roles"] if role.lower() in title]
    location_hits = [loc for loc in profile["location_preferences"] if loc.lower() in text]
    score = min(100, 30 + 8 * len(skill_hits) + 15 * bool(role_hits) + 10 * bool(location_hits))
    why = f"skills={skill_hits or ['none']}; target_title={bool(role_hits)}; preferred_location={bool(location_hits)}"
    return score, why


def cmd_init(_: argparse.Namespace) -> None:
    with connect() as db:
        db.execute("SELECT 1")
    emit("initialized", database=str(DB_PATH), profile=str(PROFILE_PATH))


def cmd_import(args: argparse.Namespace) -> None:
    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    required = {"url", "title", "company", "description"}
    missing = required - data.keys()
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(sorted(missing))}")
    timestamp = now()
    with connect() as db:
        db.execute(
            """INSERT INTO jobs(source_id,url,title,company,location,description,discovered_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET title=excluded.title, company=excluded.company,
                 location=excluded.location, description=excluded.description, updated_at=excluded.updated_at""",
            (data.get("source_id"), data["url"], data["title"], data["company"],
             data.get("location"), data["description"], timestamp, timestamp),
        )
        row = db.execute("SELECT id,status FROM jobs WHERE url=?", (data["url"],)).fetchone()
    emit("job_discovered", job_id=row["id"], title=data["title"], company=data["company"], status=row["status"])


def transition(db: sqlite3.Connection, job_id: int, status: str, note: str | None = None) -> None:
    if status not in VALID_STATUSES:
        raise SystemExit(f"Invalid status: {status}")
    row = db.execute("SELECT status,title,company FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"Unknown job id: {job_id}")
    timestamp = now()
    applied_at = timestamp if status == "applied" else None
    db.execute(
        "UPDATE jobs SET status=?,updated_at=?,applied_at=COALESCE(?,applied_at) WHERE id=?",
        (status, timestamp, applied_at, job_id),
    )
    db.execute(
        "INSERT INTO transitions(job_id,from_status,to_status,note,created_at) VALUES(?,?,?,?,?)",
        (job_id, row["status"], status, note, timestamp),
    )
    emit("status_changed", job_id=job_id, title=row["title"], company=row["company"],
         from_status=row["status"], status=status, note=note)


def cmd_score(args: argparse.Namespace) -> None:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (args.job_id,)).fetchone()
        if not row:
            raise SystemExit(f"Unknown job id: {args.job_id}")
        score, rationale = inferred_score(dict(row), load_profile())
        status = "review_required" if score >= args.threshold else "rejected"
        db.execute("UPDATE jobs SET match_score=?,rationale=? WHERE id=?", (score, rationale, args.job_id))
        transition(db, args.job_id, status, rationale)


def cmd_status(args: argparse.Namespace) -> None:
    query = "SELECT id,title,company,location,status,match_score,updated_at,url FROM jobs"
    params: tuple[Any, ...] = ()
    if args.status:
        query += " WHERE status=?"
        params = (args.status,)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params += (args.limit,)
    with connect() as db:
        rows = db.execute(query, params).fetchall()
    if args.json:
        print(json.dumps([dict(row) for row in rows], indent=2))
        return
    print(f"{'ID':>4}  {'STATUS':<16} {'SCORE':>5}  {'COMPANY':<24} TITLE")
    for row in rows:
        score = "-" if row["match_score"] is None else str(row["match_score"])
        print(f"{row['id']:>4}  {row['status']:<16} {score:>5}  {row['company'][:24]:<24} {row['title']}")


def cmd_show(args: argparse.Namespace) -> None:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (args.job_id,)).fetchone()
    if not row:
        raise SystemExit(f"Unknown job id: {args.job_id}")
    print(json.dumps(dict(row), indent=2))


def cmd_export(args: argparse.Namespace) -> None:
    output = Path(args.output)
    with connect() as db:
        rows = db.execute(
            """SELECT id,title,company,location,status,match_score,rationale,url,
                      discovered_at,updated_at,applied_at
               FROM jobs ORDER BY updated_at DESC"""
        ).fetchall()
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(rows[0].keys() if rows else [
            "id", "title", "company", "location", "status", "match_score",
            "rationale", "url", "discovered_at", "updated_at", "applied_at",
        ])
        writer.writerows([tuple(row) for row in rows])
    os.chmod(output, 0o600)
    emit("ledger_exported", output=str(output.resolve()), jobs=len(rows))


def cmd_set_status(args: argparse.Namespace) -> None:
    with connect() as db:
        transition(db, args.job_id, args.status, args.note)


def cmd_assess(args: argparse.Namespace) -> None:
    with connect() as db:
        row = db.execute("SELECT id FROM jobs WHERE id=?", (args.job_id,)).fetchone()
        if not row:
            raise SystemExit(f"Unknown job id: {args.job_id}")
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8")) if args.answers else None
        db.execute(
            "UPDATE jobs SET match_score=?,rationale=?,answers_json=?,updated_at=? WHERE id=?",
            (args.score, args.rationale, json.dumps(answers) if answers else None, now(), args.job_id),
        )
        transition(db, args.job_id, "review_required", args.rationale)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    add = sub.add_parser("import-job")
    add.add_argument("json_file")
    add.set_defaults(func=cmd_import)
    score = sub.add_parser("score")
    score.add_argument("job_id", type=int)
    score.add_argument("--threshold", type=int, default=65)
    score.set_defaults(func=cmd_score)
    status = sub.add_parser("status")
    status.add_argument("--status", choices=sorted(VALID_STATUSES))
    status.add_argument("--limit", type=int, default=100)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    show = sub.add_parser("show")
    show.add_argument("job_id", type=int)
    show.set_defaults(func=cmd_show)
    export = sub.add_parser("export-csv")
    export.add_argument("--output", default="applications.csv")
    export.set_defaults(func=cmd_export)
    set_status = sub.add_parser("set-status")
    set_status.add_argument("job_id", type=int)
    set_status.add_argument("status", choices=sorted(VALID_STATUSES))
    set_status.add_argument("--note")
    set_status.set_defaults(func=cmd_set_status)
    assess = sub.add_parser("assess")
    assess.add_argument("job_id", type=int)
    assess.add_argument("--score", type=int, required=True, choices=range(0, 101), metavar="0-100")
    assess.add_argument("--rationale", required=True)
    assess.add_argument("--answers")
    assess.set_defaults(func=cmd_assess)
    return cli


if __name__ == "__main__":
    parsed = parser().parse_args()
    try:
        parsed.func(parsed)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

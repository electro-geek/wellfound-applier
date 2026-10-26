#!/usr/bin/env python3
"""Safe wrapper around rote's Playwright browser for Wellfound authentication."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DOMAIN = "wellfound.com"
PROFILE = "job-applier"
JOBS_URL = "https://wellfound.com/jobs"
WORKSPACE_NAME = "wellfound-applier"
WORKSPACE_DIR = Path.home() / ".rote" / "workspaces" / WORKSPACE_NAME


def ensure_workspace() -> bool:
    if WORKSPACE_DIR.is_dir():
        return True
    completed = subprocess.run(
        ["rote", "init", WORKSPACE_NAME, "--seq"], check=False
    )
    return completed.returncode == 0 and WORKSPACE_DIR.is_dir()


def run(*args: str) -> int:
    if not shutil.which("rote"):
        print("error: rote is not installed", file=sys.stderr)
        return 1
    if not ensure_workspace():
        print("error: could not create the rote browser workspace", file=sys.stderr)
        return 1
    completed = subprocess.run(["rote", *args], cwd=WORKSPACE_DIR, check=False)
    return completed.returncode


def auth_status(_: argparse.Namespace) -> int:
    return run("browse", "auth", "session", "list", "--domain", DOMAIN)


def login(_: argparse.Namespace) -> int:
    print("Opening a visible browser. Enter credentials and solve CAPTCHA/MFA yourself.")
    print("After the authenticated jobs page appears, run: python3 browser_session.py save")
    return run("browse", "--headed", "--new-tab", "--no-snapshot", JOBS_URL)


def save(_: argparse.Namespace) -> int:
    return run("browse", "auth", "save", "--domain", DOMAIN, "--profile", PROFILE)


def discover(_: argparse.Namespace) -> int:
    print(json.dumps({"event": "browser_start", "mode": "headless", "url": JOBS_URL}))
    code = run("browse", "--headless", "--session", PROFILE, "--new-tab", "--no-snapshot", JOBS_URL)
    if code == 0:
        print("Browser opened. Codex must now inspect fresh page state before interacting.")
    return code


def detect_gate(_: argparse.Namespace) -> int:
    return run("browse", "auth", "detect", "--json")


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(required=True)
    for name, func in {
        "auth-status": auth_status,
        "login": login,
        "save": save,
        "discover": discover,
        "detect-gate": detect_gate,
    }.items():
        sub.add_parser(name).set_defaults(func=func)
    args = cli.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

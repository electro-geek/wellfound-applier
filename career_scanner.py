#!/usr/bin/env python3
"""Find profile-matching roles on company career sites with headless Playwright.

The browser is supplied by rote's persistent Playwright runtime. Bing is used only
to resolve an official careers/ATS page; all returned jobs are read and verified
from the career site itself.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import wellfound_agent

ROOT = Path(__file__).resolve().parent
COMPANIES_PATH = ROOT / "career_companies.json"
PRIVATE_PROFILE_PATH = ROOT / "candidate_profile.json"
EXAMPLE_PROFILE_PATH = ROOT / "candidate_profile.example.json"
PROFILE_PATH = Path(os.environ.get("CANDIDATE_PROFILE_PATH", PRIVATE_PROFILE_PATH))
if not PROFILE_PATH.exists():
    PROFILE_PATH = EXAMPLE_PROFILE_PATH
OUTPUT_JSON = ROOT / "career_matches.json"
OUTPUT_CSV = ROOT / "career_matches.csv"
OUTPUT_MD = ROOT / "career_matches.md"
BROWSER_WORKER = ROOT / "career_browser_worker.js"

ATS_HOSTS = (
    "ashbyhq.com", "greenhouse.io", "lever.co", "workable.com",
    "smartrecruiters.com", "myworkdayjobs.com", "jobs.otta.com",
    "wellfound.com", "applytojob.com", "bamboohr.com",
)
JOB_WORDS = (
    "backend", "back-end", "python", "django", "fastapi", "platform engineer",
    "software engineer", "ai engineer", "machine learning engineer", "developer",
    "founding engineer", "infrastructure engineer", "research engineer",
    "robotics engineer", "robotics software", "systems engineer", "ml engineer",
    "inference engineer", "distributed systems", "c++", "golang", "go engineer",
)
BLOCKED_HOSTS = (
    "bing.com", "google.com", "linkedin.com", "facebook.com", "instagram.com",
    "x.com", "twitter.com", "youtube.com", "wikipedia.org", "crunchbase.com",
    "geeksforgeeks.org", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "simplyhired.com", "monster.com",
)


def decode_bing_url(url: str) -> str:
    """Decode search-engine redirect URLs when present."""
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else url
    if "bing.com" not in parsed.netloc or "/ck/a" not in parsed.path:
        return url
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded:
        return url
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        import base64

        encoded += "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url


def is_jobish(title: str, url: str) -> bool:
    text = f"{title} {unquote(url)}".lower()
    return any(word in text for word in JOB_WORDS)


def is_external_result(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return url.startswith("http") and not any(blocked in host for blocked in BLOCKED_HOSTS)


def extract_result(stdout: str) -> Any:
    """Extract a JSON value printed as the Playwright code result by rote."""
    ref_match = re.findall(r"@(\d+)", stdout)
    if not ref_match:
        raise RuntimeError(f"rote did not return a result reference: {stdout[-500:]}")
    queried = subprocess.run(
        ["rote", "query", f"@{ref_match[-1]}", ".content[0].text"],
        cwd=ROTE_WORKSPACE, check=False, capture_output=True, text=True,
    )
    if queried.returncode:
        raise RuntimeError(queried.stderr.strip() or "could not read rote result")
    envelope = json.loads(queried.stdout)
    match = re.search(r"### Result\n(.*?)\n### Ran Playwright", envelope, re.S)
    if not match:
        raise RuntimeError(envelope[-800:])
    return json.loads(match.group(1))


class CareerBrowser:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["node", str(BROWSER_WORKER)], cwd=ROOT, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=1,
        )

    def run(self, payload: dict[str, str]) -> Any:
        if self.process.poll() is not None:
            error = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(error.strip() or "Playwright worker stopped unexpectedly")
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            error = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(error.strip() or "Playwright worker returned no response")
        result = json.loads(response)
        if result.get("ok") is False and set(result) <= {"ok", "error"}:
            raise RuntimeError(result.get("error", "Playwright request failed"))
        return result

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.run({"action": "close"})
            except (RuntimeError, BrokenPipeError):
                self.process.terminate()

    def discover(self, company: dict[str, str]) -> dict[str, Any]:
        handle = company.get("handle", "")
        queries = (
            f'"{company["name"]}" careers jobs {company.get("location", "")}',
            f'{handle or company["name"]} backend python golang platform ai software engineer job',
        )
        combined: list[dict[str, str]] = []
        page_titles = []
        for query in queries:
            result = self.run({"action": "discover", "query": query})
            page_titles.append(result.get("pageTitle", ""))
            combined.extend(result.get("results", []))
        unique = []
        seen = set()
        for result in combined:
            decoded = decode_bing_url(result.get("url", ""))
            if decoded not in seen:
                seen.add(decoded)
                unique.append(result)
        return {"pageTitle": " | ".join(page_titles), "results": unique}

    def scrape(self, url: str) -> dict[str, Any]:
        return self.run({"action": "scrape", "url": url})


def choose_career_pages(search: dict[str, Any]) -> list[str]:
    decoded = []
    for item in search.get("results", []):
        url = decode_bing_url(item.get("url", ""))
        if is_external_result(url):
            decoded.append((item.get("title", ""), url))
    preferred = [
        url for title, url in decoded
        if any(host in urlparse(url).netloc for host in ATS_HOSTS)
        or any(word in f"{title} {url}".lower() for word in ("career", "jobs", "join us", "open positions"))
    ]
    return list(dict.fromkeys(preferred + [url for _, url in decoded]))[:3]


def direct_search_jobs(search: dict[str, Any], company: str) -> list[dict[str, str]]:
    jobs = []
    for item in search.get("results", []):
        title = item.get("title", "").strip()
        url = decode_bing_url(item.get("url", "")).split("#", 1)[0]
        if is_external_result(url) and is_jobish(title, url):
            jobs.append({"company": company, "title": title, "url": url})
    return list({job["url"]: job for job in jobs}.values())


def page_matches_company(detail: dict[str, Any], company: str) -> bool:
    haystack = f"{detail.get('title', '')} {detail.get('body', '')[:5000]}".casefold()
    aliases = {company.casefold(), company.casefold().replace("google ", "")}
    return any(alias in haystack for alias in aliases)


def collect_job_links(page: dict[str, Any], company: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    jobs = []
    for link in page.get("links", []):
        title = link.get("title", "").strip()
        url = link.get("url", "").split("#", 1)[0]
        host = urlparse(url).netloc.lower()
        job_path = any(token in url.lower() for token in ("/job", "/position", "/opening", "/careers/"))
        ats = any(ats_host in host for ats_host in ATS_HOSTS)
        specific_job_path = job_path and url.rstrip("/").lower().split("/")[-1] not in {"job", "jobs", "career", "careers", "openings"}
        role_signal = is_jobish(title, url)
        if url not in seen and is_external_result(url) and role_signal and (specific_job_path or ats):
            seen.add(url)
            jobs.append({"company": company, "title": title, "url": url})
    return jobs


def collect_career_links(page: dict[str, Any]) -> list[str]:
    links = []
    for link in page.get("links", []):
        title = link.get("title", "")
        url = link.get("url", "").split("#", 1)[0]
        text = f"{title} {url}".lower()
        if is_external_result(url) and (
            any(host in urlparse(url).netloc for host in ATS_HOSTS)
            or any(word in text for word in ("career", "open positions", "job openings", "join us", "join the team"))
        ):
            links.append(url)
    return list(dict.fromkeys(links))[:5]


def rendered_job_title(detail: dict[str, Any], fallback: str) -> str:
    title = detail.get("title", "").strip()
    title = re.sub(r"^(?:Job opportunity|Careers?|Jobs?)\s*:\s*", "", title, flags=re.I)
    return title or fallback


def eligible_job_location(detail: dict[str, Any], profile: dict[str, Any] | None = None) -> bool:
    """Reject clearly country-locked roles outside the configured India/US/global scope."""
    if profile and profile.get("accept_any_location"):
        return True
    url = detail.get("finalUrl", "").lower()
    opening = detail.get("body", "")[:1500].lower()
    india_signal = any(term in f"{url} {opening}" for term in (
        "india", "bengaluru", "bangalore", "gurugram", "gurgaon", "noida",
        "united states", "usa", "u.s.", "/united-states/",
        "remote - everywhere", "remote everywhere", "worldwide", "global remote",
    ))
    foreign_lock = any(term in f"{url} {opening}" for term in (
        "/canada/", "canada only", "/israel/", "israel only",
        "/united-kingdom/", "united kingdom only",
        "europe only", "location\n\neurope", "location type\n\neurope",
    ))
    return india_signal or not foreign_lock


def scan(companies: list[dict[str, str]], delay: float = 1.0) -> dict[str, Any]:
    browser = CareerBrowser()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    matches: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    visited_jobs: set[str] = set()

    for index, company in enumerate(companies, 1):
        name = company["name"]
        print(f"[{index}/{len(companies)}] {name}", flush=True)
        try:
            search = browser.discover(company)
            pages = choose_career_pages(search)
            # Prefer links found on an official career/ATS page. Search-result job
            # copies are frequently stale, duplicated, or belong to a different
            # employer with a similar name, so use them only as a last resort.
            candidates: list[dict[str, str]] = []
            for career_url in pages:
                page = browser.scrape(career_url)
                candidates.extend(collect_job_links(page, name))
                if not candidates:
                    for nested_url in collect_career_links(page):
                        nested_page = browser.scrape(nested_url)
                        candidates.extend(collect_job_links(nested_page, name))
                if candidates:
                    break
            if not candidates:
                candidates = direct_search_jobs(search, name)
            for candidate in candidates[:20]:
                if candidate["url"] in visited_jobs:
                    continue
                visited_jobs.add(candidate["url"])
                detail = browser.scrape(candidate["url"])
                description = detail.get("body", "")
                title = rendered_job_title(detail, candidate["title"])
                if not page_matches_company(detail, name) or not eligible_job_location(detail, profile):
                    continue
                record = {
                    **candidate,
                    "title": title,
                    "location": company.get("location", ""),
                    "description": description,
                }
                score, rationale = wellfound_agent.inferred_score(record, profile)
                if score >= profile["automation"]["minimum_match_score"]:
                    matches.append({
                        "company": name,
                        "title": title,
                        "location": company.get("location", ""),
                        "score": score,
                        "rationale": rationale,
                        "url": detail.get("finalUrl") or candidate["url"],
                    })
            print(f"  career pages={len(pages)}, candidate jobs={len(candidates)}, matches={sum(m['company'] == name for m in matches)}", flush=True)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            errors.append({"company": name, "error": str(error)[:500]})
            print(f"  warning: {error}", file=sys.stderr, flush=True)
        time.sleep(delay)

    browser.close()

    matches.sort(key=lambda item: (-item["score"], item["company"], item["title"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "companies_scanned": len(companies),
        "matches": matches,
        "errors": errors,
    }


def write_reports(report: dict[str, Any], output_prefix: str = "career_matches") -> None:
    output_json = ROOT / f"{output_prefix}.json"
    output_csv = ROOT / f"{output_prefix}.csv"
    output_md = ROOT / f"{output_prefix}.md"
    output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with output_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("company", "title", "location", "score", "rationale", "url"))
        writer.writeheader()
        writer.writerows(report["matches"])
    lines = ["# Matching career-page jobs", "", f"Generated: {report['generated_at']}", ""]
    if report["matches"]:
        for match in report["matches"]:
            lines.append(f"- **{match['company']} — {match['title']}** ({match['score']}/100) — [Apply]({match['url']})")
    else:
        lines.append("No matching open roles were verified in this run.")
    if report["errors"]:
        lines.extend(("", f"Scanner warnings: {len(report['errors'])}"))
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", default=str(COMPANIES_PATH))
    parser.add_argument("--limit", type=int, help="scan only the first N companies")
    parser.add_argument("--names", help="comma-separated company names to scan")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between companies")
    parser.add_argument("--shard-index", type=int, default=0, help="zero-based shard index")
    parser.add_argument("--shard-count", type=int, default=1, help="number of shards")
    parser.add_argument("--output-prefix", default="career_matches", help="report filename prefix")
    args = parser.parse_args()
    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    if args.names:
        wanted = {name.strip().casefold() for name in args.names.split(",") if name.strip()}
        companies = [company for company in companies if company["name"].casefold() in wanted]
    if args.limit:
        companies = companies[: args.limit]
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard index must be within shard count")
    companies = companies[args.shard_index::args.shard_count]
    report = scan(companies, max(0, args.delay))
    write_reports(report, args.output_prefix)
    print(f"Found {len(report['matches'])} matches; reports: {args.output_prefix}.md, {args.output_prefix}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

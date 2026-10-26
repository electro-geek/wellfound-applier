# Career Page Job Scanner

A local-first toolkit for finding relevant jobs on company career pages with a
headless Playwright browser. It also includes an optional SQLite ledger for
tracking jobs reviewed or submitted through Wellfound.

The repository contains no résumé, credentials, browser session, application
answers, or candidate-specific profile. Those files stay local and are ignored
by Git.

## Features

- Discovers official career and applicant-tracking-system pages.
- Renders JavaScript job pages with headless Chromium.
- Scores jobs against configurable roles, skills, locations, and experience.
- Produces Markdown, CSV, and JSON reports.
- Splits large company lists into independent shards.
- Tracks application state locally in SQLite.
- Stops for CAPTCHA, MFA, and unknown mandatory application answers.

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer
- Chromium installed through Playwright
- Optional: `rote` for persistent Wellfound login sessions

Install the browser dependency:

```bash
npm install
npm run install-browser
```

If Playwright is supplied by another runtime, set `PLAYWRIGHT_CORE_PATH` and,
when necessary, `PLAYWRIGHT_EXECUTABLE_PATH` instead.

## Configure your private profile

Create the ignored local profile:

```bash
cp candidate_profile.example.json candidate_profile.json
```

Edit `candidate_profile.json` with your actual skills and preferences. Keep the
résumé in the project directory only if desired; PDF files are ignored. To keep
the profile elsewhere, set:

```bash
export CANDIDATE_PROFILE_PATH=/absolute/path/to/candidate_profile.json
```

Never put passwords or browser cookies in the profile. This project does not
read `.env` credentials.

## Configure companies

Company lists are JSON arrays:

```json
[
  {"name": "Example Company", "handle": "example", "location": "Remote"}
]
```

Use `career_companies.json`, one of the included example lists, or provide your
own file with `--companies`.

## Scan career pages

Run the default list:

```bash
python3 career_scanner.py
```

Useful options:

```bash
python3 career_scanner.py --companies growth_ai_companies.json --limit 5
python3 career_scanner.py --names "Cohere,ElevenLabs" --output-prefix ai_matches
python3 career_scanner.py --shard-index 0 --shard-count 4 --output-prefix shard_0
```

The scanner writes `<prefix>.md`, `<prefix>.csv`, and `<prefix>.json`. Generated
reports are ignored because they can contain personal matching rationale and
quickly become stale.

## Local Wellfound ledger

Initialize and inspect the private application ledger:

```bash
python3 wellfound_agent.py init
python3 wellfound_agent.py status
python3 wellfound_agent.py export-csv
```

State is stored under `.wellfound/` with restrictive filesystem permissions.
Both that directory and exported application CSV files are ignored.

For an authenticated browser session using `rote`:

```bash
python3 browser_session.py login
python3 browser_session.py save
python3 browser_session.py discover
```

Complete login, MFA, and CAPTCHA yourself in the visible browser. Session files
must never be committed or shared.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Privacy and safety

- `.env`, profiles, PDFs, application history, browser state, and screenshots
  are excluded by `.gitignore`.
- The checked-in profile is fictional and safe to customize locally.
- Application answers should come only from the résumé or explicit user facts.
- Review generated answers before submission and never bypass CAPTCHA or MFA.
- If a credential was ever stored in plaintext, rotate it; `.gitignore` cannot
  invalidate or erase an exposed secret.

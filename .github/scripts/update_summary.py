#!/usr/bin/env python3
"""
update_summary.py — refresh the profile header's ✳ commit summary.

All logic lives in THIS repo (self-dependent; see the plan):
  1. DISCOVER  every repo James owns from the GitHub API (public + private via GH_PAT).
               Default = tracked; a tiny opt-out EXCLUDE set + forks/archived are dropped.
               New repos are picked up automatically — nothing to register.
  2. WINDOW    last_run = when assets/header.png last changed (git log). So "what's new" =
               commits since the header last updated. First run: a short lookback.
  3. EARLY-EXIT if no tracked repo has pushed_at newer than last_run (one cheap listing call —
               no commit fetches, no Haiku, no render).
  4. FETCH     commit subjects since last_run for the repos that moved.
  5. SUMMARIZE via Claude Haiku → the TWO highest-signal threads as
               "type(project|section): detail · type(project|section): detail".
               One universal privacy guardrail: never emit names/clients/emails/paths.
  6. VALIDATE  against a strict regex; retry once. If still invalid → leave the header
               UNCHANGED (the existing PNG is the last-good state — no separate state file).
  7. RENDER    assets/header.png via gen_header.py. The workflow commits it only if it changed.

Stdlib only (urllib) so CI needs no pip install.
Env: GH_PAT (repo scope, reads private repos), ANTHROPIC_API_KEY, SUMMARY_MODEL.
Run `python3 .github/scripts/update_summary.py --self-test` to exercise filter/validate offline.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

ROOT       = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN_HEADER = os.path.join(ROOT, "assets", "gen_header.py")
HEADER_PNG = os.path.join("assets", "header.png")   # repo-relative (for git log)

OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "jj-valentine")

# Opt-out only — default is "tracked". Add a repo here only to HIDE it from the summary.
EXCLUDE = {
    "dotfiles",             # config noise
    "algorithms",           # stale (2023)
    "blog-content",         # writing — belongs in the README's "latest writing", not activity
    "journey-to-anthropic", # paused (per HANDOFF) — don't surface until built on consistently
    "jj-valentine",         # this repo — don't feed the bot's own refresh commits back in
}

FIRST_RUN_LOOKBACK_DAYS = 14   # window for the very first run (no prior header commit)
MAX_COMMITS_PER_REPO    = 30

# type(project|section): detail   ·   type(project|section): detail
_THREAD = r"[a-z]+\([a-z0-9._-]+\|[a-z0-9._-]+\): .+"
THREAD_RE = re.compile(r"^%s$" % _THREAD)

SYSTEM_PROMPT = (
    "You write a one-line activity summary for a developer's profile header from their recent "
    "git commits. Output EXACTLY two threads joined by ' · ' in this format:\n"
    "type(project|section): detail · type(project|section): detail\n"
    "Rules:\n"
    "- Pick the TWO highest-signal threads across all commits (the most substantive work).\n"
    "- type = a conventional-commit type (feat, fix, perf, refactor, chore, ...). "
    "project = the repo name. section = one short token for the area touched. "
    "detail = a few words on what changed.\n"
    "- No spaces around the '|'. Lowercase everything. No emoji, no quotes, no trailing period.\n"
    "- PRIVACY: never include names of people or clients, emails, file paths, URLs, or any "
    "private identifying detail. Describe only the technical change, in general terms.\n"
    "- Output ONLY that one line."
)


# ---------------------------------------------------------------------------- http
def _get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def gh_headers(token):
    return {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jj-valentine-profile-header",
    }


# ---------------------------------------------------------------------------- discover
def discover_repos(token):
    """All repos the owner can see, newest-pushed first (public + private via GH_PAT)."""
    url = "https://api.github.com/user/repos?affiliation=owner&sort=pushed&per_page=100"
    return _get_json(url, gh_headers(token))


def tracked(repos):
    """Opt-out filter: drop forks, archived, and the EXCLUDE set; keep everything else."""
    out = []
    for r in repos:
        if r.get("fork") or r.get("archived") or r.get("name") in EXCLUDE:
            continue
        out.append(r)
    return out


def fetch_commits(name, since, token):
    """First lines of non-bot commit subjects in `name` since `since` (ISO 8601)."""
    url = ("https://api.github.com/repos/%s/%s/commits?since=%s&per_page=%d"
           % (OWNER, name, since, MAX_COMMITS_PER_REPO))
    try:
        data = _get_json(url, gh_headers(token))
    except urllib.error.HTTPError as e:
        print("  ! %s: commits fetch failed (%s)" % (name, e.code))
        return []
    subjects = []
    for c in data:
        login = ((c.get("author") or {}).get("login") or "")
        if login.endswith("[bot]"):
            continue
        msg = ((c.get("commit") or {}).get("message") or "").strip()
        if msg:
            subjects.append(msg.splitlines()[0].strip())
    return subjects


# ---------------------------------------------------------------------------- summarize
def anthropic_summary(prompt_text, model, api_key, reminder=""):
    if not api_key:
        raise ValueError("no ANTHROPIC_API_KEY")
    body = json.dumps({
        "model": model,
        "max_tokens": 80,
        "system": SYSTEM_PROMPT + reminder,
        "messages": [{"role": "user", "content": prompt_text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": api_key, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["content"][0]["text"].strip()


def validate(summary):
    """True if `summary` is exactly two well-formed threads joined by ' · '."""
    if not summary or len(summary) > 140:
        return False
    parts = summary.split(" · ")
    return len(parts) == 2 and all(THREAD_RE.match(p.strip()) for p in parts)


def build_summary(commits_by_repo, model, api_key):
    """Haiku → validated 2-thread summary, retry once. None if it can't produce a valid one."""
    blocks = ["[%s]\n%s" % (name, "\n".join("- " + s for s in subs))
              for name, subs in commits_by_repo.items() if subs]
    prompt = "Recent commits:\n\n" + "\n\n".join(blocks)

    for reminder in ("", "\nREMINDER: output ONLY 'type(project|section): detail · "
                         "type(project|section): detail' — two threads, lowercase, no extra text."):
        try:
            cand = anthropic_summary(prompt, model, api_key, reminder)
        except Exception as e:
            print("  ! Haiku call failed: %s" % e)
            return None
        cand = cand.strip().strip('"').rstrip(".")
        print("  Haiku candidate: %r (valid=%s)" % (cand, validate(cand)))
        if validate(cand):
            return cand
    return None


# ---------------------------------------------------------------------------- window / render
def last_header_change():
    """ISO timestamp of the commit that last touched header.png; None if never."""
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "log", "-1", "--format=%cI", "--", HEADER_PNG],
            capture_output=True, text=True, check=True).stdout.strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def render(summary):
    subprocess.run([sys.executable, GEN_HEADER, summary], check=True)


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_moved(token):
    """(last_run, tracked_repos, repos_pushed_since_last_run). Cheap: one listing call."""
    now = datetime.now(timezone.utc)
    last_run = last_header_change() or (now - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)).isoformat()
    repos = tracked(discover_repos(token))
    cutoff = parse_iso(last_run)
    moved = [r for r in repos if parse_iso(r["pushed_at"]) > cutoff]
    return last_run, repos, moved


def check_for_work():
    """Cheap pre-flight (no librsvg/font/Haiku): write has_work to $GITHUB_OUTPUT, exit."""
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("error: GH_PAT (or GITHUB_TOKEN) is required")
    _, _, moved = compute_moved(token)
    has = "true" if moved else "false"
    print("has_work=%s (moved: %s)" % (has, ", ".join(r["name"] for r in moved) or "none"))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("has_work=%s\n" % has)
    sys.exit(0)


# ---------------------------------------------------------------------------- main
def main():
    token   = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    model   = os.environ.get("SUMMARY_MODEL", "").strip()
    if not token:
        sys.exit("error: GH_PAT (or GITHUB_TOKEN) is required")
    if not model:
        sys.exit("error: SUMMARY_MODEL variable is required (no hardcoded model — ops rule)")

    last_run, repos, moved = compute_moved(token)
    print("last_run (header.png last changed): %s" % last_run)
    print("tracked repos (%d): %s" % (len(repos), ", ".join(r["name"] for r in repos)))
    if not moved:
        print("no tracked repo pushed since last_run -> early exit (no Haiku, no render)")
        return
    print("moved since last_run: %s" % ", ".join(r["name"] for r in moved))

    commits_by_repo = {}
    for r in moved:
        subs = fetch_commits(r["name"], last_run, token)
        if subs:
            commits_by_repo[r["name"]] = subs
            print("  %s: %d commit(s)" % (r["name"], len(subs)))
    if not commits_by_repo:
        print("repos moved but no non-bot commits in window -> early exit")
        return

    if not api_key:
        sys.exit("error: ANTHROPIC_API_KEY is required to summarize")
    summary = build_summary(commits_by_repo, model, api_key)
    if summary is None:
        print("no valid summary produced -> leaving header unchanged (existing PNG is last-good)")
        return
    print("summary: %s" % summary)

    render(summary)
    print("rendered assets/header.png")


# ---------------------------------------------------------------------------- self-test
def self_test():
    ok = True

    good = "feat(cerebellum|recall): hybrid rerank · perf(intero|status): git cache"
    bads = [
        "feat(cerebellum | recall): x · perf(intero|status): y",   # spaces around |
        "feat(cerebellum|recall): only one thread",                # one thread
        "Feat(Cerebellum|Recall): x · perf(intero|status): y",     # uppercase type/proj
        "just some freeform text about my day",                    # no format
        good + " · feat(extra|thread): z",                         # three threads
    ]
    if not validate(good):
        print("FAIL validate(good)"); ok = False
    for b in bads:
        if validate(b):
            print("FAIL validate accepted bad: %r" % b); ok = False

    sample = [
        {"name": "cerebellum", "fork": False, "archived": False},
        {"name": "dotfiles",   "fork": False, "archived": False},
        {"name": "journey-to-anthropic", "fork": False, "archived": False},
        {"name": "jj-valentine", "fork": False, "archived": False},
        {"name": "some-fork",  "fork": True,  "archived": False},
        {"name": "old",        "fork": False, "archived": True},
        {"name": "ops",        "fork": False, "archived": False},
    ]
    if {r["name"] for r in tracked(sample)} != {"cerebellum", "ops"}:
        print("FAIL tracked()"); ok = False

    # invalid Haiku output (no key) -> None (header left unchanged)
    if build_summary({"cerebellum": ["x"]}, model="bad", api_key="") is not None:
        print("FAIL build_summary should be None on failure"); ok = False

    print("SELF-TEST: %s" % ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    if "--check" in sys.argv:
        check_for_work()
    main()

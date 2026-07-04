#!/usr/bin/env python3
"""Fetch GitHub stats and rewrite the stat lines in light.svg / dark.svg.

Each stat line in the SVGs has the shape
    <tspan class="key">KEY</tspan>:<tspan class="cc"> ...dots... </tspan><tspan class="value">VALUE</tspan>
where len(KEY) + len(dots) + len(VALUE) == DOTS_TOTAL, so the values stay
right-aligned. This script recomputes the dots whenever a value changes.

Env vars:
    GH_TOKEN   - GitHub token (classic PAT with repo scope to count private
                 contributions; falls back to public data otherwise)
    GH_LOGIN   - GitHub username (default: DarkRaiderCB)
    BIRTHDATE  - YYYY-MM-DD, used for the Uptime line
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

LOGIN = os.environ.get("GH_LOGIN", "DarkRaiderCB")
TOKEN = os.environ.get("GH_TOKEN", "")
BIRTHDATE = os.environ.get("BIRTHDATE", "")
SVG_FILES = ["light.svg", "dark.svg"]
DOTS_TOTAL = 51  # len(key) + len(dots) + len(value), constant per line


def api(url, payload=None):
    req = urllib.request.Request(url, method="POST" if payload else "GET")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    if payload:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read() or "{}")


def graphql(query, variables):
    status, body = api("https://api.github.com/graphql", {"query": query, "variables": variables})
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def rounded(n, step):
    """400 -> '400+' style display, floored to the nearest step."""
    floored = (n // step) * step
    return f"{floored}+" if floored < n else f"{n}"


def fetch_stats():
    data = graphql(
        """
        query($login: String!) {
          user(login: $login) {
            createdAt
            followers { totalCount }
            pullRequests { totalCount }
            repositoriesContributedTo(
              contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, PULL_REQUEST_REVIEW, REPOSITORY]
            ) { totalCount }
            repositories(ownerAffiliations: OWNER, first: 100) {
              totalCount
              nodes { name isFork stargazerCount }
            }
          }
        }
        """,
        {"login": LOGIN},
    )["user"]

    stars = sum(r["stargazerCount"] for r in data["repositories"]["nodes"])

    # Commit contributions must be queried per year (API limit of 1-year ranges)
    created = datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    commits = 0
    for year in range(created.year, now.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        cc = graphql(
            """
            query($login: String!, $from: DateTime!, $to: DateTime!) {
              user(login: $login) {
                contributionsCollection(from: $from, to: $to) {
                  totalCommitContributions
                  restrictedContributionsCount
                }
              }
            }
            """,
            {"login": LOGIN, "from": start.isoformat(), "to": end.isoformat()},
        )["user"]["contributionsCollection"]
        commits += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]

    stats = {
        "Repos": rounded(data["repositories"]["totalCount"], 5),
        "Contributed to": rounded(data["repositoriesContributedTo"]["totalCount"], 5),
        "Stars": str(stars),
        "Commits": rounded(commits, 50),
        "Followers": str(data["followers"]["totalCount"]),
        "PRs": str(data["pullRequests"]["totalCount"]),
    }

    loc = fetch_loc([r["name"] for r in data["repositories"]["nodes"] if not r["isFork"]])
    if loc is not None:
        stats["Lines of Code"] = rounded(loc, 1000)

    return stats


def fetch_loc(repo_names):
    """Net lines authored, from per-repo contributor stats. Returns None on failure."""
    total = 0
    try:
        for name in repo_names:
            url = f"https://api.github.com/repos/{LOGIN}/{name}/stats/contributors"
            for _ in range(5):
                status, body = api(url)
                if status != 202:  # 202 = stats still being computed, retry
                    break
                time.sleep(3)
            if status != 200 or not isinstance(body, list):
                continue
            for contributor in body:
                if contributor.get("author", {}).get("login") == LOGIN:
                    for week in contributor.get("weeks", []):
                        total += week.get("a", 0) - week.get("d", 0)
    except Exception as exc:
        print(f"LOC computation failed, keeping existing value: {exc}")
        return None
    return max(total, 0)


def uptime_string():
    birth = date.fromisoformat(BIRTHDATE)
    today = date.today()
    months = (today.year - birth.year) * 12 + today.month - birth.month
    if today.day < birth.day:
        months -= 1
    years, months = divmod(months, 12)
    return f"{years} years, {months} month{'s' if months != 1 else ''}"


def update_svg(path, stats):
    with open(path, encoding="utf-8") as f:
        content = f.read()

    for key, value in stats.items():
        dots = "." * max(DOTS_TOTAL - len(key) - len(value), 3)
        pattern = (
            rf'(<tspan class="key">{re.escape(key)}</tspan>:<tspan class="cc"> )'
            rf'\.+( </tspan><tspan class="value">)[^<]*(</tspan>)'
        )
        content, n = re.subn(pattern, rf"\g<1>{dots}\g<2>{value}\g<3>", content)
        if n == 0:
            print(f"WARNING: no line matched for key {key!r} in {path}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Updated {path}")


def main():
    if not TOKEN:
        sys.exit("GH_TOKEN is not set")
    stats = fetch_stats()
    if BIRTHDATE:
        stats["Uptime"] = uptime_string()
    print("Stats:", json.dumps(stats, indent=2))
    for svg in SVG_FILES:
        update_svg(svg, stats)


if __name__ == "__main__":
    main()

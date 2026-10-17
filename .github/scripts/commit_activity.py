#!/usr/bin/env python3
"""Render commit activity (hour of day, day of week, languages) as dark/light SVG cards.

Reads every commit authored by GH_USER across the repositories the token can
see (private included), converts timestamps to TIMEZONE and writes
activity-dark.svg / activity-light.svg. Language shares are GitHub's per-repo
byte counts summed over the same repositories. Only aggregate numbers are
rendered; no repository names or commit messages leave the API.

Environment:
  GH_TOKEN       token with read access to the repositories (required)
  GH_USER        GitHub login whose commits are counted (required)
  EXCLUDE_REPOS  comma-separated repository names to skip
  TIMEZONE       IANA timezone, default Europe/Istanbul
  OUTPUT_DIR     where the SVGs are written, default current directory
  ALLOW_DECREASE set to 1 to accept a lower total than the current card
                 (e.g. after excluding a repository)
"""

import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://api.github.com"
FONT = "'Helvetica Neue', Helvetica, Arial, sans-serif"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SEGMENTS = [("Morning", range(6, 12)), ("Afternoon", range(12, 18)),
            ("Evening", range(18, 24)), ("Night", range(0, 6))]

# Text colors match tech-*.svg / disciplines-*.svg; bar levels (empty, 1-4) are the
# top-face greens of the 3D contribution graph (night-green / green themes).
# "other" is the neutral gray for the language bar's remainder segment.
THEMES = {
    "dark": {"title": "#8b949e", "muted": "#6e7681", "other": "#444444",
             "levels": ["#444444", "#1b7d28", "#24a736", "#2dd143", "#57da69"]},
    "light": {"title": "#656d76", "muted": "#8b949e", "other": "#d1d9e0",
              "levels": ["#efefef", "#d8e887", "#8cc569", "#47a042", "#1d6a23"]},
}


def get(url, token):
    """GET a GitHub API URL; returns (json, next_page_url)."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "commit-activity-card",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        match = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link", ""))
        return json.load(resp), (match.group(1) if match else None)


def paginate(url, token):
    while url:
        page, url = get(url, token)
        yield from page


def list_repos(token, excluded):
    repos = [r for r in paginate(f"{API}/user/repos?affiliation=owner&per_page=100", token)
             if not r["fork"] and r["name"].lower() not in excluded]
    private = sum(r["private"] for r in repos)
    print(f"{len(repos)} repositories visible ({private} private)")
    return repos


def collect_commit_dates(token, user, repos):
    seen, dates = set(), []
    for repo in repos:
        url = f"{API}/repos/{repo['full_name']}/commits?author={user}&per_page=100"
        try:
            for commit in paginate(url, token):
                if commit["sha"] in seen:  # same history pushed to two repos
                    continue
                seen.add(commit["sha"])
                dates.append(commit["commit"]["author"]["date"])
        except urllib.error.HTTPError as err:
            if err.code != 409:  # 409 = empty repository
                raise
    return dates


def collect_languages(token, repos):
    total = Counter()
    for repo in repos:
        languages, _ = get(f"{API}/repos/{repo['full_name']}/languages", token)
        total.update(languages)
    return total


def language_shares(languages, top=4):
    """Top languages plus an "Other" bucket, as (name, percent) pairs."""
    size = sum(languages.values())
    if not size:
        return []
    ranked = languages.most_common()
    shares = [(name, 100 * value / size) for name, value in ranked[:top]]
    rest = sum(value for _, value in ranked[top:])
    if rest:
        shares.append(("Other", 100 * rest / size))
    return shares


def bars(counts, x0, width, y_base, height, gap, theme):
    peak_value = max(counts) or 1
    step = width / len(counts)
    out = []
    for i, value in enumerate(counts):
        h = max(round(height * value / peak_value, 1), 1.5)
        level = math.ceil(4 * value / peak_value)  # 0 = empty, 4 = busiest quarter
        fill = theme["levels"][level]
        out.append(f'<rect x="{x0 + i * step:.1f}" y="{y_base - h:.1f}" width="{step - gap:.1f}" '
                   f'height="{h}" rx="1" fill="{fill}"/>')
    return out


def text(x, y, body, size, weight, fill, spacing=0, anchor="start"):
    spacing_attr = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}"'
            f'{spacing_attr} text-anchor="{anchor}" fill="{fill}">{body}</text>')


def current_total(path):
    """Commit total shown on an existing card, or 0."""
    match = path.exists() and re.search(r"([\d,]+) COMMITS", path.read_text(encoding="utf-8"))
    return int(match.group(1).replace(",", "")) if match else 0


def language_section(shares, y, theme):
    """Stacked share bar plus a dot legend; colors step down the green levels by rank."""
    colors = [theme["other"] if name == "Other" else theme["levels"][max(4 - i, 1)]
              for i, (name, _) in enumerate(shares)]
    gap, width = 3, 650
    usable = width - gap * (len(shares) - 1)
    parts, x = [text(0, y, "BY LANGUAGE", 10, 400, theme["muted"], 2)], 0.0
    for (name, pct), color in zip(shares, colors):
        w = max(usable * pct / 100, 2)
        parts.append(f'<rect x="{x:.1f}" y="{y + 10}" width="{w:.1f}" height="8" rx="1" fill="{color}"/>')
        x += w + gap
    legend = "".join(
        f'<tspan dx="{0 if i == 0 else 18}" font-size="14" fill="{color}">●</tspan>'
        f'<tspan dx="6" fill="{theme["muted"]}">{html.escape(name)} '
        f'{"<1" if pct < 0.5 else round(pct)}%</tspan>'
        for i, ((name, pct), color) in enumerate(zip(shares, colors)))
    parts.append(f'<text x="0" y="{y + 42}" font-family="{FONT}" font-size="13" font-weight="300" '
                 f'letter-spacing="1.5">{legend}</text>')
    return parts


def render(hours, days, total, first_year, last_year, utc_label, langs, theme):
    peak_hour = max(range(24), key=lambda h: hours[h])
    busiest_day = max(range(7), key=lambda d: days[d])
    years = str(first_year) if first_year == last_year else f"{first_year} – {last_year}"
    shares = "  ·  ".join(
        f"{name} {round(100 * sum(hours[h] for h in span) / total)}%" for name, span in SEGMENTS)

    hx, hw, dx, dw, base, height = 0, 430, 480, 170, 190, 80
    parts = [
        text(0, 28, f"Peak hour {peak_hour:02d}:00. Busiest day {WEEKDAYS[busiest_day]}.",
             18, 400, theme["title"]),
        text(0, 60, f"{total:,} COMMITS  ·  {years}  ·  {utc_label}", 11, 300, theme["muted"], 2),
        text(hx, 96, "BY HOUR", 10, 400, theme["muted"], 2),
        text(dx, 96, "BY WEEKDAY", 10, 400, theme["muted"], 2),
        *bars([hours[h] for h in range(24)], hx, hw, base, height, 4, theme),
        *bars([days[d] for d in range(7)], dx, dw, base, height, 6, theme),
    ]
    hour_step, day_step = hw / 24, dw / 7
    for h in (0, 6, 12, 18, 23):
        parts.append(text(round(hx + h * hour_step + (hour_step - 4) / 2, 1), base + 16,
                          f"{h:02d}", 10, 300, theme["muted"], anchor="middle"))
    for d, name in enumerate(WEEKDAYS):
        parts.append(text(round(dx + d * day_step + (day_step - 6) / 2, 1), base + 16,
                          name[:2], 10, 300, theme["muted"], anchor="middle"))
    parts.append(
        f'<text x="0" y="236" font-family="{FONT}" font-size="13">'
        f'<tspan font-weight="500" fill="{theme["title"]}">Rhythm</tspan>'
        f'<tspan dx="10" font-weight="300" letter-spacing="1.5" fill="{theme["muted"]}">{shares}</tspan>'
        f'</text>')
    height = 250
    if langs:
        parts.extend(language_section(langs, 272, theme))
        height = 330
    body = "\n  ".join(parts)
    return (f'<svg fill="none" viewBox="0 0 650 {height}" width="650" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg">\n  <rect width="650" height="{height}" fill="transparent"/>\n'
            f'  {body}\n</svg>\n')


def main():
    token, user = os.environ.get("GH_TOKEN"), os.environ.get("GH_USER")
    if not token or not user:
        sys.exit("GH_TOKEN and GH_USER are required")
    excluded = {n.strip().lower() for n in os.environ.get("EXCLUDE_REPOS", "").split(",") if n.strip()}
    tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Istanbul"))
    out_dir = Path(os.environ.get("OUTPUT_DIR", "."))

    repos = list_repos(token, excluded)
    stamps = [datetime.fromisoformat(d.replace("Z", "+00:00")).astimezone(tz)
              for d in collect_commit_dates(token, user, repos)]
    if not stamps:
        sys.exit("No commits found; keeping the existing cards")  # never publish an empty chart
    previous = current_total(out_dir / "activity-dark.svg")
    if len(stamps) < previous and os.environ.get("ALLOW_DECREASE") != "1":
        # Commit history only grows; a drop means the token lost repository access.
        sys.exit(f"Found {len(stamps)} commits but the current card shows {previous}; "
                 "check the token's repository access (keeping the existing cards)")

    hours = Counter(s.hour for s in stamps)
    days = Counter(s.weekday() for s in stamps)
    offset = datetime.now(tz).utcoffset().total_seconds() / 3600
    utc_label = f"UTC{offset:+g}"
    first_year, last_year = min(s.year for s in stamps), max(s.year for s in stamps)
    langs = language_shares(collect_languages(token, repos))

    for name, theme in THEMES.items():
        svg = render(hours, days, len(stamps), first_year, last_year, utc_label, langs, theme)
        (out_dir / f"activity-{name}.svg").write_text(svg, encoding="utf-8")
    print(f"{len(stamps)} commits rendered ({first_year}-{last_year}, {utc_label}); "
          + ", ".join(f"{name} {pct:.1f}%" for name, pct in langs))


if __name__ == "__main__":
    main()

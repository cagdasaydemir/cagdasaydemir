#!/usr/bin/env python3
"""Redraw the language ring of the 3D contribution graph with private repositories included.

github-profile-3d-contrib builds its ring from GraphQL contributionsCollection, which
reports commits in private repositories only as a restricted count, so the ring shows a
single "other" slice. This recomputes the same chart (top five primary languages weighted
by the user's commits over the last year, plus "other") from the REST API and swaps it
into every SVG under SVG_DIR. Geometry and animation mirror create-pie-language.ts; colors follow
whichever markup the tool emitted: inline stroke/fill attributes (<= 0.8) or the stroke-bg / fill-fg
CSS classes introduced in 0.9 for dark-mode support.

Environment:
  GH_TOKEN, GH_USER, EXCLUDE_REPOS  same as commit_activity.py
  SVG_DIR                           default profile-3d-contrib
"""

import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from commit_activity import collect_commits, list_repos, parse_date

PIE_HEIGHT = 200 * 1.3  # pieHeight in create-svg.ts
OTHER = ("other", "#444444")
# GitHub linguist colors for primary languages; unknown ones fall back to gray.
COLORS = {
    "C#": "#178600", "HTML": "#e34c26", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "C++": "#f34b7d", "C": "#555555", "PHP": "#4F5D95", "CSS": "#663399", "SCSS": "#c6538c",
    "Python": "#3572A5", "Shell": "#89e051", "Java": "#b07219", "Go": "#00ADD8",
    "Dockerfile": "#384d54", "Vue": "#41b883", "Kotlin": "#A97BFF", "Swift": "#F05138",
    "Rust": "#dea584", "Ruby": "#701516", "Dart": "#00B4AB",
}
GROUP_START = re.compile(r'<g transform="translate\((\d+(?:\.\d+)?), (\d+(?:\.\d+)?)\)">')


def language_contributions(commits, since):
    """[(language, color, commits)] like the tool: top five plus an "other" remainder."""
    recent = [repo for repo, date in commits if parse_date(date, timezone.utc) >= since]
    by_language = Counter(repo["language"] for repo in recent if repo["language"])
    top = [(name, COLORS.get(name, "#8b949e"), count) for name, count in by_language.most_common(5)]
    rest = len(recent) - sum(count for *_, count in top)
    return top + ([(*OTHER, rest)] if rest > 0 else [])


def arc_path(a0, a1, outer, inner):
    """Annular sector like d3.arc(): angle 0 at 12 o'clock, clockwise."""
    if a1 - a0 >= 2 * math.pi - 1e-9:  # a single language: two half rings
        return arc_path(a0, a0 + math.pi, outer, inner) + arc_path(a0 + math.pi, a1, outer, inner)
    large = 1 if a1 - a0 > math.pi else 0
    pt = lambda r, a: f"{r * math.sin(a):.3f},{-r * math.cos(a):.3f}"
    return (f"M{pt(outer, a0)}A{outer},{outer},0,{large},1,{pt(outer, a1)}"
            f"L{pt(inner, a1)}A{inner},{inner},0,{large},0,{pt(inner, a0)}Z")


def ring_group(x, y, languages, stroke_attr, fill_attr, animate):
    """stroke_attr / fill_attr are the attribute strings the tool used for the ring's
    background stroke and label fill, e.g. 'stroke="#000"' or 'class="stroke-bg"'."""
    height, row, steps = PIE_HEIGHT, 8, 5
    radius = height / 2
    font = height / row / 1.5
    offset = (row - len(languages)) / 2 + 0.5

    def anim(i):
        if not animate:
            return ""
        values = ";".join(str(0 if k < i else min((k - i) / steps, 1)) for k in range(len(languages) + steps))
        return f'<animate attributeName="fill-opacity" values="{values}" dur="3s" repeatCount="1"></animate>'

    markers, labels, paths = [], [], []
    total, angle = sum(c for *_, c in languages), 0.0
    for i, (name, color, count) in enumerate(languages):
        cy = (i + offset) * (height / row)
        markers.append(f'<rect x="0" y="{cy - font / 2}" width="{font}" height="{font}" fill="{color}" '
                       f'{stroke_attr} stroke-width="1px">{anim(i)}</rect>')
        labels.append(f'<text dominant-baseline="middle" x="{font * 1.2}" y="{cy}" {fill_attr} '
                      f'font-size="{font}px">{escape(name)}{anim(i)}</text>')
        end = angle + 2 * math.pi * count / total
        paths.append(f'<path d="{arc_path(angle, end, radius - radius / 10, radius / 2)}" '
                     f'{stroke_attr} stroke-width="2px" style="fill: {color};">'
                     f'<title>{escape(name)} {count}</title>{anim(i)}</path>')
        angle = end
    return (f'<g transform="translate({x}, {y})">'
            f'<g transform="translate({radius * 2.1}, 0)">{"".join(markers)}{"".join(labels)}</g>'
            f'<g transform="translate({radius}, {radius})">{"".join(paths)}</g></g>')


def find_ring(svg):
    """(start, end, x, y) of the ring group: the translate(x, y) group holding the label markers."""
    for match in GROUP_START.finditer(svg):
        depth = 0
        for tag in re.finditer(r"<g[\s>]|</g>", svg[match.start():]):
            depth += -1 if tag.group(0) == "</g>" else 1
            if depth == 0:
                end = match.start() + tag.end()
                break
        body = svg[match.start():end]
        if 'dominant-baseline="middle"' in body and "<path" in body:
            return match.start(), end, match.group(1), match.group(2)
    return None


def main():
    token, user = os.environ.get("GH_TOKEN"), os.environ.get("GH_USER")
    if not token or not user:
        sys.exit("GH_TOKEN and GH_USER are required")
    excluded = {n.strip().lower() for n in os.environ.get("EXCLUDE_REPOS", "").split(",") if n.strip()}
    since = datetime.now(timezone.utc) - timedelta(days=365)
    languages = language_contributions(collect_commits(token, user, list_repos(token, excluded)), since)
    if not languages:
        sys.exit("No commits in the last year; leaving the 3D graphs as generated")

    patched = 0
    for path in sorted(Path(os.environ.get("SVG_DIR", "profile-3d-contrib")).glob("*.svg")):
        svg = path.read_text(encoding="utf-8")
        ring = find_ring(svg)
        if not ring:
            continue
        start, end, x, y = ring
        old = svg[start:end]
        if 'class="stroke-bg"' in old:  # 0.9+: colors come from the SVG's own CSS classes
            stroke_attr, fill_attr = 'class="stroke-bg"', 'class="fill-fg"'
        else:  # <= 0.8: literal colors on each element
            stroke_attr = f'stroke="{re.search(r"<rect[^>]*stroke=\"([^\"]+)\"", old).group(1)}"'
            fill_attr = f'fill="{re.search(r"<text[^>]*fill=\"([^\"]+)\"", old).group(1)}"'
        new = ring_group(x, y, languages, stroke_attr, fill_attr, "<animate" in old)
        path.write_text(svg[:start] + new + svg[end:], encoding="utf-8")
        patched += 1
    print(f"ring redrawn in {patched} SVGs: " + ", ".join(f"{n} {c}" for n, _, c in languages))


if __name__ == "__main__":
    main()

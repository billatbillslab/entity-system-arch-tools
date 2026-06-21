#!/usr/bin/env python3
"""style — identifier naming linter for the entity-core spec corpus.

Implements the mechanical half of STYLE-NAMING-CONVENTIONS.md: extract every
protocol identifier from the specs, classify it by axis and separator style,
and report every name that violates its axis's rule, with file:line.

The decided standard (v1) is a domain-invariant split:

    axis            expected separator   examples
    ------------    ------------------   ------------------------------
    type-path       kebab                system/capability/grant-entry
    field / key     snake                peer_id, content_hash
    value (quoted)  snake OR kebab        capability_denied / target-wins
                       (error codes are snake; enum values are kebab; the
                        linter accepts either and flags only mixed/camel)
    pseudo-fn       exempt               verify_request(...)  (illustrative)
    bare / other    report-only          heuristic, not gated

The per-axis policy is configurable (see AXIS_POLICY / --config) so the
standard can be retuned without editing logic.

Identifiers are extracted only from *code contexts* (fenced code blocks and
inline `code spans`); prose English is ignored. This analyzer keeps its own
specialized lexer (it tracks `~~~` fences and inline code spans the structural
model does not), per the toolkit design's "don't over-unify what differs".

Usage:
    spec style [--root DIR ...] [--all] [--json] [--config FILE]
               [--show-files] [--max-examples N]

No third-party dependencies. Python 3.11+.
(Was tools/spec-style/spec_style.py.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
import config as _config  # sibling in tools/spec/

# Corpus scope (root + excludes) from the single source of truth (config scope
# "naming-surface"): the full v7 normative surface — the naming standard governs
# every doc carrying normative type-paths, not just `specs/` (per the naming
# directive); intent (proposals) and exploratory/historical material are
# excluded there. The style guide itself is the one document that must contain
# counter-examples (its own test fixtures), so it sits in exclude_files; a direct
# `spec style STYLE-NAMING-CONVENTIONS.md` run still scans it (find_markdown).
_CFG = _config.load()
_SCOPE = _CFG.scope("naming-surface")
DEFAULT_ROOT = _SCOPE.root
EXCLUDE_DIRS = _SCOPE.exclude_dirs
EXCLUDE_FILES = _SCOPE.exclude_files


def find_markdown(roots: Iterable[Path], include_all: bool) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".md":
            out.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if not include_all:
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn.endswith(".md") and fn not in EXCLUDE_FILES:
                    out.append(Path(dirpath) / fn)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Separator styles & axes
# ---------------------------------------------------------------------------

SNAKE, KEBAB, MIXED, SINGLE, OTHER = "snake", "kebab", "mixed", "single", "other"

TYPE_PATH = "type-path"   # enforced kebab, but only for entity-type roots
OTHER_PATH = "other-path"  # slash-path that is NOT an entity type (source/config/url)
FIELD = "field"
VALUE = "value"
PSEUDO_FN = "pseudo-fn"
BARE = "bare"

# Per-axis policy: the set of separator styles that PASS. SINGLE always passes.
# None = the axis is not gated (report-only). Override via --config (JSON).
AXIS_POLICY: Dict[str, Optional[Set[str]]] = {
    TYPE_PATH: {KEBAB},      # namespace -> kebab. snake/mixed = real violation.
    VALUE: {SNAKE, KEBAB},   # error-code(snake) or enum(kebab); only mixed fails.
    # FIELD is report-only: the "token-followed-by-:" heuristic cannot yet
    # distinguish a data field (snake) from an operation / type / enum key
    # (all legitimately kebab) without block-context parsing (v0.3). Surfaced
    # as a review list, not gated, to keep the gate high-precision.
    FIELD: None,
    OTHER_PATH: None,
    PSEUDO_FN: None,
    BARE: None,
}

# Encoding / hash prefixes are not identifiers (STYLE-NAMING-CONVENTIONS §3.1).
EXEMPT_RE = re.compile(r"^(?:ecf[a-z0-9]*-)?(?:sha\d+|sha3|blake[0-9a-z]*)$", re.I)


def is_exempt(token: str) -> bool:
    return bool(EXEMPT_RE.match(token))

# Only slash-paths whose first segment is one of these are treated as entity
# types (and held to the kebab rule). Everything else is a source/config/url
# path -> report-only. From the single source of truth (config vocabulary.type_roots);
# still overridable via --config "type_roots".
TYPE_ROOTS: Set[str] = set(_CFG.type_roots)


# ---------------------------------------------------------------------------
# Region extraction: pull code contexts out of markdown
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^(\s*)(```+|~~~+)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


@dataclass
class Region:
    text: str
    line: int
    kind: str  # "fenced" | "inline"


def extract_regions(lines: List[str]) -> List[Region]:
    regions: List[Region] = []
    in_fence = False
    marker = ""
    for idx, raw in enumerate(lines, start=1):
        m = FENCE_RE.match(raw)
        if m:
            mk = m.group(2)[:3]
            if not in_fence:
                in_fence, marker = True, mk
            elif raw.strip().startswith(marker):
                in_fence = False
            continue
        if in_fence:
            regions.append(Region(raw, idx, "fenced"))
        else:
            for cm in INLINE_CODE_RE.finditer(raw):
                regions.append(Region(cm.group(1), idx, "inline"))
    return regions


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*[A-Za-z0-9]|[A-Za-z]")
TYPE_PATH_RE = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z0-9_-]+)+")
QUOTED_RE = re.compile(r"""["']([^"'\n]{1,80})["']""")


def separator_style(token: str) -> str:
    if any(c.isupper() for c in token):
        return OTHER
    u, h = "_" in token, "-" in token
    if u and h:
        return MIXED
    if u:
        return SNAKE
    if h:
        return KEBAB
    return SINGLE


def path_style(path: str) -> str:
    styles = [separator_style(s) for s in path.split("/")]
    if MIXED in styles:
        return MIXED
    if SNAKE in styles:
        return SNAKE
    if KEBAB in styles:
        return KEBAB
    return SINGLE


@dataclass
class Hit:
    token: str
    style: str
    axis: str
    fpath: str
    line: int


def classify_region(region: Region, fpath: str, type_roots: Set[str]) -> List[Hit]:
    hits: List[Hit] = []
    text = region.text
    consumed: List[Tuple[int, int]] = []

    # 1. Slash-paths -> type-path (if rooted in an entity-type namespace) or other-path.
    for m in TYPE_PATH_RE.finditer(text):
        path = m.group(0)
        if path.endswith("/") or "//" in path or "." in path:
            continue
        root = path.split("/", 1)[0]
        axis = TYPE_PATH if root in type_roots else OTHER_PATH
        hits.append(Hit(path, path_style(path), axis, fpath, region.line))
        consumed.append(m.span())

    # 2. Quoted strings -> key (if followed by ':') else value.
    for m in QUOTED_RE.finditer(text):
        inner = m.group(1).strip()
        if not inner or " " in inner or "/" in inner:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", inner):
            continue
        after = text[m.end() : m.end() + 1]
        axis = FIELD if after == ":" else VALUE
        hits.append(Hit(inner, separator_style(inner), axis, fpath, region.line))

    # 3. Bare identifiers (skip spans already consumed by a slash-path).
    for m in IDENT_RE.finditer(text):
        s, e = m.span()
        if any(cs <= s < ce for cs, ce in consumed):
            continue
        token = m.group(0)
        style = separator_style(token)
        if style in (SINGLE, OTHER):
            continue
        nxt = text[e : e + 1]
        nxt2 = text[e : e + 2]
        if nxt == "(":
            axis = PSEUDO_FN
        elif nxt == ":" and nxt2 != ":=":  # ':=' is a definition, not a field
            axis = FIELD
        else:
            axis = BARE
        hits.append(Hit(token, style, axis, fpath, region.line))

    return hits


# ---------------------------------------------------------------------------
# Aggregation & reporting
# ---------------------------------------------------------------------------

@dataclass
class Report:
    files_scanned: int = 0
    hits: List[Hit] = field(default_factory=list)
    policy: Dict[str, Optional[Set[str]]] = field(default_factory=lambda: dict(AXIS_POLICY))

    def is_violation(self, h: Hit) -> bool:
        allowed = self.policy.get(h.axis)
        if allowed is None:
            return False
        # SINGLE (one word) and OTHER (camel/Pascal/ALL_CAPS, e.g. placeholders,
        # example data, wire message constants) are never separator violations.
        if h.style in (SINGLE, OTHER):
            return False
        if is_exempt(h.token):
            return False
        return h.style not in allowed

    def violations(self) -> List[Hit]:
        return [h for h in self.hits if self.is_violation(h)]


def build_report(files: List[Path], policy, type_roots: Set[str]) -> Report:
    rep = Report(policy=policy)
    for fp in files:
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rep.files_scanned += 1
        for region in extract_regions(lines):
            rep.hits.extend(classify_region(region, str(fp), type_roots))
    return rep


def rel(p: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return p


def _distinct(hits: List[Hit]) -> Dict[str, Dict[str, object]]:
    d: Dict[str, Dict[str, object]] = {}
    for h in hits:
        e = d.setdefault(h.token, {"count": 0, "axes": set(), "styles": set(), "files": set()})
        e["count"] = int(e["count"]) + 1   # type: ignore
        e["axes"].add(h.axis)              # type: ignore
        e["styles"].add(h.style)           # type: ignore
        e["files"].add(rel(h.fpath))       # type: ignore
    return d


def print_text_report(rep: Report, max_examples: int, show_files: bool) -> None:
    viols = rep.violations()
    matrix: Dict[Tuple[str, str], int] = defaultdict(int)
    for h in rep.hits:
        matrix[(h.axis, h.style)] += 1

    print("=" * 74)
    print("spec-style audit — split standard (kebab namespace / snake keys)")
    print("=" * 74)
    print(f"files scanned: {rep.files_scanned}   identifier hits: {len(rep.hits)}")
    print()
    print("per-axis rule & result")
    print(f"  {'axis':<11} {'rule':<16} {'gated?':<7} {'violations':>10}")
    for axis in (TYPE_PATH, FIELD, VALUE, OTHER_PATH, PSEUDO_FN, BARE):
        allowed = rep.policy.get(axis)
        rule = "+".join(sorted(allowed)) if allowed else "report-only"
        gated = "yes" if allowed else "no"
        nv = sum(1 for h in viols if h.axis == axis)
        print(f"  {axis:<11} {rule:<16} {gated:<7} {nv:>10}")
    print()

    print("landscape  (axis x separator, all hits)")
    print(f"  {'axis':<11} {'snake':>7} {'kebab':>7} {'mixed':>7} {'single':>7} {'other':>7}")
    for axis in (TYPE_PATH, FIELD, VALUE, OTHER_PATH, PSEUDO_FN, BARE):
        row = [matrix[(axis, s)] for s in (SNAKE, KEBAB, MIXED, SINGLE, OTHER)]
        if any(row):
            print(f"  {axis:<11} {row[0]:>7} {row[1]:>7} {row[2]:>7} {row[3]:>7} {row[4]:>7}")
    print()

    distinct = _distinct(viols)
    print(f"ADJUDICATION LIST: {len(distinct)} distinct identifiers to fix "
          f"({len(viols)} occurrences)")
    print("(these are the real V8 rename targets under the split standard)")
    print()
    ranked = sorted(distinct.items(), key=lambda kv: -int(kv[1]["count"]))  # type: ignore
    shown = ranked if max_examples <= 0 else ranked[:max_examples]
    for token, meta in shown:
        axes = ",".join(sorted(meta["axes"]))     # type: ignore
        styles = ",".join(sorted(meta["styles"]))  # type: ignore
        nfiles = len(meta["files"])               # type: ignore
        print(f"  {meta['count']:>4}x  {token:<34} [{axes}/{styles}]  {nfiles} file(s)")
        if show_files:
            for f in sorted(meta["files"]):       # type: ignore
                print(f"          {f}")
    if max_examples > 0 and len(ranked) > max_examples:
        print(f"  ... and {len(ranked) - max_examples} more (use --max-examples 0)")
    print()

    per_file: Dict[str, int] = defaultdict(int)
    for h in viols:
        per_file[rel(h.fpath)] += 1
    if per_file:
        print("violations per file")
        for f, n in sorted(per_file.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {f}")
    print()
    print("=" * 74)
    print(f"RESULT: {len(viols)} violations / {len(distinct)} identifiers")
    print("=" * 74)


def json_report(rep: Report) -> dict:
    viols = rep.violations()
    distinct = _distinct(viols)
    return {
        "files_scanned": rep.files_scanned,
        "total_hits": len(rep.hits),
        "violations": len(viols),
        "distinct_violations": len(distinct),
        "policy": {k: (sorted(v) if v else None) for k, v in rep.policy.items()},
        "identifiers": {
            t: {"count": int(m["count"]),                  # type: ignore
                "axes": sorted(m["axes"]),                  # type: ignore
                "styles": sorted(m["styles"]),              # type: ignore
                "files": sorted(m["files"])}                # type: ignore
            for t, m in sorted(distinct.items(), key=lambda kv: -int(kv[1]["count"]))  # type: ignore
        },
        "locations": [
            {"token": h.token, "axis": h.axis, "style": h.style,
             "file": rel(h.fpath), "line": h.line}
            for h in viols
        ],
    }


def load_config(path: Path) -> Tuple[Dict[str, Optional[Set[str]]], Set[str]]:
    """Merge a JSON config over the defaults.

    {
      "axis_policy": {"value": ["snake", "kebab"], "bare": null},
      "type_roots": ["system", "primitive", "entity", "local"]
    }
    """
    policy = dict(AXIS_POLICY)
    roots = set(TYPE_ROOTS)
    data = json.loads(path.read_text(encoding="utf-8"))
    for axis, allowed in (data.get("axis_policy") or {}).items():
        policy[axis] = set(allowed) if allowed else None
    if data.get("type_roots"):
        roots = set(data["type_roots"])
    return policy, roots


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", type=Path,
                    help="dir or file to scan (repeatable; default: v7 core specs)")
    ap.add_argument("--all", action="store_true",
                    help="include deprecated/archived/test-vectors/reviews dirs")
    ap.add_argument("--config", type=Path, help="JSON config overriding axis policy / type roots")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--show-files", action="store_true",
                    help="list files for each non-conforming identifier")
    ap.add_argument("--max-examples", type=int, default=80,
                    help="cap the adjudication list (0 = all)")
    args = ap.parse_args(argv)

    policy, type_roots = dict(AXIS_POLICY), set(TYPE_ROOTS)
    if args.config:
        policy, type_roots = load_config(args.config)

    roots = args.root or [DEFAULT_ROOT]
    files = find_markdown(roots, args.all)
    if not files:
        print(f"no markdown found under: {', '.join(map(str, roots))}", file=sys.stderr)
        return 2

    rep = build_report(files, policy, type_roots)
    if args.json:
        print(json.dumps(json_report(rep), indent=2))
    else:
        print_text_report(rep, args.max_examples, args.show_files)
    return 1 if rep.violations() else 0


if __name__ == "__main__":
    raise SystemExit(main())

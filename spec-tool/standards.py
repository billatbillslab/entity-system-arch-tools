#!/usr/bin/env python3
"""standards — release-readiness checker + refiner for the entity-core spec corpus.

Mechanical enforcement of `SPECIFICATION-FORMAT.md`: it checks each normative
spec for a release-grade shape (clean header, declared status/depends, no
process narrative fossilized into normative text) and reports every deviation
with file:line.

Two modes:

    spec standards [--root DIR|FILE ...]            # check (default): flag deviations
    spec standards --refine --root FILE --output D  # refine: write a cleaned copy to D

Refine applies only the *safe* mechanical fixes (collapse a changelog-blob
Version header to a bare version; drop a trailing Document History section).
It never edits normative prose — inline dates / impl-team refs / proposal
citations are reported as remaining editorial work, not auto-stripped, because
they may sit next to load-bearing text. The source file is never modified.

Boundary: the publish-time scrub (secrets, dates, license furniture) is
`entity-core-devops/release-builder`'s job. This analyzer is spec-standards
conformance only — the quality bar, paralleling `spec style` for naming.

Stdlib-only Python 3.11+. Exit code is non-zero when gating (error) violations
exist, so it composes as a contributor/CI gate.
(Was tools/spec-standards/spec_standards.py.)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
import config as _config  # sibling in tools/spec/

# Corpus knowledge — root, excludes, accepted Status values — now comes from the
# single source of truth (tools/spec/config.default.toml, scope "core-specs-strict").
# The format spec quotes counter-examples; never lint the rulebooks themselves —
# that exclusion now lives in config exclude_files.
_CFG = _config.load()
_SCOPE = _CFG.scope("core-specs-strict")
DEFAULT_ROOT = _SCOPE.root
EXCLUDE_DIRS = _SCOPE.exclude_dirs
EXCLUDE_FILES = _SCOPE.exclude_files

CANONICAL_STATUS = _CFG.canonical_status

# ---- rule catalog -----------------------------------------------------------
# severity: "error" gates (non-zero exit); "warn" reports (editorial candidates).
RULES = {
    "header-version-missing":   ("error", "no `**Version**:` header field"),
    "header-version-blob":      ("error", "Version field carries a changelog blob, not a bare version"),
    "header-status-missing":    ("error", "no `**Status**:` header field"),
    "header-status-unknown":    ("warn",  "Status value not one of Draft/Active/Superseded/Normative"),
    "title-not-h1":             ("error", "first content line is not an H1 title"),
    "depends-missing":          ("error", "extension spec without a `**Depends**:` declaration (§8.1)"),
    "document-history-section": ("warn",  "Document History section (process history; belongs in the changelog)"),
    "date-in-body":             ("warn",  "calendar date in body (internal-lab timing; not normative)"),
    "impl-team-ref":            ("warn",  "implementation/team reference (internal process; not normative)"),
    "proposal-citation":        ("warn",  "proposal-filename citation (internal routing; not normative)"),
    "amendment-provenance":     ("warn",  "amendment-provenance note (how-we-got-here; not normative)"),
}

IMPL_TEAM_RE = re.compile(
    r"\b(cohort|keystone|workbench|egui|godot|raylib|entity-core-(?:go|rust|python|py)|wb-go)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
PROPOSAL_RE = re.compile(r"\bPROPOSAL-[A-Z0-9]|\bproposals/")
AMENDMENT_RE = re.compile(r"\bAmendment\s+\d", re.IGNORECASE)
VERSION_TOKEN_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\b")


class Finding:
    __slots__ = ("rule", "line", "text")

    def __init__(self, rule: str, line: int, text: str):
        self.rule = rule
        self.line = line
        self.text = text

    def severity(self) -> str:
        return RULES[self.rule][0]


def iter_specs(root: Path):
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name in EXCLUDE_FILES:
            continue
        yield p


def find_header_field(lines: List[str], name: str) -> Optional[Tuple[int, str]]:
    """Find a `**Name**: value` line in the header region (before first `## `)."""
    pat = re.compile(r"^\*\*%s\*\*\s*:\s*(.*)$" % re.escape(name))
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            break
        m = pat.match(ln)
        if m:
            return i, m.group(1).strip()
    return None


def first_content_line(lines: List[str]) -> Optional[Tuple[int, str]]:
    for i, ln in enumerate(lines):
        if ln.strip():
            return i, ln
    return None


def doc_history_span(lines: List[str]) -> Optional[Tuple[int, int]]:
    """Return [start, end) line indices of a Document History section, or None."""
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+document\s+history\b", ln, re.IGNORECASE):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return start, end


def analyze(path: Path, text: str) -> List[Finding]:
    lines = text.splitlines()
    findings: List[Finding] = []

    # --- title ---
    fc = first_content_line(lines)
    if not fc or not fc[1].startswith("# "):
        findings.append(Finding("title-not-h1", (fc[0] + 1) if fc else 1,
                                fc[1].strip() if fc else "(empty file)"))

    # --- version ---
    ver = find_header_field(lines, "Version")
    if ver is None:
        findings.append(Finding("header-version-missing", 1, ""))
    else:
        idx, val = ver
        m = VERSION_TOKEN_RE.match(val)
        remainder = val[m.end():].strip() if m else val
        if not m or len(remainder) > 60:
            findings.append(Finding("header-version-blob", idx + 1, val[:90]))

    # --- status ---
    st = find_header_field(lines, "Status")
    if st is None:
        findings.append(Finding("header-status-missing", 1, ""))
    else:
        idx, val = st
        primary = re.split(r"[ \|/]", val.strip())[0]
        if primary and primary not in CANONICAL_STATUS:
            findings.append(Finding("header-status-unknown", idx + 1, val[:60]))

    # --- depends (extension specs only) ---
    if path.name.startswith("EXTENSION-"):
        if find_header_field(lines, "Depends") is None:
            findings.append(Finding("depends-missing", 1, ""))

    # --- document history ---
    span = doc_history_span(lines)
    if span:
        findings.append(Finding("document-history-section", span[0] + 1,
                                lines[span[0]].strip()))

    # --- body cruft (skip fenced code blocks) ---
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        ln1 = i + 1
        if DATE_RE.search(ln):
            findings.append(Finding("date-in-body", ln1, ln.strip()[:90]))
        if IMPL_TEAM_RE.search(ln):
            findings.append(Finding("impl-team-ref", ln1, ln.strip()[:90]))
        if PROPOSAL_RE.search(ln):
            findings.append(Finding("proposal-citation", ln1, ln.strip()[:90]))
        if AMENDMENT_RE.search(ln):
            findings.append(Finding("amendment-provenance", ln1, ln.strip()[:90]))

    return findings


# ---- refine -----------------------------------------------------------------
def refine_text(text: str) -> Tuple[str, List[str]]:
    """Apply safe mechanical fixes. Returns (new_text, list_of_fixes_applied)."""
    lines = text.splitlines()
    fixes: List[str] = []

    # F1: collapse the header changelog run — keep the first **Version**
    # (as a bare version), drop every later **Version**/**Prior** entry. These
    # are pure provenance and belong in the changelog, not the spec header.
    header_end = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    kept: List[str] = []
    seen_version = False
    dropped = 0
    for i in range(header_end):
        ln = lines[i]
        is_version = ln.startswith("**Version**")
        is_prior = ln.startswith("**Prior**")
        if is_version and not seen_version:
            seen_version = True
            m = VERSION_TOKEN_RE.match(ln.split(":", 1)[1] if ":" in ln else ln)
            kept.append("**Version**: %s" % m.group(1) if m else ln)
            continue
        if is_version or is_prior:
            dropped += 1
            continue
        kept.append(ln)
    # squeeze runs of blank lines left behind in the header
    squeezed: List[str] = []
    for ln in kept:
        if ln.strip() == "" and squeezed and squeezed[-1].strip() == "":
            continue
        squeezed.append(ln)
    if dropped:
        lines = squeezed + lines[header_end:]
        fixes.append("collapsed header changelog (dropped %d Version/Prior entries)" % dropped)

    # F2: drop a Document History section.
    span = doc_history_span(lines)
    if span:
        start, end = span
        # also drop a trailing `---` separator immediately above the section
        drop_from = start
        k = start - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and lines[k].strip() == "---":
            drop_from = k
        del lines[drop_from:end]
        fixes.append("removed Document History section (%d lines)" % (end - drop_from))

    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"
    return new_text, fixes


# ---- reporting --------------------------------------------------------------
def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def run_check(roots: List[Path], as_json: bool, max_examples: int) -> int:
    report: Dict[str, List[Finding]] = {}
    for root in roots:
        for spec in iter_specs(root):
            try:
                text = spec.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                report[rel(spec)] = [Finding("title-not-h1", 1, "unreadable: %s" % exc)]
                continue
            f = analyze(spec, text)
            if f:
                report[rel(spec)] = f

    n_error = sum(1 for fs in report.values() for x in fs if x.severity() == "error")
    n_warn = sum(1 for fs in report.values() for x in fs if x.severity() == "warn")

    if as_json:
        out = {
            rp: [{"rule": x.rule, "severity": x.severity(), "line": x.line, "text": x.text}
                 for x in fs]
            for rp, fs in report.items()
        }
        print(json.dumps({"summary": {"errors": n_error, "warnings": n_warn,
                                      "files": len(report)}, "findings": out}, indent=2))
        return 1 if n_error else 0

    for rp in sorted(report):
        fs = report[rp]
        errs = [x for x in fs if x.severity() == "error"]
        warns = [x for x in fs if x.severity() == "warn"]
        print("\n%s  (%d error, %d warn)" % (rp, len(errs), len(warns)))
        for x in errs:
            print("  ERROR  %-26s %s:%d  %s" % (x.rule, rp, x.line, x.text))
        # collapse repetitive warns (dates/impl-refs) to a count + a few examples
        by_rule: Dict[str, List[Finding]] = {}
        for x in warns:
            by_rule.setdefault(x.rule, []).append(x)
        for rule, items in sorted(by_rule.items()):
            print("  warn   %-26s x%d" % (rule, len(items)))
            show = items if max_examples == 0 else items[:max_examples]
            for x in show:
                print("           %s:%d  %s" % (rp, x.line, x.text))
            if max_examples and len(items) > max_examples:
                print("           ... +%d more" % (len(items) - max_examples))

    print("\n%d file(s) flagged — %d error(s), %d warning(s)." % (len(report), n_error, n_warn))
    print("errors gate; warnings are editorial candidates (run --refine for the safe subset).")
    return 1 if n_error else 0


def run_refine(root: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    specs = list(iter_specs(root))
    if not specs:
        print("no specs found at %s" % root, file=sys.stderr)
        return 2
    base = root if root.is_dir() else root.parent
    for spec in specs:
        text = spec.read_text(encoding="utf-8")
        new_text, fixes = refine_text(text)
        try:
            dest = output / spec.relative_to(base)
        except ValueError:
            dest = output / spec.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_text, encoding="utf-8")
        remaining = [x for x in analyze(spec, new_text)]
        print("\n%s -> %s" % (rel(spec), rel(dest)))
        if fixes:
            for fx in fixes:
                print("  fixed: %s" % fx)
        else:
            print("  fixed: (nothing auto-fixable)")
        rem_warn = [x for x in remaining if x.severity() == "warn"]
        rem_err = [x for x in remaining if x.severity() == "error"]
        if rem_err:
            print("  STILL ERROR: %s" % ", ".join("%s@%d" % (x.rule, x.line) for x in rem_err))
        if rem_warn:
            by = {}
            for x in rem_warn:
                by[x.rule] = by.get(x.rule, 0) + 1
            print("  manual editorial remaining: %s"
                  % ", ".join("%s x%d" % (r, c) for r, c in sorted(by.items())))
    print("\nrefined %d spec(s) into %s (source untouched)." % (len(specs), rel(output)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", type=Path,
                    help="spec dir or single .md file (repeatable; default: core-protocol-domain/specs)")
    ap.add_argument("--refine", action="store_true",
                    help="write cleaned copies (safe fixes only) instead of checking")
    ap.add_argument("--output", type=Path, help="output dir for --refine")
    ap.add_argument("--json", action="store_true", help="emit JSON (check mode)")
    ap.add_argument("--max-examples", type=int, default=8,
                    help="max example lines per repetitive warn rule (0 = all)")
    args = ap.parse_args(argv)

    roots = args.root or [DEFAULT_ROOT]

    if args.refine:
        if not args.output:
            ap.error("--refine requires --output DIR")
        if len(roots) != 1:
            ap.error("--refine takes a single --root (dir or file)")
        return run_refine(roots[0], args.output)

    return run_check(roots, args.json, args.max_examples)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""address — the §11 addressing-conformance validator + worklist emitter.

`SPECIFICATION-FORMAT.md` §11 is the addressing contract: a fully-qualified
address is `DOC.md §N.M` (host + path); a bare `§N.M` is implicitly hosted by
the current document; a prose name ("V7", "core protocol") is NOT a citation and
SHOULD be given as the canonical `DOC.md`. This analyzer reports every deviation
from §11 and emits self-contained *finding packets* the downstream LLM passes
adjudicate (see DESIGN-ADDRESSING-CLEANUP-PIPELINE).

It extends what `topology` does (tokenized external citations) to the full
surface: prose nicknames, bare-§ internal validation, and the §11.2 exemption of
filesystem-artifact mentions (code blocks, `Depends:` fields, paths).

Finding classes:
  * drift          — an external citation lacking `.md` (bare `DOC`); §11.2 wants
                     the `.md` host form. Mechanical: append `.md`.
  * nickname       — a prose nickname governing a `§` (`V7 §5.2`). Mechanical
                     when it maps to a known doc whose section exists; judgment
                     for self/ambiguous nicknames.
  * bare-internal  — a bare `§N.M` that does not resolve to a host-doc section,
                     OR a `§` with a *detached* doc mention on the line (the
                     "based on V7 ... §5.2" trap). Always judgment.
  * dangling       — a citation whose target is not a file in the corpus.
                     Judgment: Forward / Stale / Leak (§11.4).
  * stale-section  — `DOC.md §N.M` whose doc resolves but whose section does not.
                     Judgment: redirect to an existing section.

Tier: `mechanical` (tool resolves it; LLM only spot-reviews) vs `judgment` (a
cheap batched LLM call). Findings are emitted only for *deviations* — a
conformant citation is not a finding.

    spec address [ROOT]                 # text summary of deviations by class
    spec address [ROOT] --json          # findings as JSON
    spec address [ROOT] --worklist OUT  # self-contained packets as JSONL
    spec address [ROOT] --batch-by U    # shard worklist: document|section|class|count:N
    spec address [ROOT] --gate          # exit non-zero if any deviation (the G1 gate)

ROOT defaults to the v7.0 core-protocol-domain specs tree. The analyzer is
read-only — it never edits a spec. Stdlib-only Python 3.11+.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import model as spec_model  # sibling — shared parser (iter_fences)
import config as _config

_CFG = _config.load()
REPO_ROOT = _config.REPO_ROOT

SEC_RE = re.compile(r"§(\d+(?:\.\d+)*[a-z]?)")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HEADNUM_RE = re.compile(r"^(\d+(?:\.\d+)*[a-z]?)\.?\s+(.*)$")
# A doc token: ALL-CAPS with at least one hyphen group, optional `.md`.
DOCTOKEN_RE = re.compile(r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)(\.md)?")
WORD_RE = re.compile(r"[A-Za-z]{2,}")
# words that may appear between a doc mention and its § without breaking the link
CONNECTOR_WORDS = {"md", "and", "or"}


class Finding:
    __slots__ = ("cls", "tier", "file", "line", "col", "reference", "target",
                 "sec", "edit_span", "edit_new", "note", "detached", "target_class")

    def __init__(self, cls, tier, file, line, col, reference, target, sec,
                 edit_span, edit_new, note="", detached=None, target_class=""):
        self.cls = cls
        self.tier = tier
        self.file = file
        self.line = line
        self.col = col
        self.reference = reference
        self.target = target          # resolved DOC stem, or None
        self.sec = sec                # section number cited, or None
        self.edit_span = edit_span    # [start, end] on the line, or None
        self.edit_new = edit_new      # mechanical replacement text, or None
        self.note = note
        self.detached = detached      # detached doc stem (bare-internal trap)
        self.target_class = target_class  # canonical-spec|guide|arch-doc|intent|absent


# ---- corpus model -----------------------------------------------------------
class Doc:
    def __init__(self, stem: str, path: Path):
        self.stem = stem
        self.path = path
        self.lines: List[str] = []
        self.headings: List[Tuple[int, str, str]] = []   # (line, number, title)
        self.sections: Set[str] = set()                  # numbered headings
        self.fence_lines: Set[int] = set()               # lines inside code fences


def build_doc(stem: str, path: Path, text: Optional[str] = None) -> Doc:
    """Parse one doc into a Doc (headings, numbered-section set, fenced-code
    lines). Shared by the corpus loader and the self-test."""
    d = Doc(stem, path)
    if text is None:
        text = path.read_text(encoding="utf-8")
    d.lines = text.splitlines()
    for i, ln in enumerate(d.lines):
        hm = HEAD_RE.match(ln)
        if hm:
            # Two heading styles in the corpus: `#### 3.1.1 Title` and `## §3.1.1
            # Title`. Strip a leading `§` so the §-style numbers are captured too
            # (else valid citations to those docs read as stale-section).
            htext = hm.group(2).strip().lstrip("§").lstrip()
            nm = HEADNUM_RE.match(htext)
            num = nm.group(1) if nm else ""
            title = nm.group(2) if nm else hm.group(2).strip()
            d.headings.append((i + 1, num, title))
            if num:
                d.sections.add(num)
    for _open_ln, _info, body, body_start in spec_model.iter_fences(d.lines):
        for k in range(body_start, body_start + len(body)):
            d.fence_lines.add(k)
    return d


def load_corpus(root: Path, excludes: Set[str]) -> Dict[str, Doc]:
    docs: Dict[str, Doc] = {}
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).parts
        if any(seg in excludes for seg in rel[:-1]):
            continue
        if p.stem in docs:
            continue
        docs[p.stem] = build_doc(p.stem, p)
    return docs


def enclosing_heading(doc: Doc, lineno: int) -> Optional[Tuple[int, str, str]]:
    found = None
    for h in doc.headings:
        if h[0] <= lineno:
            found = h
        else:
            break
    return found


def is_normative_body(doc: Doc, lineno: int) -> bool:
    """False for preamble (before first heading), unnumbered sections, and a
    Document History section — process material, not normative cross-refs."""
    enc = enclosing_heading(doc, lineno)
    if enc is None:
        return False
    _, num, title = enc
    if not num:
        return False
    if re.search(r"document\s+history", title, re.IGNORECASE):
        return False
    return True


# ---- per-line reference scan ------------------------------------------------
def _is_doc_token(tok: str, stems: Set[str], prefixes: Set[str],
                  families: Set[str]) -> bool:
    return (tok in stems or tok.split("-")[0] in prefixes
            or tok.split("-")[0] in families)


def scan_line(line: str, nick_re, nick_map, stems, prefixes, families):
    """Yield mention/secref events left-to-right with positions.

    Returns (events, mentions) where each event is a dict; mentions are doc/nick
    tokens, secrefs are `§` refs. The caller links each secref to its governing
    antecedent (immediate or chained) vs. detached/bare.
    """
    events = []
    doc_spans = []
    for m in DOCTOKEN_RE.finditer(line):
        tok = m.group(1)
        if _is_doc_token(tok, stems, prefixes, families):
            events.append({"k": "doc", "s": m.start(1), "e": m.end(),
                           "tok": tok, "has_md": bool(m.group(2))})
            doc_spans.append((m.start(1), m.end()))
    if nick_re is not None:
        for m in nick_re.finditer(line):
            # A nickname nested inside a doc-token span (e.g. the "V7" in
            # `ENTITY-CORE-PROTOCOL.md`) is part of a filename, not prose;
            # the citation is already canonical. Skip it.
            if any(s <= m.start() and m.end() <= e for s, e in doc_spans):
                continue
            events.append({"k": "nick", "s": m.start(), "e": m.end(),
                           "tok": m.group(0), "target": nick_map[m.group(0).lower()]})
    for m in SEC_RE.finditer(line):
        events.append({"k": "sec", "s": m.start(), "e": m.end(), "sec": m.group(1)})
    events.sort(key=lambda x: x["s"])
    return events


def _gap_is_connector(line: str, lo: int, hi: int) -> bool:
    gap = line[lo:hi]
    gap = SEC_RE.sub("", gap)                       # chained §refs don't break the link
    words = [w for w in WORD_RE.findall(gap) if w.lower() not in CONNECTOR_WORDS]
    return len(words) == 0


def analyze_doc(doc: Doc, docs: Dict[str, Doc], env: dict) -> List[Finding]:
    out: List[Finding] = []
    rel = str(doc.path.relative_to(REPO_ROOT)) if doc.path.is_relative_to(REPO_ROOT) else str(doc.path)
    for idx, line in enumerate(doc.lines):
        lineno = idx + 1
        if lineno in doc.fence_lines:
            continue
        if line.lstrip().startswith("**Depends**"):
            continue
        events = scan_line(line, env["nick_re"], env["nick_map"],
                           env["stems"], env["prefixes"], env["families"])
        if not any(e["k"] == "sec" for e in events):
            continue
        if not is_normative_body(doc, lineno):
            continue

        ante = None   # last doc/nick event
        for ev in events:
            if ev["k"] in ("doc", "nick"):
                ante = ev
                continue
            # secref
            sec = ev["sec"]
            governed = ante is not None and _gap_is_connector(line, ante["e"], ev["s"])

            if governed and ante["k"] == "doc":
                out.extend(_external_token(doc, rel, lineno, line, ante, sec, docs, env))
            elif governed and ante["k"] == "nick":
                out.extend(_external_nick(doc, rel, lineno, line, ante, sec, docs, env))
            else:
                # bare or detached
                if ante is not None and ante["k"] == "doc":
                    det = ante["tok"]
                elif ante is not None and ante["k"] == "nick":
                    det = ante["target"] or None
                else:
                    det = None
                out.extend(_bare_internal(doc, rel, lineno, line, ev, sec, det, docs))
    return out


def _path_like(line: str, ev) -> bool:
    """A token used as a filesystem artifact (§11.2 exemption): adjacent `/`."""
    s = ev["s"]
    before = line[max(0, s - 1):s]
    after = line[ev["e"]:ev["e"] + 1]
    return before == "/" or after == "/"


def _dispose_external(tgt, sec, source_class, env):
    """Target not in the analysis scope. Resolve against the wider namespace and
    apply §11.3 disposition. Returns (cls, note, target_class) or None when the
    citation is permitted (an arch/guide doc, or a normative spec citing another
    spec/guide/arch outside the analysis scope) and therefore not a finding."""
    cfg = env["cfg"]
    if tgt in env["namespace"]:
        tclass = cfg.doc_class(tgt)
        if source_class != "canonical-spec":
            return None  # arch/guide doc may cite anything real — informational
        if tclass == "intent":
            return ("leak", "normative spec cites a process artifact (§11.3)", tclass)
        return None      # normative -> spec/guide/arch outside analysis: permitted
    fam = tgt.split("-")[0]
    if fam in cfg.intent_families:
        return ("dangling", "absent intent-artifact name (likely leak)", "absent")
    return ("dangling", "target absent from corpus (forward/stale/leak)", "absent")


def _is_intent(tgt, env):
    """True if doc stem `tgt` names a process/intent artifact (§11.3) —
    proposal / exploration / review / feedback / ruling / audit."""
    cfg = env["cfg"]
    if tgt in env["namespace"] and cfg.doc_class(tgt) == "intent":
        return True
    return tgt.split("-")[0] in cfg.intent_families


_FORWARD_RE = re.compile(r"\((?:planned|forthcoming)\b")


def _forward_marked(line: str, after_pos: int) -> bool:
    """§11.4 Forward: a citation immediately followed by a `(planned)` /
    `(forthcoming)` marker is an author-declared forward reference to a spec
    that has not landed yet — permitted, not a dangling/leak finding. The marker
    is the conformance signal (a forthcoming sibling extension, typically backed
    by a `PROPOSAL-*`)."""
    return _FORWARD_RE.search(line[after_pos:after_pos + 48]) is not None


def _external_token(doc, rel, lineno, line, ante, sec, docs, env):
    tgt = ante["tok"]
    if _path_like(line, ante) and not _is_intent(tgt, env):
        return []  # genuine filesystem artifact (§11.2). EXCEPTION: a dir-prefixed
        # process-doc citation (`explorations/EXPLORATION-…`, `reviews/RULING-…`) is
        # NOT exempt — it is a §11.3 leak the `/` prefix would otherwise swallow.
    if tgt == doc.stem:
        return []  # self-cited-by-name is rare; leave for a later pass
    if tgt not in docs:
        disp = _dispose_external(tgt, sec, env["source_class"], env)
        if disp is None:
            return []
        cls, note, tclass = disp
        if cls == "dangling" and not _is_intent(tgt, env) \
                and _forward_marked(line, ante["e"]):
            return []  # §11.4 Forward (planned) — spec-shaped name, author-marked
        ref = "%s §%s" % (tgt, sec) if sec else tgt
        return [Finding(cls, "judgment", rel, lineno, ante["s"], ref, tgt, sec,
                        None, None, note=note, target_class=tclass)]
    if sec and sec not in docs[tgt].sections:
        return [Finding("stale-section", "judgment", rel, lineno, ante["s"],
                        "%s §%s" % (tgt, sec), tgt, sec, None, None,
                        note="§%s absent in %s" % (sec, tgt))]
    if not ante["has_md"]:
        return [Finding("drift", "mechanical", rel, lineno, ante["s"],
                        ante["tok"], tgt, sec, [ante["s"], ante["e"]],
                        ante["tok"] + ".md", note="bare DOC -> DOC.md (§11.2)")]
    return []  # conformant DOC.md §N.M


def _external_nick(doc, rel, lineno, line, ante, sec, docs, env):
    tgt = ante["target"]
    if not tgt:
        return [Finding("nickname", "judgment", rel, lineno, ante["s"],
                        "%s §%s" % (ante["tok"], sec), None, sec,
                        [ante["s"], ante["e"]], None,
                        note="self/ambiguous nickname — self-ref or upstream doc?")]
    if tgt not in docs:
        disp = _dispose_external(tgt, sec, env["source_class"], env)
        if disp is None:
            return []
        cls, note, tclass = disp
        return [Finding(cls, "judgment", rel, lineno, ante["s"],
                        "%s §%s" % (ante["tok"], sec), tgt, sec, None, None,
                        note=note, target_class=tclass)]
    if sec and sec not in docs[tgt].sections:
        return [Finding("stale-section", "judgment", rel, lineno, ante["s"],
                        "%s §%s" % (ante["tok"], sec), tgt, sec, None, None,
                        note="nickname -> %s.md but §%s absent" % (tgt, sec))]
    return [Finding("nickname", "mechanical", rel, lineno, ante["s"],
                    "%s §%s" % (ante["tok"], sec), tgt, sec,
                    [ante["s"], ante["e"]], tgt + ".md",
                    note="prose nickname -> canonical DOC.md (§11.2)")]


def _bare_internal(doc, rel, lineno, line, ev, sec, detached, docs):
    base = sec.split(".")[0] if sec else sec
    resolves = sec in doc.sections
    if detached:
        return [Finding("bare-internal", "judgment", rel, lineno, ev["s"],
                        "§%s" % sec, None, sec, None, None,
                        note="detached doc mention on line — self-ref or %s?" % detached,
                        detached=detached)]
    if resolves:
        return []  # conformant same-document section ref
    return [Finding("bare-internal", "judgment", rel, lineno, ev["s"],
                    "§%s" % sec, None, sec, None, None,
                    note="§%s not a section of host doc — broken self-ref or external?" % sec)]


# ---- packets ----------------------------------------------------------------
def box(lines: List[str], lineno: int, radius: int = 3) -> str:
    lo = max(0, lineno - 1 - radius)
    hi = min(len(lines), lineno + radius)
    return "\n".join(lines[lo:hi])


def packet(f: Finding, docs: Dict[str, Doc]) -> dict:
    host = Path(f.file).stem
    host_doc = docs.get(host)
    candidates = {}
    if f.target and f.target in docs:
        candidates[f.target + ".md"] = sorted(docs[f.target].sections)
    if f.detached and f.detached in docs:
        candidates[f.detached + ".md"] = sorted(docs[f.detached].sections)
    if f.cls == "bare-internal" and host_doc:
        candidates[host + ".md"] = sorted(host_doc.sections)
    mech = None
    if f.tier == "mechanical" and f.edit_span:
        mech = {"span": f.edit_span, "new": f.edit_new}
    return {
        "id": None,  # assigned at emit
        "file": f.file, "line": f.line, "col": f.col,
        "class": f.cls, "tier": f.tier,
        "reference": f.reference, "target": f.target, "sec": f.sec,
        "target_class": f.target_class, "note": f.note,
        "context": box(host_doc.lines, f.line) if host_doc else "",
        "host_sections": sorted(host_doc.sections) if host_doc else [],
        "candidates": candidates,
        "mechanical_verdict": mech,
    }


def batch_key(f: Finding, mode: str) -> str:
    if mode == "class":
        return f.cls
    if mode == "section":
        return "%s#%s" % (f.file, f.sec or "?")
    return f.file  # document (default)


# ---- run --------------------------------------------------------------------
def collect(root: Path, excludes: Set[str]) -> Tuple[List[Finding], Dict[str, Doc]]:
    docs = load_corpus(root, excludes)
    stems = set(docs)
    prefixes = {s.split("-")[0] for s in stems}
    families = _CFG.doc_families
    nick_map = {k.lower(): v for k, v in _CFG.nicknames.items()}
    nick_re = None
    if nick_map:
        keys = sorted(nick_map, key=len, reverse=True)
        nick_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b",
                             re.IGNORECASE)
    namespace = {p.stem for p in _CFG.scope("addressing-namespace").find_markdown()}
    env = {
        "nick_re": nick_re, "nick_map": nick_map, "stems": stems,
        "prefixes": prefixes, "families": families,
        "namespace": namespace, "cfg": _CFG, "source_class": "canonical-spec",
    }
    findings: List[Finding] = []
    for doc in docs.values():
        env["source_class"] = _CFG.doc_class(doc.stem)
        findings.extend(analyze_doc(doc, docs, env))
    return findings, docs


def render_text(findings: List[Finding]) -> str:
    by_cls: Dict[str, List[Finding]] = {}
    by_tier = {"mechanical": 0, "judgment": 0}
    for f in findings:
        by_cls.setdefault(f.cls, []).append(f)
        by_tier[f.tier] += 1
    out = ["spec address — §11 conformance: %d deviation(s)  "
           "(mechanical %d, judgment %d)"
           % (len(findings), by_tier["mechanical"], by_tier["judgment"]), ""]
    order = ["drift", "nickname", "bare-internal", "leak", "stale-section", "dangling"]
    for cls in order:
        items = by_cls.get(cls, [])
        if not items:
            continue
        mech = sum(1 for x in items if x.tier == "mechanical")
        out.append("%-14s %4d   (mechanical %d, judgment %d)"
                   % (cls, len(items), mech, len(items) - mech))
        for f in items[:6]:
            out.append("    %s:%d  %s  %s" % (f.file.split("/")[-1], f.line,
                                              f.reference, "→ %s.md" % f.target
                                              if f.target else f.note[:40]))
        if len(items) > 6:
            out.append("    ... +%d more" % (len(items) - 6))
        out.append("")
    out.append("(deviations only; a conformant citation is not a finding. "
               "--worklist emits packets; --gate exits non-zero if any.)")
    return "\n".join(out)


def emit_worklist(findings, docs, out_path: Path, batch_by: Optional[str]) -> None:
    with open(out_path, "w", encoding="utf-8") as fh:
        for i, f in enumerate(sorted(findings, key=lambda x: (x.file, x.line, x.col))):
            pkt = packet(f, docs)
            pkt["id"] = "F%04d" % (i + 1)
            if batch_by:
                pkt["batch"] = batch_key(f, batch_by)
            fh.write(json.dumps(pkt) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, nargs="?",
                    default=_CFG.scope("core-specs").root,
                    help="corpus root dir (default: v7.0 core specs)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--worklist", type=Path, help="emit finding packets as JSONL to this path")
    ap.add_argument("--batch-by", default=None,
                    help="document|section|class|count:N (tags packets for sharding)")
    ap.add_argument("--gate", action="store_true",
                    help="exit non-zero if any §11 deviation exists")
    ap.add_argument("--exclude", action="append",
                    default=sorted(_CFG.scope("core-specs").exclude_dirs))
    args = ap.parse_args(argv)

    if not args.root.is_dir():
        print("not a directory: %s" % args.root, file=sys.stderr)
        return 2

    findings, docs = collect(args.root, set(args.exclude))

    if args.worklist:
        emit_worklist(findings, docs, args.worklist, args.batch_by)
        print("wrote %d packet(s) to %s" % (len(findings), args.worklist))
    elif args.json:
        print(json.dumps([packet(f, docs) for f in findings], indent=2))
    else:
        print(render_text(findings))

    if args.gate:
        return 1 if findings else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

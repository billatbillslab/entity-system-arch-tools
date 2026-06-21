#!/usr/bin/env python3
"""topology — the cross-spec dependency graph for the whole corpus.

A single spec is a tree (`tree`); the *corpus* is a graph laid over many trees.
The edges are citations: `EXTENSION-TREE §5.2`, `GUIDE-CONFORMANCE §9`. This
analyzer reads every spec in a corpus, builds the registry of canonical names
(the file stem is the canonical host — the same way a peer_id is the canonical
host in the entity tree, and `§N.M` is the path under it), then resolves every
cross-reference against that registry.

Because resolution needs the full name set, the analyzer is corpus-aware by
construction — you cannot know whether `EXTENSION-FOO §3` resolves until you
have seen all the files. The core protocol falls out as the root: the most
depended-upon spec.

What it surfaces:
  * the dependency graph — who depends on whom, with citation counts, ranked by
    in-degree (foundations) and out-degree (integrators)
  * **dangling citations** — a cited doc name with no file in the corpus
    (leaked-internal references, references to archived/renamed specs, typos)
  * **citation-form drift** — a target cited both as `DOC` and `DOC.md`
  * **stale section refs** — a qualified `DOC §N.M` whose section does not exist
    in the resolved target spec

(Internal bare-`§` validation is deferred: a bare `§N.M` is often a cross-doc
reference to a doc named in prose — "core protocol §1.7" — not a self-reference,
so it can't be told apart from a broken self-ref without prose-doc-name
resolution. Every flag above resolves an explicitly-named token and stays
precise.)

    spec topology [ROOT]            # text summary of the corpus graph
    spec topology [ROOT] --json     # the full graph + flags as JSON
    spec topology [ROOT] --dot      # graphviz digraph (pipe to `dot -Tsvg`)

ROOT defaults to the v7.0 core-protocol-domain specs tree. Stdlib-only 3.11+.
(Was tools/spec-topology/spec_topology.py.)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import model as spec_tree  # sibling in tools/spec/
import config as _config   # sibling in tools/spec/
_CFG = _config.load()

# A heading number, permissive enough for letter-suffixed sections (§5.5a).
HEADNUM_RE = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)*[a-z]?)\.?\s")
# A standalone §-reference (also letter-suffix tolerant).
SECREF_RE = re.compile(r"§(\d+(?:\.\d+)*[a-z]?)")
# A doc citation: an all-caps hyphenated name, optional `.md`, optional `§sec`.
CITE_RE = re.compile(
    r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)(\.md)?(?:\s+§(\d+(?:\.\d+)*[a-z]?))?")
# A doc-*shaped* token (broader than a resolvable citation) — used only to
# suppress internal-ref extraction near any doc mention.
DOC_SHAPE_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)(\.md)?\b")
# Non-spec doc families that still name a §-context (proposals, reviews, …) —
# now from the single source of truth (config vocabulary.doc_families).
DOC_VOCAB_EXTRA = _CFG.doc_families

DEFAULT_ROOT = _CFG.scope("core-specs").root


class Spec:
    def __init__(self, stem: str, path: Path):
        self.stem = stem
        self.path = path
        self.sections: Set[str] = set()       # heading numbers it defines
        # edges out: target_stem -> citation count
        self.deps: Dict[str, int] = {}
        # raw citation records for flagging
        self.cites: List[dict] = []           # {target, has_md, sec, line, host}


def scan_corpus(root: Path, excludes: List[str]) -> Dict[str, Spec]:
    specs: Dict[str, Spec] = {}
    for p in sorted(root.rglob("*.md")):
        rel = str(p.relative_to(root))
        if any(ex in rel.split("/") for ex in excludes):
            continue
        stem = p.stem
        if stem in specs:
            print("warn: duplicate stem %s (%s); keeping first" % (stem, rel),
                  file=sys.stderr)
            continue
        specs[stem] = Spec(stem, p)
    return specs


def _host_section(line: int, secs: List[tuple]) -> str:
    """Number of the section containing `line` (shared model helper)."""
    return spec_tree.section_at(line, secs)[0]


def analyze(spec: Spec, known_prefixes: Set[str], stems: Set[str]) -> None:
    text = spec.path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # section set (permissive — includes letter-suffixed headings)
    for ln in lines:
        m = HEADNUM_RE.match(ln)
        if m:
            spec.sections.add(m.group(1))
    tree = spec_tree.parse(text)
    secs = spec_tree.section_index(tree)

    def doc_vocab(token: str) -> bool:
        return (token in stems or token.split("-")[0] in known_prefixes
                or token.split("-")[0] in DOC_VOCAB_EXTRA)

    def names_a_doc(line: str) -> bool:
        for m in DOC_SHAPE_RE.finditer(line):
            if m.group(2) or doc_vocab(m.group(1)):  # `.md` or a doc-family name
                return True
        return False

    # 1) edges — per-line doc citations (resolvable names only)
    for idx, line in enumerate(lines):
        host = _host_section(idx + 1, secs)
        if not host:
            continue  # preamble / changelog — not a normative cross-reference
        for m in CITE_RE.finditer(line):
            token, has_md, sec = m.group(1), bool(m.group(2)), m.group(3)
            if token not in stems and token.split("-")[0] not in known_prefixes:
                continue  # e.g. SHA-256 — not a corpus doc
            if token == spec.stem:
                continue  # self-reference, not an edge
            spec.cites.append({"target": token, "has_md": has_md,
                               "sec": sec, "line": idx + 1, "host": host})
            spec.deps[token] = spec.deps.get(token, 0) + 1

    # Internal bare-§ validation is deliberately NOT computed here. A bare §N.M
    # frequently refers to a doc named in *prose* ("core protocol §1.7", "the V7
    # spec §6.3") rather than by file token, so it cannot be reliably told apart
    # from a broken self-reference without resolving prose doc-names. The four
    # flags below all resolve an explicitly-named token and stay precise.
    _ = names_a_doc  # retained for a future prose-aware internal-ref pass


def build(root: Path, excludes: List[str]):
    specs = scan_corpus(root, excludes)
    stems = set(specs)
    known_prefixes = {s.split("-")[0] for s in stems}
    for spec in specs.values():
        analyze(spec, known_prefixes, stems)
    return specs, stems, known_prefixes


# ---- flag computation -------------------------------------------------------
def compute_flags(specs: Dict[str, Spec], stems: Set[str]):
    dangling: Dict[str, int] = {}                 # unknown target -> count
    drift: Dict[str, Dict[str, int]] = {}         # target -> {form: count}
    stale: List[dict] = []                        # qualified ref to missing section
    for spec in specs.values():
        for c in spec.cites:
            target = c["target"]
            if target not in stems:
                dangling[target] = dangling.get(target, 0) + 1
                continue
            form = target + (".md" if c["has_md"] else "")
            drift.setdefault(target, {}).setdefault(form, 0)
            drift[target][form] += 1
            if c["sec"] and c["sec"] not in specs[target].sections:
                stale.append({"from": spec.stem, "to": target,
                              "sec": c["sec"], "line": c["line"]})
    drift = {t: forms for t, forms in drift.items() if len(forms) > 1}
    return dangling, drift, stale


def in_degree(specs: Dict[str, Spec]) -> Dict[str, Tuple[int, int]]:
    """target -> (distinct citing specs, total citation refs)."""
    by_specs: Dict[str, int] = {}
    by_refs: Dict[str, int] = {}
    for spec in specs.values():
        for target, cnt in spec.deps.items():
            if target in specs:
                by_specs[target] = by_specs.get(target, 0) + 1
                by_refs[target] = by_refs.get(target, 0) + cnt
    return {t: (by_specs[t], by_refs.get(t, 0)) for t in by_specs}


# ---- renderers --------------------------------------------------------------
def render_text(specs, stems, flags) -> str:
    dangling, drift, stale = flags
    indeg = in_degree(specs)
    edges = sum(1 for s in specs.values() for t in s.deps if t in specs)
    root = max(indeg, key=lambda t: indeg[t]) if indeg else "(none)"
    out: List[str] = []
    out.append("spec topology — %d specs, %d cross-spec edges  (root: %s)"
               % (len(specs), edges, root))
    out.append("")
    out.append("most depended-upon (in-degree):")
    for t, (ns, nr) in sorted(indeg.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:15]:
        out.append("  %-38s cited by %2d specs, %3d refs" % (t, ns, nr))
    out.append("")
    out.append("most dependent (out-degree):")
    od = sorted(specs.values(), key=lambda s: -sum(1 for t in s.deps if t in specs))
    for s in od[:15]:
        n = sum(1 for t in s.deps if t in specs)
        if n:
            out.append("  %-38s depends on %2d specs" % (s.stem, n))
    out.append("")
    out.append("flags")
    out.append("  dangling citations (no file in corpus): %d distinct" % len(dangling))
    for t, c in sorted(dangling.items(), key=lambda kv: -kv[1])[:12]:
        out.append("    %-40s x%d" % (t, c))
    out.append("  citation-form drift (DOC vs DOC.md): %d" % len(drift))
    for t, forms in sorted(drift.items()):
        out.append("    %-30s %s" % (t, ", ".join(
            "%s x%d" % (f, n) for f, n in sorted(forms.items()))))
    out.append("  stale section refs (target section absent): %d" % len(stale))
    for s in stale[:12]:
        out.append("    %s -> %s §%s  ·L%d" % (s["from"], s["to"], s["sec"], s["line"]))
    out.append("")
    out.append("(internal bare-§ validation deferred — a bare §N.M is often a "
               "prose-named cross-doc ref, not a self-ref; needs prose-doc-name "
               "resolution to be precise.)")
    return "\n".join(out)


def render_json(specs, stems, flags) -> str:
    dangling, drift, stale = flags
    indeg = in_degree(specs)
    root = max(indeg, key=lambda t: indeg[t]) if indeg else None
    spec_nodes = []
    for s in sorted(specs.values(), key=lambda x: x.stem):
        spec_nodes.append({
            "name": s.stem,
            "path": str(s.path),
            "sections": len(s.sections),
            "in_degree_specs": indeg.get(s.stem, (0, 0))[0],
            "in_degree_refs": indeg.get(s.stem, (0, 0))[1],
            "out_degree": sum(1 for t in s.deps if t in specs),
        })
    edges = []
    for s in sorted(specs.values(), key=lambda x: x.stem):
        for t, c in sorted(s.deps.items()):
            if t in specs:
                edges.append({"from": s.stem, "to": t, "count": c})
    return json.dumps({
        "root": root,
        "specs": spec_nodes,
        "edges": edges,
        "dangling": dangling,
        "drift": drift,
        "stale_sections": stale,
    }, indent=2)


def render_dot(specs, stems, flags) -> str:
    indeg = in_degree(specs)
    out = ["digraph spec_corpus {", '  rankdir=LR;',
           '  node [shape=box, fontsize=10];']
    for s in sorted(specs.values(), key=lambda x: x.stem):
        cited = indeg.get(s.stem, (0, 0))[0]
        # foundations (high in-degree) drawn heavier
        pen = 2.0 if cited >= 8 else 1.0
        out.append('  "%s" [penwidth=%.1f];' % (s.stem, pen))
    for s in sorted(specs.values(), key=lambda x: x.stem):
        for t, c in sorted(s.deps.items()):
            if t in specs:
                out.append('  "%s" -> "%s" [label="%d"];' % (s.stem, t, c))
    out.append("}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, nargs="?", default=DEFAULT_ROOT,
                    help="corpus root dir (default: v7.0 core specs)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dot", action="store_true")
    ap.add_argument("--exclude", action="append",
                    default=sorted(_CFG.scope("core-specs").exclude_dirs),
                    help="path segments to skip (repeatable; default from config scope)")
    args = ap.parse_args(argv)

    if not args.root.is_dir():
        print("not a directory: %s" % args.root, file=sys.stderr)
        return 2
    specs, stems, _prefixes = build(args.root, args.exclude)
    flags = compute_flags(specs, stems)

    if args.json:
        print(render_json(specs, stems, flags))
    elif args.dot:
        print(render_dot(specs, stems, flags))
    else:
        print(render_text(specs, stems, flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

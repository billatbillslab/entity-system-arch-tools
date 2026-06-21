#!/usr/bin/env python3
"""render — render the diverse structure hidden in a narrative spec.

The normative payload of a spec — type definitions, algorithms, constant /
code tables, and (later) conformance rules — lives inside the prose as a small
set of structural blocks. `render` consumes the structural model (the same one
`tree` emits) and lifts each of those payloads *out* of the narrative into a
clean, navigable catalog, addressed by the `§N.M` it lives in.

The architecture is two layers, on purpose:

  * **extractors** — one per semantic structure (`types`, `algorithms`,
    `constants`, …). Each turns the model + source into a list of `Item`s.
    Anything with semantic meaning gets its own extractor.
  * **renderers** — turn `Item`s into an output form. `json` is the canonical,
    stable model; `md` is one presentation over it. The output form is meant to
    change — re-skin by editing a renderer (or the per-catalog `Catalog` config),
    never the extractor.

Adding a structure is a new extractor + a registry line; both renderers and all
output handling come for free.

    spec render SPEC.md                       # type catalog, markdown
    spec render SPEC.md --what algorithms     # algorithm catalog
    spec render SPEC.md --what constants      # constant / code tables
    spec render SPEC.md --what all            # every catalog
    spec render SPEC.md --format json         # the canonical structured form
    spec render SPEC.md --what all --output DIR  # one CATALOG-*.md per catalog

Stdlib-only Python 3.11+. Never edits the source spec; output is derived.
(Was tools/spec-render/spec_render.py.)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Reuse the one parser — "parse once, many consumers". The model lives next door.
import model as spec_tree  # sibling in tools/spec/

# A type definition opens with `name :=` at the start of a (possibly indented)
# fenced line. The name is a type-path or bare identifier; `:=` is the operator.
DEF_RE = re.compile(r"^(\s*)([A-Za-z][\w./-]*)\s*:=(.*)$")
# A pseudocode entrypoint: a function-style signature `name(args):` or `name(args)`.
SIG_RE = re.compile(r"^\s*(?:def\s+)?([a-z_][\w./-]*)\s*\([^)]*\)\s*:?\s*$")


class Item:
    """One semantic unit lifted from the narrative — uniform across catalogs."""

    __slots__ = ("kind", "name", "section", "section_title", "line", "body", "meta")

    def __init__(self, kind, name, section, section_title, line, body, meta=None):
        self.kind = kind                  # catalog key: types | algorithms | constants
        self.name = name                  # the handle a reader/implementer cites
        self.section = section            # enclosing §N.M (or "")
        self.section_title = section_title
        self.line = line                  # 1-indexed source line
        self.body = body                  # full payload text (verbatim from source)
        self.meta = meta or {}            # per-catalog extras (e.g. record/alias)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "section": self.section,
            "section_title": self.section_title,
            "line": self.line,
            "body": self.body,
            **self.meta,
        }


# Section/fence helpers live in the model (parse once).
section_index = spec_tree.section_index
section_at = spec_tree.section_at


# ---- extractors (one per semantic structure) --------------------------------
def extract_types(text: str, root) -> List[Item]:
    """Every `name := …` definition. A fence holds one record (`x := { … }`)
    or a run of one-line aliases; nested `field:` lines are not definitions."""
    lines = text.splitlines()
    secs = section_index(root)
    items: List[Item] = []
    for _open, _info, body, body_start in spec_tree.iter_fences(lines):
        opens = [j for j, b in enumerate(body) if DEF_RE.match(b)]
        for k, j in enumerate(opens):
            end = opens[k + 1] if k + 1 < len(opens) else len(body)
            chunk = body[j:end]
            while chunk and not chunk[-1].strip():
                chunk.pop()
            m = DEF_RE.match(body[j])
            name, rhs = m.group(2), m.group(3).strip()
            kind = "record" if rhs.startswith("{") or rhs.endswith("{") else "alias"
            src_line = body_start + j
            number, title = section_at(src_line, secs)
            items.append(Item("types", name, number, title, src_line,
                              "\n".join(chunk), {"def_kind": kind}))
    return items


def extract_algorithms(text: str, root) -> List[Item]:
    """Every pseudocode fence, named by its entrypoint signature when present."""
    lines = text.splitlines()
    secs = section_index(root)
    items: List[Item] = []
    for open_line, info, body, body_start in spec_tree.iter_fences(lines):
        if spec_tree.classify_fence(info, body) != "pseudocode":
            continue
        name = None
        for b in body:
            sm = SIG_RE.match(b)
            if sm:
                name = sm.group(1)
                break
        number, title = section_at(open_line, secs)
        if not name:
            name = "algorithm@§%s" % (number or "?")
        items.append(Item("algorithms", name, number, title, open_line,
                          "\n".join(body)))
    return items


# A markdown table separator row: pipes, dashes, colons, spaces only — and at
# least one dash. Distinguishes a real table from an ASCII diagram of `|` rows.
SEP_ROW_RE = re.compile(r"^\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def extract_constants(text: str, root) -> List[Item]:
    """Every markdown table (header + `---` separator), named by its caption.

    Fence-aware: pipe-rows inside code fences (example tables, ASCII sequence
    diagrams) are skipped. A run of `|`-rows is a table only if its second row
    is a real separator — which also rejects diagrams built from `|`."""
    lines = text.splitlines()
    secs = section_index(root)
    items: List[Item] = []
    n = len(lines)
    i = 0
    in_fence = False
    while i < n:
        s = lines[i].strip()
        if s.startswith("```"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not (s.startswith("|") and "|" in s[1:]):
            i += 1
            continue
        start = i
        while i < n and lines[i].strip().startswith("|"):
            i += 1
        block = lines[start:i]
        if len(block) < 2 or not SEP_ROW_RE.match(block[1].strip()):
            continue  # not a real markdown table (no separator row)
        cap = start - 1
        while cap >= 0 and not lines[cap].strip():
            cap -= 1
        caption = _clean_caption(lines[cap]) if cap >= 0 else ""
        number, title = section_at(start + 1, secs)
        name = caption or ("table@§%s" % (number or "?"))
        items.append(Item("constants", name, number, title, start + 1,
                          "\n".join(block), {"rows": len(block) - 2}))
    return items


# strongest-first, so a sentence is labelled by its most binding keyword
RFC2119_ORDER = ["MUST NOT", "MUST", "SHALL", "REQUIRED",
                 "SHOULD NOT", "SHOULD", "MAY"]
_KW_RE = re.compile(r"\b(MUST NOT|MUST|SHALL|REQUIRED|SHOULD NOT|SHOULD|MAY)\b")
_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z(`])")


def extract_conformance(text: str, root) -> List[Item]:
    """Every RFC-2119 normative statement (MUST / SHOULD / MAY …), one per
    sentence, with its strongest keyword — the spec's obligation census."""
    lines = text.splitlines()
    secs = section_index(root)
    items: List[Item] = []
    n = len(lines)
    i = 0
    buf: List[str] = []
    buf_line = 0

    def flush():
        if not buf:
            return
        blob = " ".join(b.strip() for b in buf).strip()
        for sent in _SENT_SPLIT.split(blob):
            kws = set(_KW_RE.findall(sent))
            if not kws:
                continue
            strongest = next(k for k in RFC2119_ORDER if k in kws)
            number, title = section_at(buf_line, secs)
            if not number:
                continue  # preamble / changelog narrative — not normative
            body = re.sub(r"\*\*|`", "", sent).strip()
            items.append(Item("conformance", "", number, title, buf_line,
                              body, {"keyword": strongest}))
        buf.clear()

    in_fence = False
    while i < n:
        s = lines[i].strip()
        if s.startswith("```"):
            flush()
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        # skip headings and table rows — prose only
        if s.startswith("#") or (s.startswith("|") and "|" in s[1:]):
            flush()
            i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        if not buf:
            buf_line = i + 1
        buf.append(lines[i])
        i += 1
    flush()
    # assign per-section ordinal handles (§N.M·rNN)
    seq: Dict[str, int] = {}
    for it in items:
        seq[it.section] = seq.get(it.section, 0) + 1
        it.name = "§%s·r%d" % (it.section or "?", seq[it.section])
    return items


def _clean_caption(raw: str) -> str:
    """Strip markdown furniture off a table caption line."""
    t = raw.strip()
    t = re.sub(r"^#+\s*", "", t)          # heading markers (table follows a heading)
    t = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", t)  # leading section number
    t = re.sub(r"\*\*", "", t)            # bold
    t = re.sub(r"`([^`]*)`", r"\1", t)    # inline code
    # if it's a full sentence, keep the lead clause up to the first period
    t = t.split(". ")[0].strip()
    t = t.rstrip(":.").strip()
    t = t.rstrip(" ;—-(").strip()         # dangling open-clause punctuation
    return t[:80].rstrip(" ;—-(").strip()


# ---- catalog config (the md-side customization point) -----------------------
class Catalog:
    def __init__(self, key, noun, title, extract, body_as="code", extra_cols=()):
        self.key = key
        self.noun = noun                  # singular, for the count line
        self.title = title
        self.extract: Callable = extract
        self.body_as = body_as            # "code" (fence the body) | "raw" (verbatim md)
        self.extra_cols = extra_cols      # tuple of (header, meta_key)


CATALOGS: Dict[str, Catalog] = {
    "types": Catalog("types", "definition", "Type catalog", extract_types,
                     body_as="code", extra_cols=(("kind", "def_kind"),)),
    "algorithms": Catalog("algorithms", "algorithm", "Algorithm catalog",
                          extract_algorithms, body_as="code"),
    "constants": Catalog("constants", "table", "Constant & code tables",
                         extract_constants, body_as="raw",
                         extra_cols=(("rows", "rows"),)),
    "conformance": Catalog("conformance", "requirement", "Conformance matrix",
                           extract_conformance, body_as="raw",
                           extra_cols=(("keyword", "keyword"),)),
}


# ---- renderers --------------------------------------------------------------
def render_md(cat: Catalog, items: List[Item], spec_name: str) -> str:
    out: List[str] = []
    out.append("# %s — %s" % (cat.title, spec_name))
    out.append("")
    out.append("_Derived from `%s` by `spec render`. Source is canonical; "
               "regenerate, do not hand-edit._" % spec_name)
    out.append("")
    out.append("**%d %s%s**, addressed by the `§N.M` they live in."
               % (len(items), cat.noun, "" if len(items) == 1 else "s"))
    out.append("")
    # index
    out.append("## Index")
    out.append("")
    cols = ["name"] + [h for h, _ in cat.extra_cols] + ["§", "line"]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "---|" * len(cols))
    for d in items:
        sec = ("§%s" % d.section) if d.section else "—"
        cells = ["`%s`" % d.name]
        cells += [str(d.meta.get(k, "")) for _, k in cat.extra_cols]
        cells += [sec, str(d.line)]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    # bodies, grouped by section in source order
    out.append("## Entries")
    out.append("")
    last_sec = object()
    for d in items:
        if d.section != last_sec:
            hdr = ("§%s %s" % (d.section, d.section_title)) if d.section else "(preamble)"
            out.append("### %s" % hdr)
            out.append("")
            last_sec = d.section
        out.append("#### `%s`  ·L%d" % (d.name, d.line))
        out.append("")
        if cat.body_as == "code":
            out.append("```")
            out.append(d.body)
            out.append("```")
        else:
            out.append(d.body)
        out.append("")
    return "\n".join(out)


def render_json(catalogs: Dict[str, List[Item]], spec_name: str) -> str:
    return json.dumps({
        "spec": spec_name,
        "catalogs": {k: [d.to_dict() for d in v] for k, v in catalogs.items()},
    }, indent=2)


FILENAME = {"types": "CATALOG-TYPES", "algorithms": "CATALOG-ALGORITHMS",
            "constants": "CATALOG-CONSTANTS", "conformance": "CATALOG-CONFORMANCE"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--what", choices=list(CATALOGS) + ["all"], default="types",
                    help="which structure(s) to lift")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--output", type=Path,
                    help="write catalog file(s) into this dir instead of stdout")
    args = ap.parse_args(argv)

    text = args.spec.read_text(encoding="utf-8")
    root = spec_tree.parse(text)
    keys = list(CATALOGS) if args.what == "all" else [args.what]
    extracted = {k: CATALOGS[k].extract(text, root) for k in keys}

    if args.format == "json":
        payload = render_json(extracted, args.spec.name)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            stem = "CATALOG-ALL" if args.what == "all" else FILENAME[args.what]
            dest = args.output / (stem + ".json")
            dest.write_text(payload + "\n", encoding="utf-8")
            print("wrote %s" % dest, file=sys.stderr)
        else:
            print(payload)
        return 0

    # markdown — one document per catalog
    docs = [(k, render_md(CATALOGS[k], extracted[k], args.spec.name)) for k in keys]
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        for k, doc in docs:
            dest = args.output / (FILENAME[k] + ".md")
            dest.write_text(doc + "\n", encoding="utf-8")
            cnt = len(extracted[k])
            noun = CATALOGS[k].noun + ("" if cnt == 1 else "s")
            print("wrote %d %s to %s" % (cnt, noun, dest), file=sys.stderr)
    else:
        print("\n\n---\n\n".join(doc for _, doc in docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""model — the structural parser at the core of the spec toolkit.

A normative spec is a flat markdown narrative, but it's built from a small
grammar of *structural symbols*: headings (`## N.`/`### N.M`), section
cross-refs (`§N.M`), external doc refs (`DOC §N.M`), type definitions
(`name := { ... }`), pseudocode/example fences, tables, RFC-2119 keywords,
and prose paragraphs. This module parses those symbols into a tree of
addressable nodes — section → subsection → block → paragraph — so a 4,000-line
spec becomes a navigable structure instead of a wall of text.

It is the single parser: every analyzer reads this model (`parse`, `walk`,
`iter_fences`, `section_index`, `section_at`, `classify_fence`) rather than
re-implementing fence/section tracking. `spec tree` is the reader over it:

    spec tree SPEC.md                # heading tree + per-section block makeup
    spec tree SPEC.md --symbols      # global symbol census (the grammar)
    spec tree SPEC.md --refs         # cross-reference graph (§ + external docs)
    spec tree SPEC.md --json         # full decomposed node tree as JSON

Stdlib-only Python 3.11+. (Was tools/spec-tree/spec_tree.py.)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import config as _config  # sibling in tools/spec/
# Entity-type roots from the single source of truth (config vocabulary.type_roots);
# was a duplicated inline literal also living in spec-style.
_TYPE_ROOTS = sorted(_config.load().type_roots)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")
SECREF_RE = re.compile(r"§(\d+(?:\.\d+)*)")
EXTREF_RE = re.compile(r"\b([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+(?:\.md)?)\s+§(\d+(?:\.\d+)*)")
DOCREF_RE = re.compile(r"\b((?:ENTITY|EXTENSION|SYSTEM|GUIDE|ARCHITECTURE|DOMAIN)-[A-Z0-9-]+)(?:\.md)?")
TYPEDEF_RE = re.compile(r"^([a-z][\w./-]*)\s*:=")
TYPEPATH_RE = re.compile(r"\b(?:" + "|".join(_TYPE_ROOTS) + r")/[a-z][\w./-]+")
RFC2119_RE = re.compile(r"\b(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY|SHALL|REQUIRED)\b")


def classify_fence(info: str, body: List[str]) -> str:
    text = "\n".join(body)
    if any(TYPEDEF_RE.match(b) for b in body):
        return "type-def"
    if re.search(r"^\s*\w[\w]*\([^)]*\):", text, re.M) or re.search(r"\b(DENY|ALLOW|REJECT|return)\b", text):
        return "pseudocode"
    if re.search(r'"\s*type\s*"\s*:', text) or text.strip().startswith("{"):
        return "example"
    if re.search(r"ecfv1|[0-9a-f]{16,}", text):
        return "hexdump"
    return "code:" + (info.strip() or "plain")


class Block:
    __slots__ = ("kind", "line", "meta")

    def __init__(self, kind: str, line: int, meta: str = ""):
        self.kind = kind
        self.line = line
        self.meta = meta


class Node:
    def __init__(self, level: int, number: str, title: str, line: int):
        self.level = level
        self.number = number
        self.title = title
        self.line = line
        self.children: List["Node"] = []
        self.blocks: List[Block] = []
        self.secrefs: List[str] = []
        self.docrefs: List[str] = []
        self.extrefs: List[str] = []


def parse(text: str) -> Node:
    lines = text.splitlines()
    root = Node(0, "", "(document)", 0)
    stack = [root]
    i = 0
    n = len(lines)

    def cur() -> Node:
        return stack[-1]

    while i < n:
        ln = lines[i]
        stripped = ln.strip()

        # heading
        hm = HEADING_RE.match(ln)
        if hm:
            level = len(hm.group(1))
            raw = hm.group(2).strip()
            nm = NUM_RE.match(raw)
            number = nm.group(1) if nm else ""
            title = nm.group(2) if nm else raw
            node = Node(level, number, title, i + 1)
            while stack and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(node)
            stack.append(node)
            i += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            info = stripped[3:]
            body = []
            i += 1
            start = i
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            kind = classify_fence(info, body)
            blk = Block(kind, start, info.strip())
            cur().blocks.append(blk)
            # capture type-defs declared in the fence
            for b in body:
                td = TYPEDEF_RE.match(b)
                if td:
                    cur().blocks.append(Block("typename", start, td.group(1)))
            continue

        # table (run of pipe-rows)
        if stripped.startswith("|") and "|" in stripped[1:]:
            start = i
            rows = 0
            while i < n and lines[i].strip().startswith("|"):
                rows += 1
                i += 1
            cur().blocks.append(Block("table", start + 1, "%d rows" % rows))
            continue

        # blank
        if not stripped:
            i += 1
            continue

        # list
        if re.match(r"^\s*([-*+]|\d+\.)\s+", ln):
            start = i
            while i < n and (re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]) or
                             (lines[i].strip() and lines[i].startswith("  "))):
                i += 1
            cur().blocks.append(Block("list", start + 1))
            _scan_refs(cur(), lines[start:i])
            continue

        # blockquote
        if stripped.startswith(">"):
            start = i
            while i < n and lines[i].strip().startswith(">"):
                i += 1
            cur().blocks.append(Block("note", start + 1))
            _scan_refs(cur(), lines[start:i])
            continue

        # paragraph (prose run)
        start = i
        while i < n and lines[i].strip() and not lines[i].strip().startswith(("#", "```", "|", ">")) \
                and not re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
            i += 1
        para = lines[start:i]
        cur().blocks.append(Block("paragraph", start + 1, "%d lines" % len(para)))
        _scan_refs(cur(), para)

    return root


def _scan_refs(node: Node, chunk: List[str]) -> None:
    text = "\n".join(chunk)
    node.secrefs += SECREF_RE.findall(text)
    for doc, _sec in EXTREF_RE.findall(text):
        node.extrefs.append(doc)
    node.docrefs += DOCREF_RE.findall(text)


# ---- aggregations -----------------------------------------------------------
def walk(node: Node):
    yield node
    for c in node.children:
        yield from walk(c)


# ---- shared model API (used by render, topology) ----------------------------
def iter_fences(lines: List[str]):
    """Yield (open_line, info, body_lines, body_start) for each ``` fence.

    `open_line` and `body_start` are 1-indexed source line numbers (the opening
    fence line, and the first body line). The canonical fence walk — consumers
    re-slice source bodies from it instead of re-implementing fence tracking.
    """
    n = len(lines)
    i = 0
    while i < n:
        if not lines[i].strip().startswith("```"):
            i += 1
            continue
        info = lines[i].strip()[3:].strip()
        open_line = i + 1
        i += 1
        body_start = i
        while i < n and not lines[i].strip().startswith("```"):
            i += 1
        yield open_line, info, lines[body_start:i], body_start + 1
        i += 1  # past closing fence


def section_index(root: "Node") -> List[tuple]:
    """Flat, line-ordered [(start_line, number, title)] of headed sections."""
    secs = [(n.line, n.number, n.title) for n in walk(root) if n.level > 0]
    secs.sort()
    return secs


def section_at(line: int, index: List[tuple]) -> tuple:
    """(number, title) of the deepest section whose heading precedes `line`."""
    found = ("", "")
    for sline, number, title in index:
        if sline <= line:
            found = (number, title)
        else:
            break
    return found


def block_summary(node: Node) -> str:
    counts: Dict[str, int] = {}
    for b in node.blocks:
        key = b.kind.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    order = ["paragraph", "type-def", "typename", "pseudocode", "example",
             "table", "list", "note", "hexdump", "code"]
    parts = []
    for k in order:
        if counts.get(k):
            parts.append("%s:%d" % (k, counts[k]))
    return " ".join(parts)


def print_tree(root: Node, max_level: int) -> None:
    for node in list(walk(root))[1:]:
        if node.level > max_level:
            continue
        indent = "  " * (node.level - 1)
        label = ("%s " % node.number) if node.number else ""
        bs = block_summary(node)
        tail = ("  [%s]" % bs) if bs else ""
        print("%s%s%s  ·L%d%s" % (indent, label, node.title, node.line, tail))


def print_symbols(root: Node) -> None:
    c: Dict[str, int] = {}
    typenames = set()
    typepaths = set()
    rfc: Dict[str, int] = {}
    for node in walk(root):
        for b in node.blocks:
            key = b.kind.split(":")[0]
            c[key] = c.get(key, 0) + 1
            if b.kind == "typename":
                typenames.add(b.meta)
    nodes = list(walk(root))
    headings = [n for n in nodes if n.level > 0]
    print("structural symbol census")
    print("  headings (sections):   %d" % len(headings))
    print("    h2 / h3 / h4+ :      %d / %d / %d" % (
        sum(1 for n in headings if n.level == 2),
        sum(1 for n in headings if n.level == 3),
        sum(1 for n in headings if n.level >= 4)))
    for k in ("paragraph", "type-def", "typename", "pseudocode", "example",
              "table", "list", "note", "hexdump"):
        if c.get(k):
            print("  %-20s %d" % (k, c[k]))
    # type paths + rfc2119 from full text pass below in main
    if typenames:
        print("  distinct type-defs:    %d" % len(typenames))


def print_refs(root: Node) -> None:
    internal: Dict[str, int] = {}
    external: Dict[str, int] = {}
    for node in walk(root):
        for r in node.secrefs:
            internal[r] = internal.get(r, 0) + 1
        for d in node.extrefs + node.docrefs:
            external[d] = external.get(d, 0) + 1
    print("internal cross-refs (§N.M)  — %d distinct targets, %d total" % (
        len(internal), sum(internal.values())))
    for ref, cnt in sorted(internal.items(), key=lambda kv: -kv[1])[:20]:
        print("  §%-8s x%d" % (ref, cnt))
    print("\nexternal doc references — %d distinct" % len(external))
    for ref, cnt in sorted(external.items(), key=lambda kv: -kv[1]):
        print("  %-40s x%d" % (ref, cnt))


def to_json(node: Node) -> dict:
    return {
        "level": node.level, "number": node.number, "title": node.title,
        "line": node.line,
        "blocks": [{"kind": b.kind, "line": b.line, "meta": b.meta} for b in node.blocks],
        "secrefs": sorted(set(node.secrefs)),
        "extrefs": sorted(set(node.extrefs)),
        "children": [to_json(c) for c in node.children],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--symbols", action="store_true", help="global symbol census")
    ap.add_argument("--refs", action="store_true", help="cross-reference graph")
    ap.add_argument("--json", action="store_true", help="full node tree as JSON")
    ap.add_argument("--level", type=int, default=3, help="max heading depth for the tree view")
    args = ap.parse_args(argv)

    text = args.spec.read_text(encoding="utf-8")
    root = parse(text)

    if args.json:
        print(json.dumps(to_json(root), indent=2))
        return 0
    if args.symbols:
        print_symbols(root)
        tp = set(TYPEPATH_RE.findall(text))
        rfc: Dict[str, int] = {}
        for m in RFC2119_RE.findall(text):
            rfc[m] = rfc.get(m, 0) + 1
        print("  distinct type-paths:   %d" % len(tp))
        print("  RFC-2119 keywords:     %s" % ", ".join(
            "%s:%d" % (k, v) for k, v in sorted(rfc.items(), key=lambda kv: -kv[1])))
        return 0
    if args.refs:
        print_refs(root)
        return 0

    print_tree(root, args.level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""spec — the unified spec toolkit CLI.

One functional core, commands as pathways into it. Replaces the five separate
scripts in `tools/spec-*/`. Each subcommand delegates to an analyzer module
that reads the one shared model (`model.py`) and the one config
(`config.py` / `config.default.toml`).

    spec tree <file> [--symbols|--refs|--json|--level N]   structural tree (reader)
    spec render <file> [--what …|--format …|--output …]    catalogs (reader)
    spec topology [ROOT] [--json|--dot]                    corpus graph (reader)
    spec address [ROOT] [--worklist …|--gate|--json]       §11 addressing validator
    spec standards [--root …|--refine|--json]              release-readiness gate
    spec style [--root …|--all|--json|--config …]          naming gate
    spec check                                             run both gates (style + standards)
    spec config [CONFIG.toml]                              print the resolved config

`check` exits non-zero if either gate fails. Gates (`standards`, `style`,
`check`) exit non-zero on violations; readers always exit 0.

Stdlib-only Python 3.11+. Run as `python3 tools/spec/cli.py <subcommand> …`.
"""

import sys
from pathlib import Path

import address
import config as _config
import model
import render
import standards
import style
import topology

# subcommand -> the module main(argv) it delegates to. Args after the
# subcommand are passed through verbatim, so each command behaves exactly as
# its former standalone script did.
DELEGATES = {
    "tree": model.main,
    "render": render.main,
    "topology": topology.main,
    "address": address.main,
    "standards": standards.main,
    "style": style.main,
}


def cmd_check(argv):
    """Run both corpus gates (style + standards), like `make check`.

    Non-zero exit if either gate reports violations. Output is each gate's own
    report, in order, separated by a banner — a convenience over running the
    two subcommands by hand; the per-gate output is byte-identical to running
    them individually."""
    if argv:
        print("spec check takes no arguments", file=sys.stderr)
        return 2
    print("=== spec style ===")
    rc_style = style.main([])
    print("\n=== spec standards ===")
    rc_standards = standards.main([])
    rc = 1 if (rc_style or rc_standards) else 0
    print("\n%s — style=%s standards=%s"
          % ("✓ both gates passed" if rc == 0 else "✗ a gate failed",
             "fail" if rc_style else "pass",
             "fail" if rc_standards else "pass"))
    return rc


def cmd_config(argv):
    """Print the resolved config (which root, excludes, vocabulary, scopes).

    Transparency / debugging — answers "what corpus does the tool actually see
    with this config?". Optional positional: a config.toml to resolve instead
    of the bundled default."""
    if len(argv) > 1:
        print("usage: spec config [CONFIG.toml]", file=sys.stderr)
        return 2
    cfg = _config.load(Path(argv[0]) if argv else None)
    print("repo_root  :", cfg.repo_root)
    print("corpus_dir :", cfg.corpus_dir)
    print("type_roots :", sorted(cfg.type_roots))
    print("doc_families : %d" % len(cfg.doc_families))
    print("classes    :", cfg.classes)
    for name in cfg._d.get("scope", {}):
        sc = cfg.scope(name)
        print("scope %-20s root=%s  files=%d" % (
            name, sc.root.relative_to(cfg.repo_root), len(sc.find_markdown())))
    return 0


EXTRA = {"check": cmd_check, "config": cmd_config}


def usage(stream=sys.stdout):
    print(__doc__.strip(), file=stream)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in DELEGATES:
        return DELEGATES[cmd](rest)
    if cmd in EXTRA:
        return EXTRA[cmd](rest)
    print("unknown command: %s" % cmd, file=sys.stderr)
    print("commands: %s" % ", ".join(list(DELEGATES) + list(EXTRA)), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""address_selftest — invariant checks for the §11 addressing validator.

Unlike `parity.sh`, this does NOT pin finding *counts* (those change as the
corpus is corrected). It pins the validator's *behavior* against synthetic
fixtures — the contract the downstream passes rely on. Re-run any time:

    python3 tools/spec/tests/address_selftest.py     # exits non-zero on failure

Stdlib-only. Lives beside parity.sh as the second leg of tool verification.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/spec
import address  # noqa: E402
import config as _config  # noqa: E402

CFG = _config.load()
FAKE = Path("MEM.md")


def env_for(stems, source_class, namespace=None):
    nick_map = {k.lower(): v for k, v in CFG.nicknames.items()}
    import re
    keys = sorted(nick_map, key=len, reverse=True)
    nick_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.I)
    return {
        "nick_re": nick_re, "nick_map": nick_map,
        "stems": set(stems), "prefixes": {s.split("-")[0] for s in stems},
        "families": CFG.doc_families, "cfg": CFG,
        "namespace": set(namespace if namespace is not None else stems),
        "source_class": source_class,
    }


def run(host_stem, text, stems, source_class="canonical-spec", docs=None, namespace=None):
    """Analyze `text` as host_stem against a synthetic corpus; return findings."""
    host = address.build_doc(host_stem, FAKE, text=text)
    corpus = {host_stem: host}
    for s in stems:
        if s not in corpus:
            corpus[s] = address.build_doc(s, FAKE, text="# t\n\n## 5. s\n### 5.2 x\n")
    if docs:
        corpus.update(docs)
    env = env_for(set(corpus), source_class, namespace)
    env["source_class"] = source_class
    return address.analyze_doc(host, corpus, env)


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def nickname_fires_on_governed_sec():
    f = run("HOST", "# t\n\n## 1. a\n\nSee V7 §5.2 for detail.\n",
            {"ENTITY-CORE-PROTOCOL"})
    nk = [x for x in f if x.cls == "nickname"]
    assert nk and nk[0].edit_new == "ENTITY-CORE-PROTOCOL.md", nk


@case
def nickname_silent_on_bare_prose():
    # "V7" with no governing § must NOT be flagged (it's prose, not a citation).
    f = run("HOST", "# t\n\n## 1. a\n\nThe V7 era and v7.75 shipped.\n",
            {"ENTITY-CORE-PROTOCOL"})
    assert not [x for x in f if x.cls == "nickname"], f


@case
def code_fence_excluded():
    f = run("HOST", "# t\n\n## 1. a\n\n```\nsee V7 §5.2 and EXTENSION-X §3\n```\n",
            {"ENTITY-CORE-PROTOCOL", "EXTENSION-X"})
    assert not f, f


@case
def depends_line_excluded():
    f = run("HOST", "# t\n**Depends**: EXTENSION-X §3\n\n## 1. a\n\nbody.\n",
            {"EXTENSION-X"})
    assert not f, f


@case
def path_artifact_excluded():
    f = run("HOST", "# t\n\n## 1. a\n\nAt `core/specs/EXTENSION-X.md` line.\n",
            {"EXTENSION-X"})
    assert not [x for x in f if x.cls == "drift"], f


@case
def drift_bare_token_gets_md():
    f = run("HOST", "# t\n\n## 1. a\n\nDefined in EXTENSION-X §5.2 here.\n",
            {"EXTENSION-X"})
    dr = [x for x in f if x.cls == "drift"]
    assert dr and dr[0].edit_new == "EXTENSION-X.md", dr
    # span replacement is surgical: only the token changes
    line = "Defined in EXTENSION-X §5.2 here."
    s, e = dr[0].edit_span
    assert line[s:e] == "EXTENSION-X", (line[s:e], dr[0].edit_span)


@case
def conformant_external_not_flagged():
    f = run("HOST", "# t\n\n## 1. a\n\nDefined in EXTENSION-X.md §5.2 here.\n",
            {"EXTENSION-X"})
    assert not f, f


@case
def nick_nested_in_doctoken_not_flagged():
    # The "V7" inside ENTITY-CORE-PROTOCOL.md is part of the filename, not a
    # prose nickname; the citation is already canonical -> no finding at all.
    f = run("HOST", "# t\n\n## 1. a\n\nopen types (ENTITY-CORE-PROTOCOL.md §5.2) here.\n",
            {"ENTITY-CORE-PROTOCOL"})
    assert not [x for x in f if x.cls == "nickname"], f
    assert not f, f


@case
def bare_internal_unresolved_flagged():
    f = run("HOST", "# t\n\n## 1. a\n\nSee §9.9 below.\n", set())
    bi = [x for x in f if x.cls == "bare-internal"]
    assert bi and bi[0].sec == "9.9", bi


@case
def bare_internal_resolved_not_flagged():
    f = run("HOST", "# t\n\n## 1. a\n### 1.1 b\n\nSee §1.1 above.\n", set())
    assert not [x for x in f if x.cls == "bare-internal"], f


@case
def leak_from_normative_spec():
    # normative spec citing a process artifact present in the namespace = leak.
    f = run("EXTENSION-X", "# t\n\n## 1. a\n\nPer PROPOSAL-FOO §2 we do this.\n",
            set(), source_class="canonical-spec",
            namespace={"EXTENSION-X", "PROPOSAL-FOO"})
    assert [x for x in f if x.cls == "leak"], f


@case
def arch_doc_citing_proposal_permitted():
    # same citation from an arch-doc is permitted provenance (§11.3) — no finding.
    f = run("ARCHITECTURE-X", "# t\n\n## 1. a\n\nPer PROPOSAL-FOO §2 we do this.\n",
            set(), source_class="arch-doc",
            namespace={"ARCHITECTURE-X", "PROPOSAL-FOO"})
    assert not [x for x in f if x.cls in ("leak", "dangling")], f


@case
def true_dangling_when_absent_everywhere():
    f = run("EXTENSION-X", "# t\n\n## 1. a\n\nSee EXTENSION-GHOST §3.\n",
            set(), namespace={"EXTENSION-X"})
    assert [x for x in f if x.cls == "dangling"], f


@case
def stale_section_when_doc_resolves_but_section_absent():
    target = address.build_doc("EXTENSION-Y", FAKE, text="# t\n\n## 1. a\n")
    f = run("HOST", "# t\n\n## 1. a\n\nSee EXTENSION-Y.md §9.9 here.\n",
            set(), docs={"EXTENSION-Y": target})
    assert [x for x in f if x.cls == "stale-section"], f


@case
def chained_sec_carries_antecedent():
    f = run("HOST", "# t\n\n## 1. a\n\nSee EXTENSION-X.md §5 / §5.2 here.\n",
            {"EXTENSION-X"})
    # both §5 and §5.2 resolve in EXTENSION-X (built with those) -> conformant,
    # and neither should be misread as a bare-internal self-ref.
    assert not [x for x in f if x.cls == "bare-internal"], f


@case
def path_prefixed_intent_is_leak():
    # A dir-prefixed PROCESS-artifact citation is NOT a filesystem-artifact
    # exemption — it is a §11.3 leak the `/` prefix used to swallow.
    f = run("EXTENSION-X", "# t\n\n## 1. a\n\nPer `explorations/EXPLORATION-FOO.md §1` here.\n",
            set(), source_class="canonical-spec",
            namespace={"EXTENSION-X", "EXPLORATION-FOO"})
    assert [x for x in f if x.cls == "leak"], f


@case
def path_prefixed_real_spec_still_exempt():
    # A dir-prefixed citation to a REAL spec stays exempt (it is a genuine path).
    f = run("HOST", "# t\n\n## 1. a\n\nAt `core/specs/EXTENSION-X.md §5.2` line.\n",
            {"EXTENSION-X"})
    assert not [x for x in f if x.cls in ("leak", "drift", "dangling")], f


@case
def forward_planned_permitted():
    # §11.4 Forward: a spec-shaped, author-marked (planned) citation to a not-yet-
    # landed sibling extension is permitted — not a dangling finding.
    f = run("EXTENSION-X", "# t\n\n## 1. a\n\nSee EXTENSION-GHOST.md §3 (planned) here.\n",
            set(), namespace={"EXTENSION-X"})
    assert not [x for x in f if x.cls == "dangling"], f


@case
def forward_marker_does_not_mask_process_leak():
    # The (planned) marker only forgives spec-shaped names; a process artifact
    # cited as (planned) is still a §11.3 leak.
    f = run("EXTENSION-X", "# t\n\n## 1. a\n\nPer PROPOSAL-FOO §2 (planned) here.\n",
            set(), source_class="canonical-spec",
            namespace={"EXTENSION-X", "PROPOSAL-FOO"})
    assert [x for x in f if x.cls == "leak"], f


@case
def section_style_paragraph_heading_resolves():
    # `## §N` headings (used by EXTENSION-ROUTE / EXTENSION-RELAY) must register as
    # sections, else valid external citations to them read as stale-section.
    target = address.build_doc("EXTENSION-RLY", FAKE,
                               text="# t\n\n## §3 Types\n### §3.1 Forward\n#### §3.1.1 Next-hop\n")
    assert "3.1.1" in target.sections, target.sections
    f = run("HOST", "# t\n\n## 1. a\n\nNo RELAY to receive (EXTENSION-RLY.md §3.1.1).\n",
            set(), docs={"EXTENSION-RLY": target})
    assert not [x for x in f if x.cls == "stale-section"], f


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("  ok   %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("  FAIL %s — %s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERR  %s — %r" % (fn.__name__, exc))
    print("\n%d/%d invariants pass" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

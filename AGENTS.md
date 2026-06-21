# entity-system-arch-tools — AGENTS.md

Read **AGENTS-STANDARD.md** first. This file adds entity-system-arch-tools specifics.

## Overview

The **publishable spec toolkit** — a single CLI that reads, analyzes, and gates the
Entity specification corpus: structural trees, catalogs, the corpus dependency graph,
the §11 addressing validator, and the release-readiness gates (naming + standards).
One functional core (`spec-tool/model.py`), with commands as pathways into it.

This is one slice of the architecture three-way split: it is **tooling over the spec**,
not the spec. It carries **no spec corpus of its own** — the gates and readers run
against a corpus you point them at (`--root` / a bind-mount), so the tool and the spec
repos it lints evolve independently and version-decoupled. The public repo name is
`entity-system-arch-tools` (it was split out from the former `entity-core-architecture`).

## Setup / environment

- **Stdlib-only Python 3.11+** — no `pip install`, no third-party deps. The CLI imports
  only its own sibling modules.
- **Hermetic via `make` + `podman`** (host needs only `make` + `podman`, per
  AGENTS-STANDARD). One container image (`spec-tool/Containerfile`, `python:3.12-slim`);
  the gates run inside it. `make build` builds the image; `make check-podman` runs it.
- You can also run host-native against `python3` (`make check`, `make tree`, …).

## Build & test

All targets are in `Makefile` (run `make help` for the live list):

```bash
make check                 # run both gates locally (style + standards), host python3
make check-podman          # same, hermetic (one container); CORPUS=<path> to point at a corpus
make style                 # naming gate only
make standards             # release-readiness gate only
make tree SPEC=<spec.md>   # structural node tree of one spec (reader)
make render SPEC=<spec.md> # every catalog of one spec, --what all (reader)
make topology              # corpus dependency graph (reader)
make config                # print the resolved config
make parity                # unified CLI == golden fixtures (regression oracle)
make compile               # byte-compile the spec-tool package (sanity)
make build                 # build the podman image (entity-spec:latest)
make clean                 # remove the image
```

- **Gates exit non-zero on violations** (`check`, `style`, `standards`); **readers always
  exit 0** (`tree`, `render`, `topology`, `config`).
- `make parity` is the **regression oracle** — `spec-tool/tests/parity.sh verify` replays
  22 fixed cases under `spec-tool/tests/golden/` and fails on any byte/exit drift. **Run it
  after any change that could alter tool output.** Only re-baseline (`parity.sh capture`)
  for an intended, reviewed output change.
- `python3 spec-tool/tests/address_selftest.py` runs the `address` behavior invariants
  (`address` is deliberately **not** in the parity set — its output changes every
  correction pass, so its contract is pinned by the self-test instead).

## The CLI (`spec`)

Run as `python3 spec-tool/cli.py <subcommand> …` (or via the `make` targets above).
`cli.py` is the one entry point; each subcommand delegates to its module.

| command | purpose |
|---|---|
| `spec tree <file>` | structural tree of one spec (reader) |
| `spec render <file>` | catalogs of one spec (reader) |
| `spec topology [ROOT]` | corpus dependency graph (reader) |
| `spec address [ROOT]` | §11 addressing validator + worklist emitter (read-only) |
| `spec standards` | release-readiness / standards gate |
| `spec style` | naming-convention gate |
| `spec check` | run both gates (non-zero exit if either fails) |
| `spec config [CONFIG.toml]` | print the resolved config |

## Project structure (`spec-tool/`)

- `cli.py` — the one entry point; subcommands delegate to the modules.
- `model.py` — the parser: Node tree + Blocks + refs + §-attribution API (the shared core).
- `render.py`, `topology.py` — readers over the model + corpus.
- `address.py` — §11 addressing validator; emits self-contained finding packets for the
  correction pipeline. **Read-only — it never edits.**
- `standards.py`, `style.py` — the gates (non-zero exit on violations); `style` keeps its
  own lexer.
- `config.py` + `config.default.toml` — single source of truth (roots, excludes,
  vocabulary, scopes, policy). A different corpus is a different copy of this file
  (`spec --config FILE`). Lines tagged `# DELTA(phase2)` mark inconsistencies preserved
  byte-for-byte on purpose (the Phase-1 refactor is provably non-breaking).
- `Containerfile` — the one hermetic image.
- `tests/parity.sh` + `tests/golden/` — the regression oracle (output-pinning).
- `tests/address_selftest.py` — address invariant checks (behavior-pinning).

See `spec-tool/README.md` for the full module map and command flags.

## Boundaries — do NOT modify

- **This is tooling over the spec — it reads the spec, it does not define it.** The Entity
  spec corpus is upstream and lives in the spec repos; do not add spec content, wire
  formats, or normative rules here. Bugs in what the tool *reports* are tool bugs; gaps in
  what the spec *says* route upstream (AGENTS-STANDARD §Working across the polyrepo).
- **No spec corpus in this repo.** Don't vendor specs in; point the gates at a corpus via
  `--root` / the `CORPUS=` bind-mount. `make config` against this bare repo is a smoke run
  (zero files in scope) — that's expected, not a failure.
- **`spec-tool/tests/golden/` is frozen test data**, captured from the legacy standalone
  tools. Do not hand-edit it to "fix" stale strings or paths inside the fixtures — that is
  the parity oracle; only `parity.sh capture` (a reviewed re-baseline) may regenerate it.
- **`spec address` is read-only by contract** — keep it that way; it analyzes and emits
  worklists, it never edits the corpus.

## Commit & PR

Default branch **`master`**; DCO sign-off required — see AGENTS-STANDARD.

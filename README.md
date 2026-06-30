# Entity System Architecture Tools

**The publishable spec toolkit.** A single CLI that reads, analyzes, and gates the Entity
specification corpus — structural trees, catalogs, the corpus dependency graph, the §11
addressing validator, and the release-readiness gates (naming + standards). It ships as its own
repo, decoupled from any spec version, so the spec repos and the tool that lints them evolve
independently.

> **No system usage.** Like every repo in the project, the toolkit runs **hermetically via
> `make` + `podman`** — there is no `pip install`, no system-Python dependency. One container
> image; the gates run inside it.

## What it does

One functional core, commands as pathways into it:

| command | purpose |
|---|---|
| `spec tree <file>` | structural tree of one spec (reader) |
| `spec render <file>` | catalogs of one spec (reader) |
| `spec topology` | corpus dependency graph (reader) |
| `spec address` | §11 addressing validator |
| `spec standards` | release-readiness / standards gate |
| `spec style` | naming-convention gate |
| `spec check` | run both gates (exits non-zero on violations) |

Stdlib-only Python; the reader commands always exit 0, the gates exit non-zero on violations.

## Usage

```
make check            # run both gates locally
make check-podman     # run both gates hermetically (one container)
make topology         # corpus graph
make render SPEC=…    # catalogs for one spec
```

It lints a **target spec repo** (`entity-core-protocol` / `entity-system-architecture`), supplied
as the corpus root. (Standalone-operation note: the root is taken from the target mount/`--root`,
not the tool's own location.)

## License

Tooling is code: **Apache-2.0** (the project's code license). Distinct from the spec corpora it
operates on (which are dual-licensed CC-BY-ND-4.0 / Apache-2.0).

---

## Supporting the project

This project is developed in the open. If it's useful to you, the best support is
to use it, report issues, and contribute back — see
[CONTRIBUTING.md](CONTRIBUTING.md).

To support the work directly, see the project's funding page.

# entity-system-arch-tools — the spec toolkit, one tool with command pathways.
#
# Everything routes through the unified CLI (spec-tool/cli.py); the old
# spec-* scripts are retired (parity-proven equivalent — see
# spec-tool/tests/parity.sh). This is a standalone tool repo: it carries no spec
# corpus itself — point the gates at a corpus you bind-mount (see check-podman).
#
#   make check                 run both gates locally (style + standards)
#   make check-podman          run both gates hermetically (one container)
#   make style                 naming gate only
#   make standards             release-readiness gate only
#   make tree SPEC=<spec.md>   structural tree of one spec
#   make render SPEC=<spec.md> all catalogs of one spec
#   make topology              corpus dependency graph
#   make config                print the resolved config
#   make parity                verify the unified CLI matches the golden fixtures

PYTHON ?= python3
SPEC   ?=
IMAGE  ?= entity-spec:latest
CLI    := spec-tool/cli.py

# This standalone repo IS the tool root. The gates run against a spec corpus
# bind-mounted at /work; override CORPUS=<path> to point check-podman at one
# (defaults to this repo, which carries no specs — so check is a smoke run).
REPO   := $(CURDIR)
CORPUS ?= $(CURDIR)

.PHONY: help check check-podman style standards tree render topology config \
        parity compile build clean test lint fmt

help:
	@echo "the unified spec toolkit (one tool, command pathways)"
	@echo
	@echo "gates (exit non-zero on violations):"
	@echo "  check           spec check (style + standards), host python3"
	@echo "  check-podman    same, hermetic (one container)"
	@echo "  style           naming gate only"
	@echo "  standards       release-readiness gate only"
	@echo "readers (informational):"
	@echo "  tree SPEC=…     structural node tree"
	@echo "  render SPEC=…   every catalog (--what all)"
	@echo "  topology        corpus dependency graph"
	@echo "  config          print the resolved config"
	@echo "meta:"
	@echo "  parity          unified CLI == golden fixtures (regression oracle)"
	@echo "  compile         byte-compile the spec package (sanity)"
	@echo "ADR-0019 Tier-1 (over the tool's own code, stdlib-only):"
	@echo "  test            compile + parity + address self-test (the tool's suite)"
	@echo "  lint            byte-compile static check (no 3rd-party linter; alias of compile)"
	@echo "  fmt             no-op — stdlib-only, no vendored autoformatter"
	@echo "  NOTE: 'check' above is the SPEC gate (style+standards), not lint+test."

# --- gates ---
check:
	@$(PYTHON) $(CLI) check

style:
	@$(PYTHON) $(CLI) style

standards:
	@$(PYTHON) $(CLI) standards

# Hermetic: bind-mount the repo read-only at /work and run the CLI from there,
# so REPO_ROOT and sibling imports resolve against the live tree (image stays
# corpus-free). `spec check` exits non-zero if either gate fails.
check-podman: build
	podman run --rm -v $(CORPUS):/work:ro,Z -w /work $(IMAGE) /opt/spec-tool/cli.py check

build:
	podman build -t $(IMAGE) -f spec-tool/Containerfile .

# --- readers ---
tree:
	@test -n "$(SPEC)" || (echo "set SPEC=<spec.md>"; exit 2)
	@$(PYTHON) $(CLI) tree $(SPEC)

render:
	@test -n "$(SPEC)" || (echo "set SPEC=<spec.md>"; exit 2)
	@$(PYTHON) $(CLI) render $(SPEC) --what all

topology:
	@$(PYTHON) $(CLI) topology

config:
	@$(PYTHON) $(CLI) config

# --- meta ---
parity:
	@spec-tool/tests/parity.sh verify

compile:
	@$(PYTHON) -m py_compile \
		spec-tool/cli.py spec-tool/model.py spec-tool/render.py spec-tool/topology.py \
		spec-tool/standards.py spec-tool/style.py spec-tool/config.py && echo "✓ spec-tool package compiles"

# --- ADR-0019 Tier-1 verbs (over the tool's OWN code) -----------------------
# This is a stdlib-only Python tool (no third-party deps — see AGENTS.md), so
# there is no clippy/ruff/black to wrap. `lint` is the byte-compile static check;
# `test` is the tool's own self-test suite (parity oracle + address invariants);
# `fmt` is an honest no-op (nothing vendored to autoformat). NOTE: the `check`
# target above is this tool's DOMAIN gate (spec style+standards), not the ADR's
# `check = lint+test` — reconciling that name is a separate decision.
test: compile parity
	@$(PYTHON) spec-tool/tests/address_selftest.py

lint: compile

fmt:
	@echo "arch-tools is stdlib-only (no vendored autoformatter); sources are"
	@echo "hand-formatted. 'make lint' runs the byte-compile static check."

clean:
	-podman rmi $(IMAGE)

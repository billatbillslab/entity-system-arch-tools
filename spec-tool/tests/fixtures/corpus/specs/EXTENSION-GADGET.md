# Example Gadget Extension

**Version**: 1.0
**Status**: Experimental

An extension spec that is missing its `**Depends**:` field, so the standards
gate flags `depends-missing` (an error). Its status value is not one of the
canonical values, so it also flags `header-status-unknown` (a warning). It
carries deliberate naming violations for the style gate.

## 1. Naming Surface

The kebab namespace rule is violated by these type paths:

```
system/bad_path/snake-root := { kind: "bad" }
entity/mixed-name_token := { kind: "bad" }
```

A conforming path looks like `system/good-path/leaf-entry`.

## 2. Citations

This section cites a document that does not exist in the corpus, which
topology reports as a dangling citation: EXTENSION-MISSING §4.

It also cites its base both as ENTITY-CORE-PROTOCOL and as
ENTITY-CORE-PROTOCOL.md, which topology reports as citation-form drift.

Finally it cites a section that does not exist in the base, which topology
reports as a stale section ref: ENTITY-CORE-PROTOCOL §9.9.

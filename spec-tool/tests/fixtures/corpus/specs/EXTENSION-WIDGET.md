# Example Widget Extension

**Version**: 1.0
**Status**: Active
**Depends**: ENTITY-CORE-PROTOCOL

A clean, conforming extension spec: it has a title, a bare version, a known
status, and a declared depends field, so the standards gate reports nothing
for it. It cites its base so topology draws an edge.

## 1. Overview

The widget extension layers on ENTITY-CORE-PROTOCOL §3. It also references
ENTITY-NATIVE-TYPE-SYSTEM §3 for its field types.

## 2. Behaviour

A widget MUST resolve `entity/grant/entry` before acting. See
ENTITY-CORE-PROTOCOL §2.1 for the addressing model.

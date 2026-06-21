# Example Core Protocol Specification

**Version**: 1.0
**Status**: Normative

The synthetic root spec for the spec-tool test corpus. It is deliberately the
most-cited document so the topology analyzer resolves it as the corpus root.
This text carries no calendar dates and no internal document names — it exists
only to exercise the structural grammar.

## 1. Introduction

### 1.1 Purpose

This document defines the example core protocol used by the toolkit tests.
A conforming peer MUST honour the bootstrap rules in §2. See
ENTITY-NATIVE-TYPE-SYSTEM §3 for the type registry these rules build on.

### 1.2 Scope

The protocol covers entity addressing and the core wire shape. It does not
cover extensions; those live in their own EXTENSION-WIDGET §1 documents and
MAY layer on top per SPECIFICATION-FORMAT §8.

## 2. Bootstrap

A peer SHALL acquire a root entity before serving requests. The root entity
is content-addressed and its type path is `system/entity/root-entry`.

```
verify_request(peer_id, payload):
    if not valid(peer_id):
        return DENY
    return ALLOW
```

### 2.1 Addressing

An address dereferences a path under a type root. The canonical example is
`system/capability/grant-entry`, which a peer MUST resolve before granting.

| field | meaning |
| --- | --- |
| peer_id | the canonical host identifier |
| content_hash | the address of the payload |

### 2.2 Wire Shape

A core message is a small map. Values use snake or kebab style consistently;
mixed forms such as `bad_mixed-token` are non-conforming and the style gate
flags them.

```
root-entry := { type: "system/entity/root-entry", id: peer_id }
```

## 3. Conformance

A conforming implementation MUST pass every requirement in §2 and SHOULD
expose the addressing surface from §2.1. See EXTENSION-WIDGET §2 for an
example of an extension that depends on this section.

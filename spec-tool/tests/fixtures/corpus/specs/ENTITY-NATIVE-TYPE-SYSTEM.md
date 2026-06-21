# Example Native Type System Specification

**Version**: 1.0
**Status**: Normative

The synthetic type-heavy spec. It defines several example types so the tree
and render readers have a rich type catalog to lift. No dates, no internal
document names appear here.

## 1. Introduction

### 1.1 Purpose

Define the example primitive and composite types referenced by
ENTITY-CORE-PROTOCOL §2. Every type path uses the kebab namespace style.

### 1.2 Notation

A type is declared with `:=` inside a fenced block.

## 2. Primitive Types

A primitive type is a leaf with no fields of its own.

```
primitive/byte-string := { kind: "bytes" }
primitive/unsigned-int := { kind: "uint" }
primitive/bool-flag := { kind: "bool" }
```

### 2.1 Primitive Registry

| type | kind |
| --- | --- |
| primitive/byte-string | bytes |
| primitive/unsigned-int | uint |
| primitive/bool-flag | bool |

## 3. Composite Types

A composite type composes other types by reference.

```
system/type/name := { fields: { label: primitive/byte-string } }
system/tree/path := { fields: { segments: primitive/byte-string } }
entity/grant/entry := { fields: { holder: peer_id, scope: primitive/byte-string } }
```

### 3.1 Composition Rules

A composite type MUST reference each field type by its canonical path. A field
key uses snake style, e.g. `field_label`, while a type path uses kebab style.
See ENTITY-CORE-PROTOCOL §2.1 for the addressing model these types rely on.

## 4. Constraints

A constraint narrows a type. Constraints MUST NOT widen a type. The example
constraint `non_empty` requires at least one element.

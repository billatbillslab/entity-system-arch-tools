# Example Specification Format

**Version**: 1.0
**Status**: Normative

The synthetic meta-spec: it describes the shape every other spec in this
corpus follows. As the rulebook, it is excluded from the standards/style gates
(it would quote its own counter-examples), but it remains in the topology
namespace so its citations resolve. No dates or internal document names here.

## 1. Header

Every spec opens with an H1 title, then a `**Version**:` and `**Status**:`
field. See ENTITY-CORE-PROTOCOL §1 for a conforming example.

## 2. Body

The body is structural markdown: numbered headings, fenced blocks, and tables.

## 8. Extensions

An extension spec declares a `**Depends**:` field naming its base. See
EXTENSION-WIDGET §1 for a conforming extension and ENTITY-NATIVE-TYPE-SYSTEM §3
for the type model.

### 8.1 Depends

The `**Depends**:` field is REQUIRED for any document whose name begins with
the extension prefix.

## 11. Citations

A citation names a canonical document and an optional section, e.g.
ENTITY-CORE-PROTOCOL §2.1. A prose nickname is not a citation.

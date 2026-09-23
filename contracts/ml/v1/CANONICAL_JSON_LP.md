# CANONICAL_JSON_LP_V1

This binary codec is the nested-field component of recommendation serving
contract v2. One value has a one-byte ASCII tag:

| Value | Tag | Following bytes |
| --- | --- | --- |
| null | `n` | none |
| false | `f` | none |
| true | `t` | none |
| signed int64 | `i` | eight-byte two's-complement big-endian integer |
| NFC UTF-8 string | `s` | four-byte unsigned big-endian byte length, then bytes |
| array | `a` | four-byte element count, then each child as four-byte byte length plus full tagged child |
| object | `o` | four-byte member count, then each key as four-byte UTF-8 byte length plus ASCII bytes, followed by its value as four-byte byte length plus full tagged child |

Object members sort by raw ASCII key bytes. Every object path must have an
explicit schema-declared allowed-key set, including nested paths (`$.field`)
and array element paths (`$.field[]`). Unknown keys, duplicate JSON keys,
fractional/exponent numbers, non-NFC strings, non-ASCII schema keys and values
outside signed int64 fail before SQL. Bounds: depth 12, 4,096 members per
container, 4,096 bytes per string/key, and 65,536 bytes for the encoded value.
The parent item schema still decides required fields; this codec checks allowed
members and byte identity.

The checked-in golden fixture `tests/fixtures/ml/canonical-json-lp-v1.json`
covers every tag, int64 endpoints, NFC, empty containers and a nested map. For
`{"a":[true,null],"b":"é"}` its exact LP bytes have SHA-256
`e10ff5de64836563590aff6e63efd5833105f818a759260b83c3927e1ebb873c`.

The Python pre-SQL codec is implemented. The PostgreSQL recursive encoder,
item/set/decision hashes, and serving-v2 cutover are pending. No current P11
serving or persistence path uses LP yet.

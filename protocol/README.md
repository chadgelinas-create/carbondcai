# Protocol Source of Truth

Status: Phase 3 candidate implementation; production profile intentionally blocked  
Schema: `1.0.0`  
Generator: `1.0.0`

This directory is the only manually maintained source for protocol points selected for
implementation. It does not make a vendor row, model, firmware, endpoint, timeout, or command
safe merely by recording it. Lifecycle and production-profile validation preserve that boundary.

## Layout

| Path | Purpose | May feed production? |
| --- | --- | --- |
| `schema/protocol-v1.schema.json` | Versioned document shape and allowed values | Schema only |
| `examples/` | Synthetic, non-device examples | No |
| `devices/` | BESS and converter candidate maps with exact source provenance | Only after explicit approval |
| `reference/` | Quarantined Skid 1 reference material and known conflicts | No |
| `blocked/` | Missing-interface manifests without invented points | No |
| `golden-vectors.json` | Hand-calculated primitive codec vectors | Test oracle only |
| `../generated/protocol/` | Deterministic review artifacts; never hand-edit | Candidate artifacts are not production contracts |

Files use JSON-compatible YAML: JSON is a strict subset of YAML 1.2. This deliberately provides
unambiguous strings/numbers, duplicate-key rejection, and deterministic standard-library parsing
without another runtime dependency. Use JSON syntax even though the controlled extension is
`.yaml`.

## Document and field contract

Every document declares:

- schema version, kind, stable ID, title, and lifecycle;
- device identity, role, protocol role, endpoint status, and independent model, firmware, and map
  applicability;
- controlled source filename, revision, classification, and SHA-256;
- review authority, named reviewer, result, date, and evidence;
- normalized points and/or explicit blockers.

Every point declares:

- stable logical ID and Logix-compatible generated name, separate from vendor wording;
- lifecycle, Modbus service, direction, and access;
- original display address plus notation/base/register space and canonical zero-based wire offset;
- primitive, signedness, word count, byte/word order, bit definitions, and atomic grouping;
- exact decimal scale/offset, unit, range, sentinel values, and rounding;
- poll class, timeout, stale threshold, retry behavior, and timestamp expectations;
- quality outcomes for sentinels, communications failure, and out-of-range values;
- command prerequisites, acknowledgement, timeout, safe value, write spacing, and ramp when
  writable;
- exact source hash, sheet, row, cells, raw vendor name, and normalization notes.

Unknown values are recorded explicitly. Omitting a required field is an input error. There are no
wire-semantic defaults.

## Address and value conventions

- Canonical register offsets are zero-based PDU offsets in `0..65535`.
- Display notation and base are preserved; all `+1`/`-1` conversion is confined to
  `skid2.protocol.address`.
- Multiword points occupy their complete span. Unexplained overlaps fail.
- Generic codecs never hide a device/domain sign conversion.
- Internal power direction is positive for discharge only at the explicitly named vendor adapter;
  the Sinexcel source convention remains positive for charge.
- Decimal conversion uses `Decimal`; quantization behavior is `reject`, `half_even`, or `half_up`.
- Sentinel raw values decode to configured invalid quality, not plausible engineering values.

## Profiles and promotion

- `candidate` validates imported/candidate review work but does not authorize production use.
- `reference` validates quarantined legacy material while preserving its exclusion.
- `production` fails closed unless the document, device, point, applicability, wire/address/
  engineering/timing metadata, endpoint, command semantics, and blockers are all explicitly
  approved.

Import and generation cannot promote a lifecycle state. Production promotion requires recorded
model/firmware/map applicability, complete wire and timing metadata, approved endpoint and command
semantics, no unresolved blocker manifests, and the named engineering/vendor reviews required for
the affected device.

## Maintainer workflow

Run the complete containerized protocol gate:

```bash
make protocol-check
```

Useful review commands inside the locked quality image or a supported `uv` environment:

```bash
python -m skid2.protocol validate protocol/devices/*.yaml protocol/blocked/*.yaml
python -m skid2.protocol validate protocol/reference/*.yaml --profile reference
python -m skid2.protocol generate
python -m skid2.protocol check-drift
```

An authorized workbook may be inspected without Excel:

```bash
python -m skid2.protocol import /controlled/path/source.xlsx \
  --adapter wenergy-bess-v1.18
```

Available adapters are `wenergy-bess-v1.18`, `sinexcel-v126-ro`, and
`sinexcel-v126-rw`. The default command prints only a non-confidential summary. Supplying
`--output` writes raw staging data and therefore requires an authorized controlled destination.
Import verifies the original filename/hash/size/mtime before and after reading, never edits the
workbook, and always labels rows `imported`.

To change a candidate point:

1. verify the authorized source identity and applicability;
2. import/review the exact row and record provenance;
3. edit only the controlled YAML;
4. validate candidate and production profiles;
5. regenerate and review every changed target;
6. run `make protocol-check`;
7. obtain the authorities required by the governance policy.

Never edit `generated/protocol/` to fix a source problem.

## Compatibility policy

Schema versions follow semantic versioning:

- patch: clarification or validator correction that accepts the same valid source shape;
- minor: backwards-compatible optional capability with explicit behavior;
- major: removed/renamed fields, changed meaning/defaults, or any compatibility break.

Generators reject unsupported schema versions. A version change must update the schema, parser,
validator, example, fixtures, generator version as applicable, ADR/governance record, and all
generated artifacts in one review. Old approved inputs remain immutable or are explicitly
superseded; they are never silently reinterpreted.

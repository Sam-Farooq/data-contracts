# data-contracts

A linter for schema changes. Schemas live as files, the library compares two of
them, and the CLI fails a build when a change breaks the direction its subject
promised. It also holds a registry of consumers, so the report names which ones
break rather than only that something does.

One runtime dependency, pydantic. No broker, no registry server, no schema
service: the question is whether two files in a working tree agree, and that is
answerable in a pull request before anything is published.

## The question a policy cannot answer

`payments.transaction` promises backward compatibility. A candidate drops its
`channel` enum. The policy allows that, because removing a field cannot break a
reader on the new schema: the field is not in its field list, so whatever the
writer sent is skipped.

```bash
contractlint check payments.transaction \
  --proposed proposals/payments.transaction.v4-drop-channel.avsc --consumers
```

```
payments.transaction: policy backward_transitive, requires backward

  ok    v1 -> v4        holds backward                5 changes
  ok    v2 -> v4        holds backward                3 changes
  ok    v3 -> v4        holds backward                1 change

pass: backward holds against every version compared

2 of 4 consumers break

  BREAKS  fraud-scoring [risk]              pinned v3, lagging, projects
          forward from payments.transaction v3: field_removed_required at channel
            enum{CARD,TRANSFER,DIRECT_DEBIT}, no default, removed
  BREAKS  warehouse-loader [data-platform]  pinned v3, replay, reads everything
          forward from payments.transaction v3: field_removed_required at channel
            enum{CARD,TRANSFER,DIRECT_DEBIT}, no default, removed
  ok      settlement-recon [payments-ops]   pinned v2, lagging, projects
          passed over: field_removed_required at channel, which it does not read
  ok      card-notifications [growth]       pinned v3, lockstep, projects

fail: fraud-scoring, warehouse-loader
```

The policy is not wrong. A backward policy protects the consumer that upgrades
before the producer, and says nothing about the one that has not upgraded yet.
Four consumers read that subject, two of them break, and the two that survive
survive for different reasons: one does not read the field, and the other needs
the other direction because it deploys with the producer.

A verdict of "backward compatible" is the answer to a question nobody asked out
loud. This repository makes the question explicit.

## Backward and forward are directions, not degrees of safety

Both words are useless on their own, and almost every argument about a schema
change is two people using one of them to mean the other.

| | reader | writer | the question |
|---|---|---|---|
| **backward** | new schema | old schema | can I read what is already written? |
| **forward** | old schema | new schema | can what is deployed read what I am about to write? |

Everything in the rule table follows from one walk. A reader goes through its own
fields, takes a value from the writer when the names match, falls back to its own
default when they do not, and fails when there is neither. So:

- adding a field with a default breaks nothing, and without one it breaks
  backward, because the new reader needs a value the old writer never sent
- removing a field breaks forward, and only when the old schema gave that field
  no default, because the old reader has lost both the value and its fallback
- widening a type breaks forward and narrowing breaks backward, because
  promotion runs one way: `int` reads as `long`, and `long` does not read as `int`
- adding an enum symbol breaks forward and removing one breaks backward, unless
  the reader on the other side declares a fallback symbol
- changing a default breaks nothing and changes what a missing value means, which
  is reported and is not a failure unless you ask for `--strict`
- a logical type is an annotation over a physical one, so resolution never
  objects to one: adding it breaks backward, removing it breaks forward, and
  changing the unit under it breaks both without failing to parse

`contractlint rules` prints all of it, which is the version that cannot go stale.

## The rule table

32 rules. `--strict` is for the changes where resolution keeps working and a
reader observes a value it would not have seen before.

| change | breaks | --strict |
|---|---|---|
| container_kind_changed | backward, forward | |
| decimal_precision_narrowed | backward | |
| decimal_precision_widened | forward | |
| decimal_scale_changed | backward, forward | |
| default_added | nothing | |
| default_changed | nothing | backward |
| default_removed | nothing | |
| doc_changed | nothing | |
| enum_fallback_added | nothing | |
| enum_fallback_removed | nothing | |
| enum_symbol_added | forward | |
| enum_symbol_added_with_fallback | nothing | forward |
| enum_symbol_removed | backward | |
| enum_symbol_removed_with_fallback | nothing | backward |
| enum_symbols_reindexed | backward, forward | |
| field_added_optional | nothing | |
| field_added_required | backward | |
| field_removed_optional | nothing | |
| field_removed_required | forward | |
| field_renamed_with_alias | forward | |
| field_reordered | nothing | |
| likely_rename | nothing | |
| logical_type_added | backward | |
| logical_type_changed | backward, forward | |
| logical_type_removed | forward | |
| logical_unit_changed | backward, forward | |
| nullability_added | forward | |
| nullability_removed | backward | |
| type_changed | backward, forward | |
| type_interchangeable | nothing | |
| type_narrowed | backward | |
| type_widened | forward | |

Five of those are worth more than a row.

**`logical_unit_changed`** is the one row where a clean resolution is the problem.
`timestamp-millis` and `timestamp-micros` are both a `long`, so every structural
check passes and a reader on the wrong one is out by a factor of 1000: a 2026
timestamp read as the other unit lands in 1970 or in the far future, populated
and plausibly typed. There is no reader that survives it and no encoding under
which it is harmless, which is why it breaks both directions outright instead of
sitting behind `--strict`. The unit is the trailing word, so
`local-timestamp-millis` becoming `timestamp-millis` is a changed family rather
than a changed unit, and `decimal` carries its scale and precision as part of
the type: moving the point leaves the bytes the same width and the number wrong
by a power of ten.

**`enum_symbols_reindexed`** is not about resolution at all. The binary encoding
writes an enum as the index of its symbol, so moving one remaps every value
already written. Nothing errors, and records decode to the wrong symbol. It is
the only rule here scoped to a dialect: JSON Schema has no binary form and writes
the symbol itself, so a reorder there is nothing. Appending a symbol leaves every
existing index alone, which is why appending is a different row from inserting.

**`likely_rename`** exists because a rename without an alias is not detectable.
From two files it is one field leaving and another arriving, and that is how it is
reported. The combined verdict comes out right anyway: the removal costs forward,
the addition costs backward, so an unaliased rename of a required field breaks
both. The hint is there for the pairing nothing else catches. Rename a required
field to a name that carries a default and resolution succeeds in the backward
direction, with every record ever written reading as the default.

**`nullability_removed`** is how an optional property becoming required shows up
in the JSON Schema dialect, since the loader maps an optional property to a
nullable field with a null default.

**`type_interchangeable`** covers `string` and `bytes`, which promote to each
other. Treating that pair as a widening would report a forward break that does
not exist.

## The case a pairwise check cannot see

`payments.transaction` is on `backward_transitive`, which compares a proposal
against every retained version rather than the newest one. That costs a
comparison per version, and it buys one failure:

```bash
contractlint check payments.transaction \
  --proposed proposals/payments.transaction.v4-fee-returns.avsc
```

```
  FAIL  v1 -> v4        holds neither                 6 changes
  ok    v2 -> v4        holds backward, forward       4 changes
  ok    v3 -> v4        holds backward, forward       1 change

  v1 -> v4
  [both]      type_changed                      fee_minor
                long -> string
                no promotion exists between the two types in either direction
      and 5 other changes that cost nothing

fail: backward required, and payments.transaction v1 does not hold it
```

v1 had `fee_minor` as a required long. v2 dropped it. The candidate brings it
back as a string with a default. Against v3 that is a new optional field, and
every pairwise checker says yes. A reader on the candidate handed a v1 record
finds a long where it wants a string.

The three transitive policies are the only ones that look. The price is that a
subject on `backward_transitive` can never reuse a field name for a different
type, which is a real constraint and the reason the pairwise policies still
exist.

## The registry is a directory

```
registry/
  consumers.toml                     who reads what, and when they upgrade
  payments.transaction/
    subject.toml                     compatibility = "backward_transitive"
    v1.avsc  v2.avsc  v3.avsc        Avro-style records
  crm.account/
    subject.toml                     compatibility = "full"
    v1.schema.json  v2.schema.json   JSON Schema, draft 2020-12 subset
  search.query_log/
    subject.toml                     compatibility = "backward"
    v1.schema.json  v2.schema.json
```

3 subjects, 7 versions, 6 consumers, which is what `contractlint validate`
counts. **Every schema in `registry/` and `proposals/` is hand authored for this
repository.** Nothing was exported from a broker, a registry server or a
producer, no payload has ever been serialized against any of it, and the comment
at the top of each `subject.toml` says so. The schemas are there so the rules
have something concrete to be checked against.

A version number missing from a subject is an error rather than a warning. A
transitive policy would otherwise check a proposal against a history with a hole
in it and report success.

The seven committed versions are not all clean transitions, deliberately.
`search.query_log` widened `latency_ms` from integer to number, which cost it
forward compatibility, which is why that subject is declared backward only. A
registry where every subject is on `full` has either no history or no honesty.

## consumers.toml carries the two things a schema cannot

```toml
[[consumer]]
name = "fraud-scoring"
subject = "payments.transaction"
version = 3
upgrade = "lagging"
owner = "risk"
reads = ["transaction_id", "account_id", "amount_minor", "currency", "occurred_at", "channel"]
```

`version` is where the consumer is pinned. `upgrade` is when it moves relative to
the producer, and it decides which direction that consumer needs:

| upgrade | what it means | what it needs |
|---|---|---|
| `lagging` | stays on its pinned schema while the producer moves ahead | forward, from its pin |
| `lockstep` | deploys with the producer, then drains what is in flight | backward, from its pin |
| `replay` | re-reads retained history with the new schema, and is not upgraded first | backward from every retained version, and forward from its pin |

`reads` defaults to `["*"]`, and the default is the honest one. A consumer that
hands the broker its whole pinned schema fails on any resolution failure, whether
or not the application touches the field. Listing fields is a claim that the
consumer resolves only those, which is true of a projecting reader and false
otherwise. A declared path covers everything under it, so a consumer reading
`merchant` is reached by a change to `merchant.category_code`, and one reading
`merchant.merchant_id` is not.

Breaking changes outside what a consumer reads are kept and printed as `passed
over` rather than dropped, because a near miss rests on that claim and whoever
reviews the change should see the claim it rested on.

Validation refuses a stale declaration: a consumer pinned to a version the
registry does not hold, or reading a field its pinned version does not define, is
an error. Both make the impact report wrong in the direction that matters, which
is under-reporting.

## What the loader decides for you

Two dialects normalize into one model, so the rules have one shape to reason
about and a dialect is a loader rather than a second rule table. JSON Schema does
not say everything Avro says, and the gaps are decisions. They are recorded on
the document and printed:

```bash
contractlint show search.query_log
```

```
the loader decided these, the file did not say:
  - JSON Schema integer is loaded as Avro long, since the dialect has no width
  - JSON Schema number is loaded as Avro double
```

The two that change results:

- **An optional property with no default becomes nullable with a null default.**
  A payload may omit it, so a reader has to cope with its absence. The
  consequence is that for an optional property this checker cannot see a
  nullability change, because the loader already made it nullable. For a required
  property it can.
- **`integer` becomes `long` and `number` becomes `double`.** JSON Schema has no
  integer width, so a subject in that dialect can never express `int` and never
  trips the int-to-long widening rule in either direction.

Both loaders refuse rather than guess. `$ref`, `oneOf`, `anyOf`, `allOf`, a
multi-branch union, an object with neither properties nor an
`additionalProperties` schema, a named type reference, and a `decimal` with no
precision or a scale past its own precision all raise with the path they gave up
on. A checker that silently ignores indirection compares two things
that are not the schemas.

## In a pipeline

```yaml
- uses: ./action
  with:
    registry: registry
    subject: payments.transaction
    proposed: proposals/payments.transaction.v4-drop-channel.avsc
```

The action is a composite action in `action/`, installing the package from its
own parent directory, so the same block works as `./action` from inside this
repository and as `<owner>/data-contracts/action@<ref>` from another one. Nothing
here is published to the Marketplace.

Its logic is `action/run.sh` rather than an inline `run:` block, which means it
runs on a laptop and that CI can prove the script works rather than hoping the
YAML does:

```bash
CONTRACTLINT_REGISTRY=registry bash action/run.sh
```

Inputs reach that script as environment variables and never as `${{ }}` inside a
shell line. A subject name is attacker controlled on a fork pull request, and
that interpolation is a command injection.

Exit codes, because this is only useful if a build can act on it:

| code | meaning |
|---|---|
| 0 | compatible under the policy asked for |
| 1 | a policy was violated, or a declared consumer breaks |
| 2 | refused: usage, an unreadable file, an unknown subject, a broken registry |
| 3 | `validate` found something wrong with the registry itself |

2 and 3 are not 1. A build that treats a refusal as a pass has no gate, because
an unparseable schema file would read as nothing to report, and that is the one
failure mode a schema checker cannot afford. `check` refuses outright when the
registry has errors rather than comparing whatever is left.

## Commands

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

contractlint validate                       # the registry itself: 3 subjects, 7 versions
contractlint check                          # every subject's latest version against its policy
contractlint check payments.transaction --verbose
contractlint diff payments.transaction 1 2  # what changed, and what it cost
contractlint show payments.transaction      # one version, normalized, with the assumptions
contractlint rules                          # the table above, from the source of truth
contractlint check --format json --consumers > contractlint-report.json
```

`--format json` is built by hand rather than dumped from the models. Every
direction in here is a set, and dumping one gives whatever order the interpreter
felt like, so two runs over one tree produced two different reports. Sorted lists
and sorted keys, so a build can diff it.

## Tests

```bash
pytest -q                 # 258 tests, no network, no broker, no service
ruff check . && ruff format --check .
```

49 of those are rows in one table in `tests/test_rules.py`: a concrete schema
pair, the change kinds it should produce, and the directions it should break.
There is a test asserting that every one of the 32 rules has at least one row,
because a rule nobody wrote a case for is a rule nobody has checked.

The rest covers what the loaders refuse, the transitive comparison, consumer
impact against the three committed candidates, registry validation, and the CLI's
four exit codes. Nothing opens a socket, because nothing in the design wants to.

## What it refuses to do

- **No payload validation.** This compares schemas. It cannot tell you whether
  the data conforms to either of them, and a registry that is fully compatible
  can still be fed records that do not match it.
- **No multi-branch unions.** `["null", "string"]` is supported and
  `["string", "long", "bytes"]` raises. Real union resolution needs per-branch
  matching, and a checker that silently picks one branch is worse than one that
  says no.
- **No named type references, no recursion, no `fixed`.** Each file is read on
  its own, so a type defined once and referenced by name elsewhere is not
  resolved. A `decimal` is therefore read over `bytes` and not over `fixed`, and
  `duration`, which only exists over `fixed`, is not read at all.
- **No Protobuf.** Field numbers rather than names change every rule in the
  table, which makes it a different tool rather than a third loader.
- **No history.** There is no state anywhere: no registry server, no id per
  schema, no record of what has been published. The working tree is the registry,
  which is what makes this runnable in CI and useless as a source of truth about
  what producers are actually writing.
- **No transport.** Nothing here knows about Kafka, and nothing here needs to.

## Where it can still be wrong

- **`reads` is a claim, not a measurement.** Nothing verifies that a consumer
  resolves only the fields it listed. A consumer that lists six fields and
  deserializes with its full pinned schema will be told it is safe when it is
  not. The conservative default exists for that reason, and a wrong `reads` list
  beats it.
- **`upgrade` is a declaration about deployment order.** It is the thing that
  decides which direction a consumer needs, and it is a sentence in a TOML file
  rather than anything observable.
- **A changed default is reported and not failed.** Resolution succeeds, and a
  reader that falls back now observes a different value. Calling that a
  compatibility break is a judgement rather than schema resolution, so it lives
  behind `--strict`.
- **A JSON Schema `format` is compared as if it bound the value.** The
  specification calls `format` an annotation, and a document may declare
  `date-time` while every validator in the path ignores it. A changed `format` is
  reported here as a changed meaning, which is right for a consumer that parses
  by it and strict for one that does not. The dialect has no second place to put
  the rule, and the alternative is the silence the Avro side used to have.
- **Decimal precision is read as range.** Avro resolves on the physical type and
  never on precision, so a reader handed more digits than it declared gets
  whatever its own decimal library does with them. Widening is reported as a
  forward break on that reading, which is stricter than the serializer. Scale is
  the one that is wrong in every library, and it breaks both directions.
- **A logical type over a named or recursive type is not seen at all**, because
  neither is resolved. What is compared is the annotation on a primitive, an
  array's items, or a map's values.
- **Enum reindexing is reported as breaking on the assumption of a binary
  encoding.** Under a JSON encoding on the Avro side it is harmless, and that
  case is not distinguished.

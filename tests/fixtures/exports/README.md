# Export format regression fixtures

All records are original **synthetic data**, created for this project. They are
not private exports, production incidents, or a copied third-party dataset.
The fixture data and authoring code use this project's MIT license. No exporter
implementation or third-party example data is vendored here.

| File | Physical records | Provenance |
| --- | ---: | --- |
| `duckdb-copy.ndjson` | 3 | Actual DuckDB **2.0.0** `COPY ... (FORMAT JSON, ARRAY false)` output from an in-memory table |
| `pandas-records.ndjson` | 3 | Actual pandas **3.1.0** `DataFrame.to_json(orient="records", lines=True, force_ascii=False)` output |
| `elasticsearch-bulk.ndjson` | 5 | Hand-authored synthetic actions/documents following the official Bulk request format; **not** captured from Elasticsearch |
| `mongodb-extended.ndjson` | 3 | Hand-authored synthetic Canonical/Relaxed Extended JSON documents; **not** produced by `mongoexport` |

`provenance.json` records the byte sizes, SHA256 hashes, producer versions, and
source URLs. Hashes identify these fixtures; they are not authenticity proofs.
Git text conversion is disabled for the `.ndjson` files. Elasticsearch's fixture
uses CRLF; the other fixtures use LF. Tests also exercise damaged records and
missing final terminators without changing the committed fixtures.

## Real exporter reproduction

From the project root, in a disposable Python 3.13 virtual environment:

```console
python -m pip install .
python -m pip install -r scripts/export-requirements.txt
python scripts/export_producer_smoke.py
```

The final command actually runs both pinned exporters, compares their bytes to
the two checked-in tool-generated fixtures, partitions both clean and deliberately
damaged variants, removes every original source, and restores from each bundle.
It prints versions, counts, and hashes, never record contents. To deliberately
refresh the two generated fixtures, append `--write-fixtures`; review and update
their entries in `provenance.json` when bytes change. This command does not replace
the two hand-authored fixtures. Producer packages are development-only extras;
the regular test matrix reads the committed fixtures without importing them.

The source rows include escaped CR/LF, quotes and backslashes, Unicode including
U+2028/U+2029, nested values, nulls, and exact large integers. The DuckDB table
contains `HUGEINT` values at the signed 128-bit limits. The pandas frame uses
`dtype=object` so nullable values do not first coerce integers to floats. This
tests the explicit export setup; it does not promise every pandas dtype or
exporter option preserves every source type.

## Source-derived format boundaries

Elasticsearch Bulk mixes action and source records. A `delete` has no source;
`index` and `create` do. Five valid JSON records here describe three actions.
The protocol requires a final LF and allows CRLF. JSONL quarantine works on
physical records: it **does not validate action/source pairing**, validate index
operations, or prepare a safe replay request. A missing final LF is accepted as
JSONL even though Bulk requires it. Removing a rejected document can leave its
action orphaned; tests explicitly demonstrate that limitation.

MongoDB Extended JSON encodes types using JSON objects such as `$oid`,
`$numberLong`, `$numberDecimal`, `$date`, and `$binary`. The strings `"NaN"` and
`"Infinity"` inside `$numberDouble` are valid JSON strings; bare numeric literals
`NaN` and `Infinity` are rejected. Acceptance verifies JSON syntax, not ObjectId,
decimal, date, binary, or MongoDB schema validity. Both Canonical and Relaxed
examples are covered; no MongoDB server or `mongoexport` compatibility claim is
made.

## Primary sources

- [DuckDB COPY options](https://duckdb.org/docs/2.0/sql/statements/copy),
  [DuckDB 2.0.0 distribution](https://pypi.org/project/duckdb/2.0.0/).
- [pandas DataFrame.to_json](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_json.html),
  [pandas 3.1.0 distribution](https://pypi.org/project/pandas/3.1.0/).
- [Elasticsearch Bulk API request framing](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-bulk).
- [MongoDB Extended JSON representations](https://www.mongodb.com/docs/manual/reference/mongodb-extended-json/).

No external service, production user dataset, or exporter authentication is
needed. The fixtures still contain raw synthetic records; real bundles would
retain the input's private data and need appropriate local protection.

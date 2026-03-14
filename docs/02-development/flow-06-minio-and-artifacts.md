# Flow 06 - MinIO And Artifacts

> **Status:** ⚠️ Partially Implemented  
> **Verified:** 2026-03-09

## Goal

Expose safe artifact discovery and object-read operations for ruleset and field registry artifacts stored in MinIO.

## Deliverables

- readonly MinIO client
- bucket and prefix-safe object listing
- object metadata inspection
- bounded object read
- storage resources generated from ownership metadata

## Tool Set

- `s3.list_buckets`
- `s3.list_objects`
- `s3.head_object`
- `s3.get_object`

## Implementation Tasks

1. Load bucket and path ownership metadata from `storage.yaml`.
2. Restrict listing and reads to approved buckets and prefixes.
3. Add size limits and content-type handling for object reads.
4. Support JSON artifact preview with truncation.
5. Expose artifact catalog resources for rulesets and field registries.
6. Audit bucket, prefix, object key, and byte counts.

## Guardrails

- no bucket create/delete
- no object put/delete
- no unrestricted recursive list beyond allowed prefixes
- no raw credential exposure

## Verification

- list objects works for approved artifact prefix
- object head returns metadata
- large object read is truncated safely
- disallowed prefix is rejected

## Known Gaps

- ✔ Readonly MinIO/S3 client via aioboto3
- ✔ Bucket listing, object listing with prefix and max keys
- ✔ Object metadata inspection via head_object
- ✔ Bounded object read with size limit and content-type filtering
- ✔ Config-driven bucket/prefix allowlists enforced for list, head, get, and bucket catalog reads
- ✔ S3 object metadata redaction before response emission
- ✔ Text-only content types enforced (JSON, YAML, CSV, XML, text)
- ✔ Result redaction via `redact()`
- ❌ **No ownership metadata ingestion** — `storage.yaml` from platform control-plane is not consumed
- ❌ **Ownership metadata source of truth** — bucket/prefix allowlists are config-driven today; they are not generated from `storage.yaml`
- ❌ **Dedicated artifact resource families** — no ruleset-specific or field-registry-specific MCP resources exist yet

## Codex Prompt Seed

Implement Flow 06 for `card-fraud-mcp-gateway`. Add readonly MinIO tools and storage resources driven by the platform storage ownership file. Keep reads bounded and prefix-restricted. Add tests and report the exact commands used.

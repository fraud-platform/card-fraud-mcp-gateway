"""
Live MCP Agent Demo — with real Docker backends.
Simulates exactly what Claude Desktop / any AI agent does when connected.
Run: uv run python scripts/demo_agent.py
"""

import json
import os
import re

import httpx

BASE = os.environ.get("GATEWAY_DEMO_BASE", "http://localhost:8005")
HDRS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def sse(t):
    m = re.search(r"^data: (.+)$", t, re.MULTILINE)
    return json.loads(m.group(1)) if m else None


def banner(title):
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def section(title):
    print()
    print(f"--- {title} " + "-" * max(0, 56 - len(title)))


c = httpx.Client(base_url=BASE, timeout=15)
h = dict(HDRS)

banner("CARD FRAUD MCP GATEWAY -- LIVE DEMO WITH REAL BACKENDS")

# Pre-flight
r = c.get("/health")
print(f"Health  : {r.json()}")
r = c.get("/ready")
rd = r.json()
print(f"Ready   : {rd['ready']} -- {rd['backends']}")

# MCP Handshake
section("MCP Handshake")
r = c.post(
    "/mcp",
    headers=h,
    json={
        "jsonrpc": "2.0",
        "id": nid(),
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "demo-agent", "version": "1.0"},
        },
    },
)
init = sse(r.text)
h["mcp-session-id"] = r.headers.get("mcp-session-id", "")
res = init["result"]
print(f"  Server   : {res['serverInfo']['name']} v{res['serverInfo']['version']}")
print(f"  Protocol : {res['protocolVersion']}")
print(f"  Session  : {h['mcp-session-id'][:16]}...")
c.post(
    "/mcp",
    headers=h,
    json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
)


def call(name, args=None):
    r2 = c.post(
        "/mcp",
        headers=h,
        json={
            "jsonrpc": "2.0",
            "id": nid(),
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        },
    )
    d = sse(r2.text)
    res2 = (d or {}).get("result", {})
    text = (res2.get("content") or [{}])[0].get("text", "")
    return res2.get("isError", False), text


def prompt_get(name, args=None):
    r2 = c.post(
        "/mcp",
        headers=h,
        json={
            "jsonrpc": "2.0",
            "id": nid(),
            "method": "prompts/get",
            "params": {"name": name, "arguments": args or {}},
        },
    )
    d = sse(r2.text)
    msgs = (d or {}).get("result", {}).get("messages", [])
    return [(m.get("role"), m.get("content", {}).get("text", "")) for m in msgs]


# PLATFORM DOMAIN
section("PLATFORM DOMAIN -- 4 tools (no backend needed)")

err, text = call("platform.inventory")
data = json.loads(text)
print(f"  platform.inventory -> {data['count']} services:")
for s in data.get("services", []):
    print(f"    {s['name']:<38} type={s['type']:<14} owner={s['owner']}")

err, text = call("platform.service_status", {"service_name": "card-fraud-engine"})
data = json.loads(text)
status = data.get("status")
service_type = data.get("type")
print(
    f"  platform.service_status(card-fraud-engine) -> status={status}, "
    f"type={service_type}"
)

err, text = call("platform.service_health", {"service_name": "card-fraud-mcp-gateway"})
data = json.loads(text)
health = data.get("health", {})
healthy = health.get("healthy")
reason = health.get("reason", "N/A")
print(
    f"  platform.service_health -> healthy={healthy}, reason={reason}"
)

err, text = call("platform.ownership_summary")
data = json.loads(text)
print(f"  platform.ownership_summary -> teams: {list(data.get('by_owner', {}).keys())}")

# POSTGRES DOMAIN
section("POSTGRES DOMAIN -- 4 tools (PostgreSQL 18, fraud_gov db)")

err, text = call("postgres.list_schemas")
if not err:
    data = json.loads(text)
    schemas = data.get("schemas", [])
    print(f"  postgres.list_schemas -> {len(schemas)} schemas: {schemas}")
else:
    print(f"  postgres.list_schemas -> ERROR: {text[:100]}")

err, text = call("postgres.list_tables", {"schema": "public"})
if not err:
    data = json.loads(text)
    tables = [t.get("table_name") for t in data.get("tables", [])]
    print(f"  postgres.list_tables(public) -> {len(tables)} tables: {tables[:8]}")
else:
    print(f"  postgres.list_tables -> ERROR: {text[:100]}")

# Pick the first available table to describe
if not err and tables:
    first_table = tables[0]
    err2, text2 = call("postgres.describe_table", {"table": first_table, "schema": "public"})
    if not err2:
        data2 = json.loads(text2)
        cols = data2.get("columns", [])
        print(f"  postgres.describe_table({first_table}) -> {len(cols)} columns:")
        for col in cols[:5]:
            col_name = col.get("column_name")
            col_type = col.get("data_type")
            col_nullable = col.get("is_nullable")
            print(
                f"    {col_name:<25} {col_type:<20} nullable={col_nullable}"
            )
    else:
        print(f"  postgres.describe_table -> ERROR: {text2[:100]}")

err, text = call(
    "postgres.query_readonly",
    {
        "sql": (
            "SELECT table_name, "
            "pg_size_pretty(pg_total_relation_size(quote_ident(table_name)::text)) AS size "
            "FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name LIMIT 10"
        )
    },
)
if not err:
    data = json.loads(text)
    rows = data.get("rows", [])
    print(f"  postgres.query_readonly (table sizes) -> {len(rows)} rows:")
    for row in rows:
        print(f"    {row.get('table_name', '?'):<30} {row.get('size', '?')}")
else:
    print(f"  postgres.query_readonly -> ERROR: {text[:120]}")

# REDIS DOMAIN
section("REDIS DOMAIN -- 4 tools (Redis on localhost:6379)")

err, text = call("redis.scan_prefix", {"prefix": "fraud:"})
if not err:
    data = json.loads(text)
    keys = data.get("keys", [])
    print(f"  redis.scan_prefix(fraud:) -> {len(keys)} keys: {keys[:6]}")
    if keys:
        first_key = keys[0]
        err2, text2 = call("redis.get_key", {"key": first_key})
        if not err2:
            data2 = json.loads(text2)
            value_preview = str(data2.get("value", ""))[:60]
            print(
                f"  redis.get_key({first_key}) -> type={data2.get('type')}, "
                f"value_preview={value_preview}"
            )
        err3, text3 = call("redis.ttl", {"key": first_key})
        if not err3:
            data3 = json.loads(text3)
            print(f"  redis.ttl({first_key}) -> {data3.get('ttl_seconds')} seconds")
        err4, text4 = call("redis.type", {"key": first_key})
        if not err4:
            data4 = json.loads(text4)
            print(f"  redis.type({first_key}) -> {data4.get('type')}")
else:
    print(
        "  redis.scan_prefix -> no 'fraud:' keys or allowlist blocked. "
        "Try setting GATEWAY_REDIS_ALLOWED_PREFIXES=fraud:"
    )
    # Try no-prefix scan
    err, text = call("redis.scan_prefix", {"prefix": ""})
    if not err:
        data = json.loads(text)
        keys = data.get("keys", [])
        print(f"  redis.scan_prefix('') -> {len(keys)} keys: {keys[:8]}")

# KAFKA DOMAIN
section("KAFKA DOMAIN -- 4 tools (Redpanda on localhost:9092)")

err, text = call("kafka.list_topics")
if not err:
    data = json.loads(text)
    topics = data.get("topics", [])
    print(f"  kafka.list_topics -> {len(topics)} topics: {topics[:8]}")
    if topics:
        first_topic = topics[0]
        err2, text2 = call("kafka.describe_topic", {"topic": first_topic})
        if not err2:
            data2 = json.loads(text2)
            partition_count = data2.get("partition_count")
            print(
                f"  kafka.describe_topic({first_topic}) -> partitions={partition_count}"
            )
        err3, text3 = call("kafka.peek_messages", {"topic": first_topic, "max_messages": 3})
        if not err3:
            data3 = json.loads(text3)
            msgs = data3.get("messages", [])
            print(f"  kafka.peek_messages({first_topic}) -> {len(msgs)} messages")
            for msg in msgs[:2]:
                preview = str(msg.get("value", ""))[:80].replace("\n", " ")
                print(f"    offset={msg.get('offset')} key={msg.get('key')} value={preview}")
else:
    print(f"  kafka.list_topics -> ERROR: {text[:100]}")

# S3 DOMAIN
section("S3/MINIO DOMAIN -- 4 tools (MinIO on localhost:9000)")

err, text = call("s3.list_buckets")
if not err:
    data = json.loads(text)
    buckets = data.get("buckets", [])
    print(f"  s3.list_buckets -> {len(buckets)} buckets: {[b.get('name') for b in buckets]}")
    if buckets:
        first_bucket = buckets[0].get("name")
        err2, text2 = call("s3.list_objects", {"bucket": first_bucket, "prefix": ""})
        if not err2:
            data2 = json.loads(text2)
            objects = data2.get("objects", [])
            print(f"  s3.list_objects({first_bucket}) -> {len(objects)} objects:")
            for obj in objects[:4]:
                print(f"    {obj.get('key'):<40} {obj.get('size_bytes')} bytes")
            if objects:
                first_obj = objects[0].get("key")
                err3, text3 = call("s3.head_object", {"bucket": first_bucket, "key": first_obj})
                if not err3:
                    data3 = json.loads(text3)
                    content_type = data3.get("content_type")
                    size_bytes = data3.get("size_bytes")
                    print(
                        f"  s3.head_object({first_obj}) -> content_type={content_type}, "
                        f"size={size_bytes} bytes"
                    )
        else:
            print(f"  s3.list_objects -> ERROR: {text2[:100]}")
else:
    print(f"  s3.list_buckets -> ERROR: {text[:100]}")

# OPS DOMAIN
section("OPS DOMAIN -- cross-domain investigation")

err, text = call("ops.get_investigation_context", {"transaction_id": "txn_001"})
if not err:
    data = json.loads(text)
    print(f"  ops.get_investigation_context(txn_001) -> sources={data.get('sources')}")
    if data.get("transaction"):
        txn = data["transaction"]
        print(f"    transaction: {txn}")
    if data.get("db_note"):
        print(f"    db_note: {data['db_note']}")
    if data.get("redis_note"):
        print(f"    redis_note: {data['redis_note']}")
else:
    print(f"  ops.get_investigation_context -> ERROR: {text[:120]}")

err, text = call(
    "ops.run_investigation", {"investigation_type": "transaction_review", "target_id": "txn_001"}
)
if not err:
    data = json.loads(text)
    investigation_status = data.get("status")
    steps = [s.get("step") for s in data.get("steps", [])]
    print(
        f"  ops.run_investigation(transaction_review, txn_001) -> "
        f"status={investigation_status}, steps={steps}"
    )
else:
    print(f"  ops.run_investigation -> ERROR: {text[:120]}")

# PROMPTS
section("PROMPTS -- 5 guided investigation workflows")
for name, args in [
    ("investigate-transaction", {"transaction_id": "txn_001"}),
    ("triage-platform-health", {}),
    ("review-consumer-lag", {"group_id": "fraud-processor"}),
]:
    msgs = prompt_get(name, args)
    total = sum(len(t) for _, t in msgs)
    preview = msgs[0][1][:120].replace("\n", " ") if msgs else ""
    print(f"  {name}: {len(msgs)} msg(s), ~{total} chars")
    print(f"    {preview}...")

banner("LIVE DEMO COMPLETE -- ALL BACKENDS CONNECTED")
print("  PostgreSQL  : fraud_gov @ localhost:5432")
print("  Redis       : localhost:6379")
print("  Kafka       : Redpanda @ localhost:9092")
print("  S3/MinIO    : http://localhost:9000")
print()
print("  Add to Claude Desktop config:")
print('  { "mcpServers": { "card-fraud-gateway": { "url": "http://localhost:8005/mcp" } } }')
print()

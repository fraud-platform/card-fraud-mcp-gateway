"""MinIO / S3 read-only inspection tools — bucket listing, object reads, metadata."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.audit import audit_tool, tool_result
from app.backends import get_s3_session
from app.config import settings
from app.metrics import record_result_truncation
from app.security.allowlist import check_exact, check_path_prefix
from app.security.policy import require_scope
from app.security.redaction import redact, redact_dict


def _check_bucket_allowed(bucket: str) -> None:
    """Reject access to buckets not in the allowlist (when configured)."""
    check_exact(bucket, settings.s3_allowed_buckets, "Bucket")


def _check_prefix_allowed(bucket: str, prefix: str) -> None:
    """Reject access to bucket/prefix paths not in the allowlist (when configured)."""
    check_path_prefix(bucket, prefix, settings.s3_allowed_prefixes)


def _check_object_allowed(bucket: str, key: str) -> None:
    """Reject object access outside the configured bucket/prefix boundary."""
    _check_bucket_allowed(bucket)
    _check_prefix_allowed(bucket, key)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="s3.list_buckets",
        description=(
            "List all S3/MinIO buckets. Domain: storage | Read-only | Scope: fraud.storage.read"
        ),
    )
    @require_scope("fraud.storage.read", domain="storage", tool_name="s3.list_buckets")
    @audit_tool("storage")
    async def s3_list_buckets() -> str:
        async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
            response = await s3.list_buckets()
        buckets = [
            {"name": b["Name"], "created": b["CreationDate"].isoformat()}
            for b in response.get("Buckets", [])
        ]
        allowed = settings.s3_allowed_buckets
        if allowed:
            buckets = [b for b in buckets if b["name"] in allowed]
        return tool_result({"buckets": buckets, "count": len(buckets)})

    @mcp.tool(
        name="s3.list_objects",
        description=(
            "List objects in a bucket with optional prefix. Max 100 objects returned. "
            "Args: bucket, prefix (optional). "
            "Domain: storage | Read-only | Scope: fraud.storage.read"
        ),
    )
    @require_scope("fraud.storage.read", domain="storage", tool_name="s3.list_objects")
    @audit_tool("storage")
    async def s3_list_objects(bucket: str, prefix: str = "") -> str:
        _check_object_allowed(bucket, prefix)
        kwargs: dict = {"Bucket": bucket, "MaxKeys": 100}
        if prefix:
            kwargs["Prefix"] = prefix
        async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
            response = await s3.list_objects_v2(**kwargs)
        objects = [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "modified": obj["LastModified"].isoformat(),
            }
            for obj in response.get("Contents", [])
        ]
        if response.get("IsTruncated"):
            record_result_truncation("storage", "s3.list_objects", "max_keys")
        return tool_result(
            {"bucket": bucket, "prefix": prefix, "objects": objects, "count": len(objects)}
        )

    @mcp.tool(
        name="s3.head_object",
        description=(
            "Get metadata for an S3 object without downloading it. "
            "Args: bucket, key. "
            "Domain: storage | Read-only | Scope: fraud.storage.read"
        ),
    )
    @require_scope("fraud.storage.read", domain="storage", tool_name="s3.head_object")
    @audit_tool("storage")
    async def s3_head_object(bucket: str, key: str) -> str:
        _check_object_allowed(bucket, key)
        async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
            response = await s3.head_object(Bucket=bucket, Key=key)
        metadata = redact_dict(
            {
                "bucket": bucket,
                "key": key,
                "size": response["ContentLength"],
                "content_type": response.get("ContentType", ""),
                "modified": response["LastModified"].isoformat(),
                "etag": response.get("ETag", ""),
                "metadata": response.get("Metadata", {}),
            }
        )
        return tool_result(metadata)

    @mcp.tool(
        name="s3.get_object",
        description=(
            f"Read an S3 object's content. Max {settings.s3_max_object_bytes} bytes. "
            "Only text-based content types (JSON, YAML, CSV, XML, text) are supported. "
            "Args: bucket, key. "
            "Domain: storage | Read-only | Scope: fraud.storage.read"
        ),
    )
    @require_scope("fraud.storage.read", domain="storage", tool_name="s3.get_object")
    @audit_tool("storage")
    async def s3_get_object(bucket: str, key: str) -> str:
        _check_object_allowed(bucket, key)
        range_end = settings.s3_max_object_bytes
        async with get_s3_session().client("s3", endpoint_url=settings.s3_endpoint) as s3:
            response = await s3.get_object(
                Bucket=bucket,
                Key=key,
                Range=f"bytes=0-{range_end}",
            )
            content_type = response.get("ContentType", "")
            size = response.get("ContentLength", 0)

            if size > settings.s3_max_object_bytes:
                record_result_truncation("storage", "s3.get_object", "max_object_bytes")
                return tool_result(
                    {
                        "error": (
                            f"Object too large ({size} bytes, max {settings.s3_max_object_bytes})"
                        ),
                        "bucket": bucket,
                        "key": key,
                        "content_type": content_type,
                    }
                )

            text_types = ("json", "text", "yaml", "xml", "csv", "plain")
            if not any(t in content_type.lower() for t in text_types):
                return tool_result(
                    {
                        "error": (
                            f"Binary content type '{content_type}' not supported. "
                            "Use s3.head_object for metadata only."
                        ),
                        "bucket": bucket,
                        "key": key,
                    }
                )

            body = await response["Body"].read()

        content = body.decode("utf-8", errors="replace")
        payload = {
            "bucket": bucket,
            "key": key,
            "content_type": content_type,
            "size": size,
            "content": redact(content),
        }
        return tool_result(redact_dict(payload))

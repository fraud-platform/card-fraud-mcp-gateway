"""Tests for storage domain tools and helpers."""

from __future__ import annotations

from app.domains import storage


class TestCheckBucketAllowed:
    def test_check_bucket_allowed_with_allowlist(self, monkeypatch):
        monkeypatch.setattr(storage.settings, "s3_allowed_buckets", ["allowed-bucket"])
        storage._check_bucket_allowed("allowed-bucket")


class TestCheckPrefixAllowed:
    def test_check_prefix_allowed_with_allowlist(self, monkeypatch):
        monkeypatch.setattr(storage.settings, "s3_allowed_prefixes", ["bucket/allowed:"])
        storage._check_prefix_allowed("bucket", "allowed:prefix")

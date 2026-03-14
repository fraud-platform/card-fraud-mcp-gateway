"""Tests for platform domain tools and helpers."""

from __future__ import annotations

import pytest

from app.config import AppEnvironment
from app.domains import platform


class TestLoadInventory:
    def test_load_inventory_returns_default_when_no_file(self, monkeypatch):
        monkeypatch.setattr(platform.settings, "services_file", None)
        inventory = platform._load_inventory()
        assert "card-fraud-platform" in inventory
        assert inventory["card-fraud-platform"]["owner"] == "platform-team"

    def test_load_inventory_returns_default_when_file_not_exists(self, monkeypatch):
        monkeypatch.setattr(platform.settings, "services_file", "/nonexistent/file.yaml")
        inventory = platform._load_inventory()
        assert "card-fraud-platform" in inventory

    def test_load_inventory_returns_default_when_invalid_yaml(self, monkeypatch, tmp_path):
        yaml_file = tmp_path / "services.yaml"
        yaml_file.write_text("invalid: yaml content")
        monkeypatch.setattr(platform.settings, "services_file", str(yaml_file))
        inventory = platform._load_inventory()
        assert "card-fraud-platform" in inventory

    def test_prod_requires_contract_file(self, monkeypatch, tmp_path):
        missing = tmp_path / "missing-services.yaml"
        monkeypatch.setattr(platform.settings, "services_file", str(missing))
        monkeypatch.setattr(platform.settings, "app_env", AppEnvironment.PROD)
        with pytest.raises(RuntimeError, match="Platform service contract not found"):
            platform._load_inventory_and_source()


class TestFetchServiceEndpoint:
    def test_fetch_service_endpoint_simple(self):
        result = platform._fetch_service_endpoint("my-service", "status")
        assert result == "/api/v1/services/my-service/status"

    def test_fetch_service_endpoint_with_special_chars(self):
        result = platform._fetch_service_endpoint("my-service-v2", "health")
        assert result == "/api/v1/services/my-service-v2/health"


class TestServiceInventory:
    def test_default_inventory_loaded(self):
        assert "card-fraud-platform" in platform._SERVICE_INVENTORY
        assert "card-fraud-api" in platform._SERVICE_INVENTORY
        assert platform._SERVICE_INVENTORY["card-fraud-platform"]["owner"] == "platform-team"

    def test_inventory_has_required_fields(self):
        for _name, info in platform._SERVICE_INVENTORY.items():
            assert "type" in info
            assert "owner" in info
            assert "port" in info

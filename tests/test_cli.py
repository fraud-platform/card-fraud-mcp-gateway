"""Tests for main CLI functions."""

from __future__ import annotations


class TestCheckFunction:
    def test_check_prints_info(self, capsys):
        from app.main import check

        check()
        captured = capsys.readouterr()
        assert "Card Fraud MCP Gateway" in captured.out
        assert "Registered tools:" in captured.out


class TestExportCatalog:
    def test_export_catalog_works(self):
        from starlette.testclient import TestClient

        from app import main

        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get("/catalog")
        assert resp.status_code == 200

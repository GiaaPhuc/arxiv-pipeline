"""Scaffold sanity: everything imports, shared contract behaves."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from crawler.config import Config
from crawler.models import SCHEMA_VERSION, IdRange, Status

MODULES = [
    "crawler.config",
    "crawler.models",
    "crawler.http",
    "crawler.ids",
    "crawler.discovery",
    "crawler.source",
    "crawler.s2",
    "crawler.manifest",
    "crawler.pipeline",
    "crawler.metrics",
    "crawler.export",
    "corpus",
    "run_crawl",
    "validate_corpus",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)


def test_status_final_states():
    assert not Status.PENDING.is_final
    assert not Status.FAILED.is_final
    for s in (Status.OK, Status.OK_NO_TEX, Status.NO_SOURCE, Status.NOT_FOUND, Status.CORRUPT):
        assert s.is_final


def test_id_range_is_value_object():
    r = IdRange("2024-08", 11045, "2024-08", 16044)
    assert r == IdRange("2024-08", 11045, "2024-08", 16044)
    assert SCHEMA_VERSION >= 1


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("S2_API_KEY", "secret-key")
    monkeypatch.setenv("CRAWLER_CONTACT", "me@example.com")
    cfg = Config.from_env()
    assert cfg.s2_api_key == "secret-key"
    assert "me@example.com" in cfg.user_agent
    assert "secret-key" not in repr(cfg)  # key must never leak into logs
    assert cfg.arxiv_min_interval >= 3.0
    assert cfg.data_dir == Path("data")

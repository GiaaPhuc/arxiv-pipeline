"""CLI behaviour of ``run_crawl`` and sanity of the committed id ranges (no network)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import run_crawl
from crawler import ids, pipeline

RANGES = Path(__file__).resolve().parents[1] / "id_ranges.csv"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.delenv("CRAWLER_CONTACT", raising=False)
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.delenv("CRAWLER_DATA_DIR", raising=False)
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):  # release crawl.log opened by main()
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)


def test_committed_ranges_are_25000_distinct_ids_with_both_ends():
    all_ids = [i for r in ids.load_ranges(RANGES) for i in ids.expand_range(r)]
    assert len(all_ids) == 25_000
    assert len(set(all_ids)) == 25_000  # ranges never overlap
    assert {"2408.01045", "2408.16044", "2407.07839", "2407.12838"} <= set(all_ids)
    assert {"2406.07938", "2406.12937"} <= set(all_ids)


def test_dry_run_prints_plan_without_network_or_files(capsys, tmp_path):
    code = run_crawl.main(["--ranges", str(RANGES), "--dry-run", "--data-dir", str(tmp_path / "d")])

    out = capsys.readouterr().out
    assert code == 0
    assert "25000 ids" in out and "first 2408.11045" in out
    assert not (tmp_path / "d").exists()


def test_limit_restricts_the_number_of_ids(capsys):
    assert run_crawl.main(["--ranges", str(RANGES), "--dry-run", "--limit", "7"]) == 0
    assert "7 ids" in capsys.readouterr().out


def test_real_crawl_refuses_to_start_without_contact(capsys, monkeypatch):
    monkeypatch.setattr(pipeline, "run", lambda *_a, **_k: pytest.fail("must not crawl"))
    assert run_crawl.main(["--ranges", str(RANGES), "--limit", "1"]) == 2
    assert "CRAWLER_CONTACT" in capsys.readouterr().err


def test_bad_ranges_file_is_a_setup_error(capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    assert run_crawl.main(["--ranges", str(bad)]) == 2
    assert "cannot read ranges" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("raised", "code"),
    [(pipeline.StopCrawl("429", {"ok": 1}), 3), (KeyboardInterrupt(), 130)],
)
def test_exit_codes_for_stop_and_interrupt(monkeypatch, tmp_path, raised, code):
    monkeypatch.setenv("CRAWLER_CONTACT", "me@example.com")

    def boom(*_a, **_k):
        raise raised

    monkeypatch.setattr(pipeline, "run", boom)
    args = ["--ranges", str(RANGES), "--limit", "1", "--data-dir", str(tmp_path / "d")]
    assert run_crawl.main(args) == code


def test_successful_run_passes_ids_and_config_to_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWLER_CONTACT", "me@example.com")
    seen = {}

    def fake_run(all_ids, config):
        seen["ids"] = list(all_ids)
        seen["data_dir"] = config.data_dir
        return {"ok": 2}

    monkeypatch.setattr(pipeline, "run", fake_run)
    args = ["--ranges", str(RANGES), "--limit", "2", "--data-dir", str(tmp_path / "d")]

    assert run_crawl.main(args) == 0
    assert seen["ids"] == ["2408.11045", "2408.11046"]
    assert seen["data_dir"] == tmp_path / "d"
    assert (tmp_path / "d" / "logs" / "crawl.log").is_file()

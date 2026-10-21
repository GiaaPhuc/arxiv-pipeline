"""T1.2 (owner A, reviewer C). Replace the skips with real tests."""

from __future__ import annotations

from pathlib import Path

from crawler.manifest import Manifest, atomic_write
from crawler.models import Status, VersionMeta


def _download_once(manifest: Manifest, data_dir: Path, downloaded: set[tuple[str, int]]) -> None:
    for arxiv_id, version in manifest.todo():
        pair = (arxiv_id, version)
        version_dir = data_dir / "papers" / f"paper-{arxiv_id}" / f"v{version}"
        meta_path = version_dir / "meta.json"
        with atomic_write(meta_path) as tmp:
            tmp.write_text('{"status":"ok"}', encoding="utf-8")
        manifest.record(
            VersionMeta(
                arxiv_id=arxiv_id,
                version=version,
                status=Status.OK,
                crawled_at="2026-10-21T00:00:00+00:00",
            )
        )
        downloaded.add(pair)


def test_interrupt_then_rerun_does_not_redownload_ok_pairs(tmp_path):
    manifest_path = tmp_path / "manifest.sqlite"
    data_dir = tmp_path / "data"
    manifest = Manifest(manifest_path)

    manifest.register("2408.11045", 1)
    manifest.register("2408.11046", 1)

    first_run: set[tuple[str, int]] = set()
    _download_once(manifest, data_dir, first_run)
    assert first_run == {("2408.11045", 1), ("2408.11046", 1)}

    manifest.register("2408.11047", 1)
    second_run: set[tuple[str, int]] = set()
    _download_once(manifest, data_dir, second_run)

    assert second_run == {("2408.11047", 1)}
    assert manifest.status("2408.11045", 1) == Status.OK


def test_interrupt_leaves_no_temp_files(tmp_path):
    target = tmp_path / "papers" / "2408-11045" / "v1" / "meta.json"

    try:
        with atomic_write(target) as tmp:
            tmp.write_text("partial", encoding="utf-8")
            raise RuntimeError("simulated crash before replace")
    except RuntimeError:
        pass

    assert not target.exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_failed_pairs_are_retried_on_next_run(tmp_path):
    manifest = Manifest(tmp_path / "manifest.sqlite")

    manifest.register("2408.11045", 1)
    manifest.register("2408.11046", 1)
    manifest.register("2408.11047", 1)

    manifest.record(
        VersionMeta(arxiv_id="2408.11045", version=1, status=Status.FAILED, error="timeout")
    )
    manifest.record(VersionMeta(arxiv_id="2408.11046", version=1, status=Status.NOT_FOUND))

    todo = set(manifest.todo())
    assert ("2408.11045", 1) in todo
    assert ("2408.11047", 1) in todo
    assert ("2408.11046", 1) not in todo

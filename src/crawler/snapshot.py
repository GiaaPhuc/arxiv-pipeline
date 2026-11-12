"""Kaggle arXiv metadata snapshot parser & version partitioning.

Theo gợi ý trong yêu cầu đề bài: 'OAI-PMH và Kaggle chỉ là hộp công cụ'.
Module này đọc tệp snapshot metadata JSONLines chính thức từ Kaggle
(``arxiv-metadata-oai-snapshot.json``), lọc nhanh các bài thuộc dải ID được giao
mà KHÔNG cần gửi hàng trăm request Atom API qua mạng.

Chức năng chính:
  1. ``load_kaggle_snapshot``: đọc streaming tệp snapshot, lọc ra tập target_ids.
  2. Phân loại cấu trúc phiên bản:
     - ``single_v1_ids``: các bài chỉ có duy nhất 1 phiên bản (v1, chiếm ~70-75%).
     - ``multi_version_ids``: các bài có nhiều phiên bản (v2..vN, chiếm ~25-30%).
  3. Ghi trực tiếp ``paper.json`` theo chuẩn ``PaperMeta`` (models.py) vào
     ``data/papers/<dirname>/paper.json`` để crawler tự động bỏ qua bước khám phá online.
"""

from __future__ import annotations

import email.utils
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from .ids import id_to_dirname, normalize_id
from .manifest import atomic_write
from .models import PaperMeta

logger = logging.getLogger(__name__)


def parse_kaggle_date(raw_date: str) -> str:
    """Parse date from Kaggle versions entry or update_date to ISO YYYY-MM-DD."""
    if not raw_date:
        return ""
    # Try RFC 2822 (e.g. "Mon, 12 Aug 2024 14:15:20 GMT")
    try:
        dt = email.utils.parsedate_to_datetime(raw_date)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # Fallback to regex YYYY-MM-DD
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw_date)
    if m:
        return m.group(1)
    return raw_date[:10]


def parse_kaggle_authors(item: dict) -> list[str]:
    """Parse authors list from Kaggle snapshot item."""
    parsed = item.get("authors_parsed")
    if isinstance(parsed, list) and parsed:
        authors: list[str] = []
        for entry in parsed:
            if isinstance(entry, list) and len(entry) >= 2:
                last = str(entry[0]).strip()
                first = str(entry[1]).strip()
                middle = str(entry[2]).strip() if len(entry) >= 3 else ""
                full_parts = [p for p in (first, middle, last) if p]
                full = " ".join(full_parts).strip()
                if full:
                    authors.append(full)
        if authors:
            return authors

    raw = item.get("authors") or ""
    if not raw:
        return []
    # Split by comma or 'and'
    split = [a.strip() for a in re.split(r",|\band\b", raw) if a.strip()]
    return split or [raw.strip()]


def parse_kaggle_entry(item: dict) -> PaperMeta | None:
    """Convert one raw JSON dictionary from the Kaggle snapshot into PaperMeta."""
    raw_id = item.get("id")
    if not raw_id:
        return None
    arxiv_id = normalize_id(str(raw_id).strip())

    title = " ".join((item.get("title") or "").split())
    authors = parse_kaggle_authors(item)

    versions = item.get("versions") or []
    num_versions = len(versions) if isinstance(versions, list) and versions else 1

    submission_date = ""
    revised_dates: list[str] = []

    if isinstance(versions, list) and versions:
        if isinstance(versions[0], dict) and "created" in versions[0]:
            submission_date = parse_kaggle_date(versions[0]["created"])
        for v in versions[1:]:
            if isinstance(v, dict) and "created" in v:
                revised_dates.append(parse_kaggle_date(v["created"]))

    if not submission_date:
        submission_date = parse_kaggle_date(item.get("update_date") or "")

    venue = item.get("journal-ref")
    if venue:
        venue = " ".join(str(venue).split()) or None

    return PaperMeta(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        submission_date=submission_date,
        revised_dates=revised_dates,
        venue=venue,
        num_versions=max(1, num_versions),
    )


def filter_kaggle_snapshot(
    snapshot_path: Path | str,
    target_ids: Iterable[str],
    data_dir: Path | str | None = None,
    write_paper_json: bool = True,
) -> tuple[dict[str, PaperMeta], list[str], list[str]]:
    """Stream through the Kaggle JSONLines snapshot and filter records for target_ids.

    Returns:
        (papers_dict, single_version_ids, multi_version_ids)
    """
    path = Path(snapshot_path)
    if not path.is_file():
        raise FileNotFoundError(f"Kaggle snapshot file not found: {path}")

    wanted = {normalize_id(x) for x in target_ids}
    found: dict[str, PaperMeta] = {}
    single_v1: list[str] = []
    multi_v: list[str] = []

    logger.info("Scanning Kaggle snapshot %s for %d target IDs...", path, len(wanted))

    papers_root = Path(data_dir) / "papers" if data_dir is not None else None

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            # Quick check if any target prefix is in the line to speed up JSON parsing
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            raw_id = item.get("id")
            if not raw_id:
                continue
            norm_id = normalize_id(str(raw_id).strip())
            if norm_id not in wanted:
                continue

            meta = parse_kaggle_entry(item)
            if meta is None:
                continue

            found[norm_id] = meta
            if meta.num_versions == 1:
                single_v1.append(norm_id)
            else:
                multi_v.append(norm_id)

            if write_paper_json and papers_root is not None:
                paper_folder = papers_root / id_to_dirname(norm_id)
                paper_folder.mkdir(parents=True, exist_ok=True)
                target_json = paper_folder / "paper.json"
                if not target_json.is_file():
                    with atomic_write(target_json) as tmp:
                        tmp.write_text(
                            json.dumps(asdict(meta), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

            # Early exit if all target IDs have been found
            if len(found) >= len(wanted):
                logger.info("Found all %d target IDs at line %d.", len(wanted), line_num)
                break

    logger.info(
        "Kaggle snapshot filtered: %d/%d found (single-v1: %d, multi-version: %d)",
        len(found),
        len(wanted),
        len(single_v1),
        len(multi_v),
    )
    return found, single_v1, multi_v


def save_partition_summary(
    output_dir: Path | str,
    single_v1: list[str],
    multi_v: list[str],
) -> Path:
    """Save version partition index to JSON for reference and offline inspection."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_file = out / "version_partition.json"
    summary = {
        "total_papers": len(single_v1) + len(multi_v),
        "single_version_count": len(single_v1),
        "single_version_ratio": (
            len(single_v1) / (len(single_v1) + len(multi_v)) if (single_v1 or multi_v) else 0.0
        ),
        "multi_version_count": len(multi_v),
        "single_version_sample": single_v1[:10],
        "multi_version_sample": multi_v[:10],
    }
    with atomic_write(summary_file) as tmp:
        tmp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary_file

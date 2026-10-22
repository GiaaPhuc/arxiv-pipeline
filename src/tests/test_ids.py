"""T1.3 (owner B, reviewer D). Replace the skips with real tests."""

from pathlib import Path

from crawler.ids import expand_range, id_to_dirname, load_ranges, normalize_id, split_version
from crawler.models import IdRange


def test_normalize_id():
    """Ensure normalize_id strips URLs, prefixes, and keeps old/new IDs intact."""
    assert normalize_id("2310.12345") == "2310.12345"
    assert normalize_id("arXiv:2310.12345v1") == "2310.12345v1"
    assert normalize_id("https://arxiv.org/abs/2310.12345") == "2310.12345"
    assert normalize_id("hep-th/9901001") == "hep-th/9901001"


def test_split_version_new_old_and_versioned_ids():
    """2310.12345v2, hep-th/9901001, 2310.12345 (no suffix)."""
    assert split_version("2310.12345v2") == ("2310.12345", 2)
    assert split_version("2310.12345") == ("2310.12345", None)
    assert split_version("hep-th/9901001") == ("hep-th/9901001", None)
    assert split_version("hep-th/9901001v3") == ("hep-th/9901001", 3)
    assert split_version("arXiv:2310.12345v1") == ("2310.12345", 1)


def test_id_to_dirname():
    """2310.12345 -> 2310-12345; hep-th/9901001 -> hep-th-9901001."""
    assert id_to_dirname("2310.12345") == "2310-12345"
    assert id_to_dirname("2310.12345v2") == "2310-12345"
    assert id_to_dirname("hep-th/9901001") == "hep-th-9901001"
    assert id_to_dirname("hep-th/9901001v1") == "hep-th-9901001"


def test_expand_sample_range_has_5000_ids_both_ends_inclusive():
    """2024-08/11045 -> 2024-08/16044 == 2408.11045 .. 2408.16044 (5,000 ids)."""
    rng = IdRange("2024-08", 11045, "2024-08", 16044)
    ids = list(expand_range(rng))
    assert len(ids) == 5000
    assert ids[0] == "2408.11045"
    assert ids[-1] == "2408.16044"


def test_expand_range_across_months_does_not_add_numbers_across_months():
    """Range crossing months resets counter per month."""
    rng = IdRange("2024-08", 99998, "2024-09", 2)
    ids = list(expand_range(rng))
    assert ids[0] == "2408.99998"
    assert ids[1] == "2408.99999"
    assert ids[2] == "2409.00001"
    assert ids[3] == "2409.00002"
    assert len(ids) == 4


def test_zero_padding_4_digits_before_2015_5_after():
    """4 digits before 2015, 5 digits starting 2015-01."""
    rng_old = IdRange("2014-12", 1, "2014-12", 3)
    ids_old = list(expand_range(rng_old))
    assert ids_old == ["1412.0001", "1412.0002", "1412.0003"]

    rng_new = IdRange("2015-01", 1, "2015-01", 3)
    ids_new = list(expand_range(rng_new))
    assert ids_new == ["1501.00001", "1501.00002", "1501.00003"]


def test_load_ranges(tmp_path: Path):
    """Test loading ranges from a valid CSV."""
    csv_file = tmp_path / "test_ranges.csv"
    csv_file.write_text(
        "start_month,start_id,end_month,end_id\n2024-08,11045,2024-08,16044\n",
        encoding="utf-8",
    )
    ranges = load_ranges(csv_file)
    assert len(ranges) == 1
    assert ranges[0].start_month == "2024-08"
    assert ranges[0].start_id == 11045

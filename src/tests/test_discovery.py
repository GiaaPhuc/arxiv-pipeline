"""T2.1 (owner B, reviewer A). Replace the skips with real tests (mock API with `responses`)."""

import requests
import responses

from crawler.config import Config
from crawler.discovery import discover

SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2310.12345v3</id>
    <published>2023-10-18T12:00:00Z</published>
    <updated>2023-10-25T15:00:00Z</updated>
    <title>Sample Paper on Transformers</title>
    <author>
      <name>Alice Smith</name>
    </author>
    <author>
      <name>Bob Jones</name>
    </author>
    <arxiv:journal_ref>Journal of Machine Learning, 2023</arxiv:journal_ref>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2310.67890v1</id>
    <published>2023-10-20T08:00:00Z</published>
    <updated>2023-10-20T08:00:00Z</updated>
    <title>Another Study on Deep Learning</title>
    <author>
      <name>Charlie Brown</name>
    </author>
  </entry>
</feed>
"""


class DummyClient:
    """Mock HostClient for discovery tests without depending on http.py."""

    def __init__(self, base_url: str = "https://export.arxiv.org") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        return self.session.get(url, **kwargs)


@responses.activate
def test_num_versions_read_from_latest_version_suffix():
    """Verify num_versions is read from vN suffix (v3 -> num_versions=3)."""
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=SAMPLE_ATOM_XML,
        status=200,
        content_type="application/atom+xml",
    )
    config = Config.from_env()
    client = DummyClient()

    found, missing = discover(["2310.12345"], client, config)
    assert len(found) == 1
    assert missing == []
    paper = found[0]
    assert paper.arxiv_id == "2310.12345"
    assert paper.num_versions == 3
    assert paper.title == "Sample Paper on Transformers"
    assert paper.authors == ["Alice Smith", "Bob Jones"]
    assert paper.submission_date == "2023-10-18"


@responses.activate
def test_missing_ids_are_reported_not_raised():
    """Verify non-existent IDs are returned in missing list without raising."""
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=SAMPLE_ATOM_XML,
        status=200,
        content_type="application/atom+xml",
    )
    config = Config.from_env()
    client = DummyClient()

    found, missing = discover(["2310.12345", "2310.99999"], client, config)
    assert len(found) == 1
    assert missing == ["2310.99999"]


@responses.activate
def test_venue_prefers_journal_ref_then_s2_then_none():
    """Verify venue extracts arxiv:journal_ref when present, else None."""
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=SAMPLE_ATOM_XML,
        status=200,
        content_type="application/atom+xml",
    )
    config = Config.from_env()
    client = DummyClient()

    found, _ = discover(["2310.12345", "2310.67890"], client, config)
    paper1 = next(p for p in found if p.arxiv_id == "2310.12345")
    paper2 = next(p for p in found if p.arxiv_id == "2310.67890")

    assert paper1.venue == "Journal of Machine Learning, 2023"
    assert paper2.venue is None


def _feed(*entries: str) -> str:
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        + "".join(entries)
        + "</feed>"
    )


def _versioned(base: str, version: int, updated: str) -> str:
    return (
        f"<entry><id>http://arxiv.org/abs/{base}v{version}</id>"
        f"<published>2024-08-01T00:00:00Z</published><updated>{updated}T00:00:00Z</updated>"
        f"<title>T</title></entry>"
    )


@responses.activate
def test_absent_id_is_asked_again_on_its_own_before_it_is_called_missing():
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=SAMPLE_ATOM_XML)
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=_feed())

    found, missing = discover(["2310.12345", "2310.99999"], DummyClient(), Config.from_env())

    assert [p.arxiv_id for p in found] == ["2310.12345"]
    assert missing == ["2310.99999"]
    queried = [call.request.params["id_list"] for call in responses.calls]
    assert queried == ["2310.12345,2310.99999", "2310.99999"]


@responses.activate
def test_id_dropped_from_a_batch_but_found_on_its_own_is_not_missing():
    batch_only_one = _feed(_versioned("2310.00001", 1, "2024-08-01"))
    single = _feed(_versioned("2310.00002", 2, "2024-08-09"))
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=batch_only_one)
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=single)

    found, missing = discover(["2310.00001", "2310.00002"], DummyClient(), Config.from_env())

    assert missing == []
    assert {p.arxiv_id: p.num_versions for p in found} == {"2310.00001": 1, "2310.00002": 2}


@responses.activate
def test_server_error_is_raised_not_reported_as_missing():
    responses.add(responses.GET, "https://export.arxiv.org/api/query", status=503)
    try:
        discover(["2310.12345"], DummyClient(), Config.from_env())
    except requests.HTTPError:
        return
    raise AssertionError("a 503 must not be turned into a missing id")


@responses.activate
def test_revised_dates_come_from_the_updated_field_of_each_version():
    from crawler.discovery import fetch_revised_dates
    from crawler.models import PaperMeta

    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(
            _versioned("2310.00001", 2, "2024-08-10"),
            _versioned("2310.00001", 3, "2024-09-01"),
            _versioned("2310.00002", 2, "2024-08-20"),
        ),
    )
    papers = [
        PaperMeta("2310.00001", "A", [], "2024-08-01", num_versions=3),
        PaperMeta("2310.00002", "B", [], "2024-08-02", num_versions=2),
        PaperMeta("2310.00003", "C", [], "2024-08-03", num_versions=1),  # nothing to look up
    ]

    incomplete = fetch_revised_dates(papers, DummyClient(), Config.from_env())

    assert incomplete == []
    assert papers[0].revised_dates == ["2024-08-10", "2024-09-01"]
    assert papers[1].revised_dates == ["2024-08-20"]
    assert papers[2].revised_dates == []
    assert responses.calls[0].request.params["id_list"] == (
        "2310.00001v2,2310.00001v3,2310.00002v2"
    )


@responses.activate
def test_incomplete_version_dates_are_reported_and_never_invented():
    from crawler.discovery import fetch_revised_dates
    from crawler.models import PaperMeta

    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(_versioned("2310.00001", 2, "2024-08-10")),  # v3 missing from the feed
    )
    paper = PaperMeta("2310.00001", "A", [], "2024-08-01", num_versions=3)

    incomplete = fetch_revised_dates([paper], DummyClient(), Config.from_env())

    assert incomplete == ["2310.00001"]
    assert paper.revised_dates == []


@responses.activate
def test_discover_populates_v2_immediately_and_queries_only_intermediate_for_v3():
    from crawler.discovery import fetch_revised_dates

    # 2310.00001 has N=2 (updated=2024-08-10), 2310.00002 has N=3 (updated=2024-09-01)
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(
            _versioned("2310.00001", 2, "2024-08-10"),
            _versioned("2310.00002", 3, "2024-09-01"),
        ),
    )
    # Only 2310.00002v2 needs to be queried in fetch_revised_dates!
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(_versioned("2310.00002", 2, "2024-08-15")),
    )

    client = DummyClient()
    config = Config.from_env()
    found, missing = discover(["2310.00001", "2310.00002"], client, config)
    assert missing == []
    p1, p2 = found
    # N=2 already has revised_dates right after discover()
    assert p1.revised_dates == ["2024-08-10"]
    assert p2.revised_dates == []

    incomplete = fetch_revised_dates(found, client, config)
    assert incomplete == []
    assert p1.revised_dates == ["2024-08-10"]
    assert p2.revised_dates == ["2024-08-15", "2024-09-01"]
    assert len(responses.calls) == 2
    assert responses.calls[1].request.params["id_list"] == "2310.00002v2"


@responses.activate
def test_fetch_revised_dates_rechecks_dropped_version_in_subbatch():
    from crawler.discovery import fetch_revised_dates
    from crawler.models import PaperMeta

    # First batch drops v2, second sub-batch call returns v2.
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(_versioned("2310.00001", 3, "2024-09-01")),
    )
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(_versioned("2310.00001", 2, "2024-08-10")),
    )
    paper = PaperMeta("2310.00001", "A", [], "2024-08-01", num_versions=3)

    incomplete = fetch_revised_dates([paper], DummyClient(), Config.from_env())

    assert incomplete == []
    assert paper.revised_dates == ["2024-08-10", "2024-09-01"]
    queried = [c.request.params["id_list"] for c in responses.calls]
    assert queried == ["2310.00001v2,2310.00001v3", "2310.00001v2"]


@responses.activate
def test_discover_subbatch_error_entry_falls_back_to_individual_queries():
    error_entry = (
        "<entry><id>http://arxiv.org/api/errors#malformed</id><title>Error</title></entry>"
    )
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=_feed())
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=_feed(error_entry))
    responses.add(
        responses.GET,
        "https://export.arxiv.org/api/query",
        body=_feed(_versioned("2310.00001", 1, "2024-08-01")),
    )
    responses.add(responses.GET, "https://export.arxiv.org/api/query", body=_feed())

    found, missing = discover(["2310.00001", "2310.99999"], DummyClient(), Config.from_env())
    assert [p.arxiv_id for p in found] == ["2310.00001"]
    assert missing == ["2310.99999"]

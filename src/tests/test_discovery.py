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

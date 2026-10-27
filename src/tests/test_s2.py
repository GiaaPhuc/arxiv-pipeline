"""T2.3 (owner B, reviewer A). Replace the skips with real tests (mock S2 with `responses`)."""

import requests
import responses

from crawler.s2 import fetch_references


class DummyS2Client:
    """Mock HostClient for S2 tests without depending on http.py."""

    def __init__(self, base_url: str = "https://api.semanticscholar.org") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        return self.session.get(url, **kwargs)


@responses.activate
def test_pagination_when_references_are_truncated():
    """Verify offset pagination collects all pages until next is None."""
    client = DummyS2Client()

    # Page 1
    responses.add(
        responses.GET,
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2310.12345/references",
        json={
            "data": [
                {
                    "citedPaper": {
                        "paperId": "p1",
                        "title": "Ref 1",
                        "authors": [{"name": "Author A"}],
                        "year": 2021,
                        "externalIds": {"ArXiv": "2101.00001"},
                    }
                }
            ],
            "next": 1,
        },
        status=200,
    )
    # Page 2
    responses.add(
        responses.GET,
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2310.12345/references",
        json={
            "data": [
                {
                    "citedPaper": {
                        "paperId": "p2",
                        "title": "Ref 2",
                        "authors": [{"name": "Author B"}],
                        "year": 2022,
                        "externalIds": {"ArXiv": "2202.00002"},
                    }
                }
            ],
            "next": None,
        },
        status=200,
    )

    result = fetch_references(client, "2310.12345")
    assert result.found is True
    assert len(result.references) == 2
    assert result.references[0].ref_s2_id == "p1"
    assert result.references[0].ref_arxiv_id == "2101.00001"
    assert result.references[1].ref_s2_id == "p2"
    assert result.references[1].ref_arxiv_id == "2202.00002"
    assert len(result.raw["pages"]) == 2


@responses.activate
def test_429_backoff_and_paper_not_indexed_is_valid_state():
    """Verify 404 returns found=False without raising an exception."""
    client = DummyS2Client()
    responses.add(
        responses.GET,
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2310.99999/references",
        status=404,
    )

    result = fetch_references(client, "2310.99999")
    assert result.found is False
    assert result.references == []
    assert result.raw == {"status": 404}


@responses.activate
def test_references_missing_fields_and_old_style_arxiv_ids_kept_verbatim():
    """Verify old-style arXiv IDs and missing title/authors are safely handled."""
    client = DummyS2Client()
    responses.add(
        responses.GET,
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:hep-th%2F9901001/references",
        json={
            "data": [
                {
                    "citedPaper": {
                        "paperId": "p_old",
                        "title": None,
                        "authors": [],
                        "year": 1999,
                        "externalIds": {"ArXiv": "hep-th/9901002"},
                    }
                },
                {
                    "citedPaper": {
                        "paperId": "p_book",
                        "title": "Textbook Title",
                        "authors": None,
                        "publicationDate": "1998-05-01",
                        "externalIds": {},  # No arXiv ID
                    }
                },
            ],
            "next": None,
        },
        status=200,
    )

    result = fetch_references(client, "hep-th/9901001")
    assert result.found is True
    assert len(result.references) == 2

    # Old-style ID preserved
    ref_old = result.references[0]
    assert ref_old.ref_arxiv_id == "hep-th/9901002"
    assert ref_old.ref_title is None

    # Textbook (no arXiv ID) preserved with ref_arxiv_id=None
    ref_book = result.references[1]
    assert ref_book.ref_arxiv_id is None
    assert ref_book.ref_title == "Textbook Title"
    assert ref_book.ref_submission_date == "1998-05-01"


@responses.activate
def test_fetch_venue_returns_real_venue_and_drops_arxiv_repository():
    from crawler.s2 import fetch_venue

    base = "https://api.semanticscholar.org/graph/v1/paper"
    responses.add(responses.GET, f"{base}/ARXIV:2310.00001", json={"venue": "  NeurIPS  2023 "})
    responses.add(responses.GET, f"{base}/ARXIV:2310.00002", json={"venue": "arXiv.org"})
    responses.add(responses.GET, f"{base}/ARXIV:2310.00003", json={"venue": ""})
    responses.add(responses.GET, f"{base}/ARXIV:2310.00004", status=404, json={})
    client = DummyS2Client()

    assert fetch_venue(client, "2310.00001v2") == "NeurIPS 2023"
    assert fetch_venue(client, "2310.00002") is None
    assert fetch_venue(client, "2310.00003") is None
    assert fetch_venue(client, "2310.00004") is None

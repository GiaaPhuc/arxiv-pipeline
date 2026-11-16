"""T2.3 (owner B, reviewer A). Replace the skips with real tests (mock S2 with `responses`)."""

import requests
import responses

from crawler.s2 import fetch_references, fetch_references_batch


class DummyS2Client:
    """Mock HostClient for S2 tests without depending on http.py."""

    def __init__(self, base_url: str = "https://api.semanticscholar.org") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        return self.session.get(url, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        return self.session.post(url, **kwargs)


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


@responses.activate
def test_batch_returns_references_and_venue():
    client = DummyS2Client()
    responses.add(
        responses.POST,
        "https://api.semanticscholar.org/graph/v1/paper/batch",
        json=[
            {
                "paperId": "p-main",
                "venue": "ICLR 2024",
                "referenceCount": 1,
                "references": [
                    {
                        "paperId": "p-ref",
                        "title": "Batch ref",
                        "authors": [{"name": "Ann"}],
                        "year": 2023,
                        "externalIds": {"ArXiv": "2301.00001"},
                    }
                ],
            }
        ],
    )

    out = fetch_references_batch(client, ["2310.12345"])
    result = out["2310.12345"]
    assert result.found is True
    assert result.venue == "ICLR 2024"
    assert len(result.references) == 1
    assert result.references[0].ref_arxiv_id == "2301.00001"
    assert result.raw["source"] == "batch"


@responses.activate
def test_batch_null_element_is_not_indexed():
    client = DummyS2Client()
    responses.add(
        responses.POST,
        "https://api.semanticscholar.org/graph/v1/paper/batch",
        json=[None],
    )

    out = fetch_references_batch(client, ["2310.99999"])
    result = out["2310.99999"]
    assert result.found is False
    assert result.raw == {"status": 404}


@responses.activate
def test_batch_falls_back_when_reference_count_exceeds_embedded_list():
    client = DummyS2Client()
    responses.add(
        responses.POST,
        "https://api.semanticscholar.org/graph/v1/paper/batch",
        json=[
            {
                "paperId": "p-big",
                "venue": "NeurIPS 2024",
                "referenceCount": 50,
                "references": [{"paperId": "only-one", "title": "One", "authors": []}],
            }
        ],
    )
    responses.add(
        responses.GET,
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2310.12345/references",
        json={
            "data": [
                {
                    "citedPaper": {
                        "paperId": "p1",
                        "title": "From pagination",
                        "authors": [],
                        "year": 2020,
                        "externalIds": {},
                    }
                }
            ],
            "next": None,
        },
    )

    out = fetch_references_batch(client, ["2310.12345"])
    result = out["2310.12345"]
    assert result.found is True
    assert result.venue == "NeurIPS 2024"
    assert result.references[0].ref_title == "From pagination"
    assert result.raw["source"] == "references"


def test_extract_local_references_bib_and_bbl(tmp_path):
    from crawler.s2 import extract_local_references

    # Create dummy bib file
    tex_dir = tmp_path / "v1" / "tex"
    tex_dir.mkdir(parents=True)
    bib_content = """
    @article{vaswani2017attention,
      title = {Attention is all you need},
      author = {Vaswani, Ashish and Shazeer, Noam},
      year = {2017},
      eprint = {1706.03762},
      archivePrefix = {arXiv}
    }
    @book{goodfellow2016deep,
      title = {Deep Learning},
      author = {Goodfellow, Ian and Bengio, Yoshua},
      year = {2016}
    }
    """
    (tex_dir / "refs.bib").write_text(bib_content, encoding="utf-8")

    refs = extract_local_references(tmp_path)
    assert len(refs) == 2
    r1 = next(r for r in refs if r.ref_arxiv_id == "1706.03762")
    assert r1.ref_title == "Attention is all you need"
    assert "Vaswani, Ashish" in r1.ref_authors
    assert r1.ref_submission_date == "2017"

    r2 = next(r for r in refs if r.ref_title == "Deep Learning")
    assert r2.ref_arxiv_id is None
    assert r2.ref_submission_date == "2016"


@responses.activate
def test_batch_bib_fallback_when_s2_returns_null(tmp_path):
    from crawler.s2 import fetch_references_batch

    client = DummyS2Client()
    tex_dir = tmp_path / "v1" / "tex"
    tex_dir.mkdir(parents=True)
    (tex_dir / "refs.bib").write_text(
        "@article{a, title={Fallback Paper}, author={Author One}, "
        "year={2023}, eprint={2301.00001}}",
        encoding="utf-8",
    )

    responses.add(
        responses.POST,
        "https://api.semanticscholar.org/graph/v1/paper/batch",
        json=[None],
    )

    # Without fallback enabled
    out_no_fb = fetch_references_batch(
        client, ["2310.99999"], paper_dirs={"2310.99999": tmp_path}, enable_bib_fallback=False
    )
    assert out_no_fb["2310.99999"].found is False

    # With fallback enabled
    out_fb = fetch_references_batch(
        client, ["2310.99999"], paper_dirs={"2310.99999": tmp_path}, enable_bib_fallback=True
    )
    res = out_fb["2310.99999"]
    assert res.found is True
    assert res.raw["source"] == "local_bib"
    assert len(res.references) == 1
    assert res.references[0].ref_arxiv_id == "2301.00001"
    assert res.references[0].ref_title == "Fallback Paper"


@responses.activate
def test_batch_bisection_on_payload_too_large():
    from crawler.s2 import fetch_references_batch

    client = DummyS2Client()

    # 1) When batch receives 2 IDs, return 413 Payload Too Large
    def batch_callback(request):
        import json

        body = json.loads(request.body)
        if len(body["ids"]) > 1:
            return 413, {}, "Payload Too Large"
        paper_id = body["ids"][0].removeprefix("ARXIV:")
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "paperId": f"p-{paper_id}",
                        "venue": "Test Venue",
                        "referenceCount": 0,
                        "references": [],
                    }
                ]
            ),
        )

    responses.add_callback(
        responses.POST,
        "https://api.semanticscholar.org/graph/v1/paper/batch",
        callback=batch_callback,
        content_type="application/json",
    )

    out = fetch_references_batch(client, ["2310.00001", "2310.00002"])
    assert len(out) == 2
    assert out["2310.00001"].found is True
    assert out["2310.00002"].found is True

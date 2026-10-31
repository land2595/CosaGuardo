import httpx
import pytest

from app.imdb import ImdbRatings
from tests import mock_tmdb


@pytest.fixture(autouse=True)
def mock_imdb(monkeypatch):
    """I voti IMDb arrivano dal finto server, mai dalla rete."""
    monkeypatch.setenv("IMDB_RATINGS_URL", "http://mock/imdb/title.ratings.tsv.gz")
    monkeypatch.setattr(ImdbRatings, "_client", lambda self: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_tmdb.app), base_url="http://mock"))

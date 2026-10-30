import asyncio

import httpx

from app.catalog import Catalog, match_platform, unique_items
from app.config import PLATFORMS, Settings
from tests import mock_tmdb

ALL = list(PLATFORMS)


def test_match_platform():
    assert match_platform("Netflix", ALL) == "netflix"
    assert match_platform("Netflix Standard with Ads", ALL) == "netflix"
    assert match_platform("Amazon Prime Video", ALL) == "prime"
    assert match_platform("Amazon Video", ALL) is None  # noleggio/acquisto, non abbonamento
    assert match_platform("Apple TV", ALL) is None
    assert match_platform("Apple TV Plus", ALL) == "apple"
    assert match_platform("Now TV", ALL) == "sky"
    assert match_platform("Sky Go", ALL) == "sky"
    assert match_platform("Netflix", ["prime"]) is None


def test_unique_items_merges_platforms():
    items = unique_items([
        {"id": "movie-1", "platforms": ["netflix"]},
        {"id": "movie-1", "platforms": ["prime", "netflix"]},
        {"id": "tv-1", "platforms": ["sky"]},
    ])
    assert [i["id"] for i in items] == ["movie-1", "tv-1"]
    assert items[0]["platforms"] == ["netflix", "prime"]


def test_refresh_with_mock(tmp_path):
    s = Settings()
    s.tmdb_api_key = "test"
    s.data_dir = str(tmp_path)
    cat = Catalog(s)
    cat._client = lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_tmdb.app), base_url="http://mock"
    )
    asyncio.run(cat.refresh())

    assert cat.error is None
    novita = cat.data["novita"]
    ids = [i["id"] for i in novita]
    assert novita and len(ids) == len(set(ids)), "nessun doppione"
    dates = [i["date"] for i in novita]
    assert dates == sorted(dates, reverse=True), "ordinate dalla piu' recente"
    # Il film 1001 e' su Netflix, Prime Video e NOW: compare una volta con tutte le piattaforme
    m = next(i for i in novita if i["id"] == "movie-1001")
    assert set(m["platforms"]) == {"netflix", "prime", "sky"}
    assert cat.data["tendenze"][0]["rank"] == 1
    assert (tmp_path / "catalogo.json").exists()


def test_set_key_from_page(tmp_path):
    s = Settings()
    s.tmdb_api_key = ""
    s.data_dir = str(tmp_path)
    cat = Catalog(s)
    cat._client = lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_tmdb.app), base_url="http://mock",
        params={"api_key": s.tmdb_api_key},
    )
    assert cat.needs_key

    try:
        asyncio.run(cat.set_key("sbagliata-0123456789abcdef"))
        raise AssertionError("la chiave sbagliata deve essere rifiutata")
    except Exception as e:  # noqa: BLE001
        assert "non valida" in str(e)
    assert cat.needs_key and not (tmp_path / "chiave_tmdb").exists()

    asyncio.run(cat.set_key("0123456789abcdef0123456789abcdef"))
    assert not cat.needs_key
    assert (tmp_path / "chiave_tmdb").read_text() == "0123456789abcdef0123456789abcdef"
    # Al riavvio la chiave salvata viene ricaricata
    s2 = Settings()
    s2.tmdb_api_key = ""
    s2.data_dir = str(tmp_path)
    assert Catalog(s2).s.tmdb_api_key == "0123456789abcdef0123456789abcdef"

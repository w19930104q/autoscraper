"""Offline record-mode tests against a real BeautifulSoup document."""

import json

import bs4
import pytest

from autoscraper import AutoScraper


HTML = """
<html>
  <body>
    <header><h1 class="name">Alpha</h1><span class="price">$99</span></header>
    <section class="list">
      <article class="product" data-id="1">
        <h2 class="name">Alpha</h2>
        <span class="price">$10</span>
        <ul class="tags">
          <li class="tag">Sale</li>
          <li class="tag">Sale</li>
          <li class="tag">New</li>
        </ul>
        <a class="link" href="/products/alpha">Details</a>
        <img class="photo" src="/img/alpha.png">
        <article class="product" data-id="1a">
          <h2 class="name">Alpha Mini</h2>
          <ul class="tags"><li class="tag">Mini</li></ul>
        </article>
      </article>
      <article class="product" data-id="2">
        <h2 class="name">Beta</h2>
        <ul class="tags"><li class="tag">Sale</li></ul>
        <a class="link" href="/products/beta">Details</a>
      </article>
      <article class="product" data-id="3">
        <h2 class="name">Beta</h2>
        <ul class="tags"><li class="tag">Sale</li></ul>
        <a class="link" href="/products/beta">Details</a>
      </article>
    </section>
  </body>
</html>
"""

PAGE_TWO = """
<html>
  <body>
    <article class="product">
      <h2 class="name">Gamma</h2>
      <span class="price">$30</span>
      <ul class="tags"><li class="tag">Hot</li><li class="tag">Hot</li></ul>
      <a class="link" href="/products/gamma">Details</a>
    </article>
    <article class="product">
      <h2 class="name">Delta</h2>
    </article>
  </body>
</html>
"""

BASE_URL = "https://shop.example/catalog"

WANTED = {
    "name": ["Alpha", "Alpha Mini"],
    "price": ["$10"],
    "tag": ["Sale", "Mini"],
    "label": ["Details"],
    "link": ["https://shop.example/products/alpha"],
    "image": ["https://shop.example/img/alpha.png"],
}

EXPECTED = [
    {
        "name": ["Alpha"],
        "price": ["$10"],
        "tag": ["Sale", "Sale", "New"],
        "label": ["Details"],
        "link": ["https://shop.example/products/alpha"],
        "image": ["https://shop.example/img/alpha.png"],
    },
    {
        "name": ["Alpha Mini"],
        "price": [],
        "tag": ["Mini"],
        "label": [],
        "link": [],
        "image": [],
    },
    {
        "name": ["Beta"],
        "price": [],
        "tag": ["Sale"],
        "label": ["Details"],
        "link": ["https://shop.example/products/beta"],
        "image": [],
    },
    {
        "name": ["Beta"],
        "price": [],
        "tag": ["Sale"],
        "label": ["Details"],
        "link": ["https://shop.example/products/beta"],
        "image": [],
    },
]


def test_real_beautifulsoup_is_used():
    assert hasattr(bs4, "__version__")
    soup = bs4.BeautifulSoup('<p class="x">hi</p>', "lxml")
    assert soup.select_one("p.x").get_text() == "hi"


def test_records_end_to_end_with_real_bs4():
    scraper = AutoScraper()
    rows = scraper.build(
        url=BASE_URL,
        html=HTML,
        wanted_dict=WANTED,
        record_selector="article.product",
    )
    assert rows == EXPECTED
    assert scraper.get_result_records(url=BASE_URL, html=HTML) == EXPECTED
    # The header values are never absorbed into a record.
    assert "$99" not in rows[0]["price"]
    assert all(len(rows) == 4 for _ in [0])


def test_cross_page_reuse_with_real_bs4():
    scraper = AutoScraper()
    scraper.build(
        url=BASE_URL,
        html=HTML,
        wanted_dict=WANTED,
        record_selector="article.product",
    )
    rows = scraper.get_result_records(
        url="https://shop.example/page-2", html=PAGE_TWO
    )
    assert rows == [
        {
            "name": ["Gamma"],
            "price": ["$30"],
            "tag": ["Hot", "Hot"],
            "label": ["Details"],
            "link": ["https://shop.example/products/gamma"],
            "image": [],
        },
        {
            "name": ["Delta"],
            "price": [],
            "tag": [],
            "label": [],
            "link": [],
            "image": [],
        },
    ]


def test_save_load_roundtrip_with_real_bs4(tmp_path):
    scraper = AutoScraper()
    scraper.build(
        url=BASE_URL,
        html=HTML,
        wanted_dict=WANTED,
        record_selector="article.product",
    )
    path = tmp_path / "records.json"
    scraper.save(path)

    loaded = AutoScraper()
    loaded.load(path)
    assert loaded.record_selector == "article.product"
    assert loaded.record_aliases == list(WANTED.keys())
    assert loaded.get_result_records(url=BASE_URL, html=HTML) == EXPECTED

    # Old flat models still load and extract.
    flat = AutoScraper()
    flat.build(html="<ul><li>Banana</li><li>Apple</li></ul>", wanted_list=["Banana"])
    flat_path = tmp_path / "flat.json"
    flat.save(flat_path)
    with open(flat_path) as model_file:
        assert "record" not in json.load(model_file)
    restored = AutoScraper()
    restored.load(flat_path)
    assert restored.record_selector is None
    assert restored.get_result_exact(
        html="<ul><li>Banana</li><li>Apple</li></ul>"
    ) == ["Banana"]


def test_invalid_model_load_preserves_state_with_real_bs4(tmp_path):
    scraper = AutoScraper()
    scraper.build(
        url=BASE_URL,
        html=HTML,
        wanted_dict={"name": ["Alpha"]},
        record_selector="article.product",
    )
    before = (list(scraper.stack_list), scraper.record_selector, list(scraper.record_aliases))

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"stack_list": [], "record": {"selector": ".x", "aliases": "name"}}))
    with pytest.raises(ValueError):
        scraper.load(bad)
    assert (scraper.stack_list, scraper.record_selector, scraper.record_aliases) == before

    with pytest.raises(ValueError):
        scraper.build(
            html=HTML,
            wanted_dict={"name": ["Alpha"], "price": []},
            record_selector="article.product",
        )
    assert (scraper.stack_list, scraper.record_selector, scraper.record_aliases) == before

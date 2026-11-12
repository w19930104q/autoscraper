import json

import pytest

from autoscraper import AutoScraper


RECORD_HTML = """
<html>
  <body>
    <h1 class="title">Alpha</h1>
    <div class="card" id="card-1">
      <h2 class="title">Alpha</h2>
      <span class="price">$10</span>
      <ul class="tags">
        <li class="tag">Sale</li>
        <li class="tag">Sale</li>
        <li class="tag">New</li>
      </ul>
      <a class="link" href="https://example.com/alpha">Details</a>
      <div class="card" id="card-1-nested">
        <h2 class="title">Nested Alpha</h2>
        <ul class="tags">
          <li class="tag">Inner</li>
          <li class="tag">Exclusive</li>
        </ul>
      </div>
    </div>
    <div class="card" id="card-2">
      <h2 class="title">Beta</h2>
      <ul class="tags">
        <li class="tag">Sale</li>
      </ul>
      <a class="link" href="https://example.com/beta">Details</a>
    </div>
  </body>
</html>
"""

BASE_URL = "https://example.com/products"

WANTED = {
    "title": ["Alpha", "Nested Alpha"],
    "price": ["$10"],
    "tag": ["Sale", "Inner"],
    "label": ["Details"],
    "link": ["https://example.com/alpha"],
}

EXPECTED = [
    {
        "title": ["Alpha"],
        "price": ["$10"],
        "tag": ["Sale", "Sale", "New"],
        "label": ["Details"],
        "link": ["https://example.com/alpha"],
    },
    {
        "title": ["Nested Alpha"],
        "price": [],
        "tag": ["Inner", "Exclusive"],
        "label": [],
        "link": [],
    },
    {
        "title": ["Beta"],
        "price": [],
        "tag": ["Sale"],
        "label": ["Details"],
        "link": ["https://example.com/beta"],
    },
]


def test_records_by_container_in_dom_order():
    scraper = AutoScraper()
    result = scraper.build(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict=WANTED,
        record_selector="div.card",
    )
    assert result == EXPECTED
    assert scraper.get_result_records(url=BASE_URL, html=RECORD_HTML) == EXPECTED
    assert scraper.get_records(url=BASE_URL, html=RECORD_HTML) == EXPECTED


def test_header_is_not_absorbed():
    # "Alpha" appears in the page header and in the first card.  The header
    # is outside the record containers and must not become a record or a
    # value.
    scraper = AutoScraper()
    rows = scraper.build(
        html=RECORD_HTML,
        wanted_dict={"title": ["Alpha"]},
        record_selector=".card",
    )
    assert [row["title"] for row in rows] == [["Alpha"], ["Nested Alpha"], ["Beta"]]


def test_missing_field_is_empty_list_and_not_borrowed():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"price": ["$10"]},
        record_selector=".card",
    )
    assert rows == [{"price": ["$10"]}, {"price": []}, {"price": []}]


def test_multi_value_alias_keeps_dom_order():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"tag": ["Sale"]},
        record_selector=".card",
    )
    assert rows[0]["tag"] == ["Sale", "Sale", "New"]
    assert rows[1]["tag"] == ["Inner", "Exclusive"]
    assert rows[2]["tag"] == ["Sale"]


def test_duplicate_text_from_different_nodes_is_preserved():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"tag": ["Sale"]},
        record_selector=".card",
    )
    assert rows[0]["tag"].count("Sale") == 2
    assert rows[2]["tag"] == ["Sale"]


def test_completely_duplicate_cards_are_preserved():
    html = (
        '<div class="card"><span class="v">X</span></div>'
        '<div class="card"><span class="v">X</span></div>'
    )
    rows = AutoScraper().build(
        html=html, wanted_dict={"v": ["X"]}, record_selector=".card"
    )
    assert rows == [{"v": ["X"]}, {"v": ["X"]}]


def test_same_node_can_belong_to_different_aliases():
    rows = AutoScraper().build(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict={
            "label": ["Details"],
            "link": ["https://example.com/alpha"],
        },
        record_selector=".card",
    )
    assert rows[0]["label"] == ["Details"]
    assert rows[0]["link"] == ["https://example.com/alpha"]
    assert rows[1]["label"] == []
    assert rows[1]["link"] == []
    assert rows[2]["label"] == ["Details"]
    assert rows[2]["link"] == ["https://example.com/beta"]


def test_same_node_can_belong_to_different_aliases_with_identical_rules():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"primary": ["Details"], "secondary": ["Details"]},
        record_selector=".card",
    )
    assert rows[0]["primary"] == ["Details"]
    assert rows[0]["secondary"] == ["Details"]
    assert rows[2]["primary"] == ["Details"]
    assert rows[2]["secondary"] == ["Details"]


def test_first_rule_wins_for_same_node_and_alias():
    # Two samples hit the same <a> node for the same alias.  The value of
    # the rule learned first (the text "Details") is used once.
    rows = AutoScraper().build(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict={"field": ["Details", "https://example.com/alpha"]},
        record_selector=".card",
    )
    assert rows[0]["field"] == ["Details"]
    assert rows[1]["field"] == []
    assert rows[2]["field"] == ["Details"]


def test_nested_containers_are_separate_rows():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"title": ["Alpha", "Nested Alpha"], "tag": ["Sale", "Inner"]},
        record_selector=".card",
    )
    assert [row["title"] for row in rows] == [["Alpha"], ["Nested Alpha"], ["Beta"]]
    assert rows[0]["tag"] == ["Sale", "Sale", "New"]
    assert rows[1]["tag"] == ["Inner", "Exclusive"]
    assert "Nested Alpha" not in rows[0]["title"]
    assert "Inner" not in rows[0]["tag"]


def test_selector_variants():
    for selector in ["div.card", ".card", ".card#card-2", "div[class~=card]"]:
        wanted = ["Beta"] if selector == ".card#card-2" else ["Alpha"]
        rows = AutoScraper().build(
            html=RECORD_HTML,
            wanted_dict={"title": wanted},
            record_selector=selector,
        )
        if selector == ".card#card-2":
            assert rows == [{"title": ["Beta"]}]
        else:
            assert [row["title"] for row in rows] == [
                ["Alpha"],
                ["Nested Alpha"],
                ["Beta"],
            ]


def test_attribute_and_full_url_rules():
    html = """
    <div class="card">
      <span class="sku" data-sku="A-1">x</span>
      <a class="link" href="/a">Go</a>
      <img class="pic" src="/img/a.png">
    </div>
    <div class="card">
      <span class="sku" data-sku="B-2">x</span>
      <a class="link" href="/b">Go</a>
      <img class="pic" src="/img/b.png">
    </div>
    """
    scraper = AutoScraper()
    rows = scraper.build(
        url="https://shop.example/list",
        html=html,
        wanted_dict={
            "sku": ["A-1"],
            "link": ["https://shop.example/a"],
            "image": ["https://shop.example/img/a.png"],
        },
        record_selector=".card",
    )
    assert rows == [
        {
            "sku": ["A-1"],
            "link": ["https://shop.example/a"],
            "image": ["https://shop.example/img/a.png"],
        },
        {
            "sku": ["B-2"],
            "link": ["https://shop.example/b"],
            "image": ["https://shop.example/img/b.png"],
        },
    ]


def test_cross_page_reuse():
    page_one = """
    <div class="card"><h2 class="name">Alpha</h2><a class="link" href="/a">Go</a></div>
    <div class="card"><h2 class="name">Beta</h2><a class="link" href="/b">Go</a></div>
    """
    page_two = """
    <div class="card"><h2 class="name">Gamma</h2><a class="link" href="/g">Go</a></div>
    <div class="card"><h2 class="name">Delta</h2></div>
    """
    scraper = AutoScraper()
    scraper.build(
        url="https://shop.example/one",
        html=page_one,
        wanted_dict={"name": ["Alpha"], "link": ["https://shop.example/a"]},
        record_selector=".card",
    )
    rows = scraper.get_result_records(
        url="https://shop.example/two", html=page_two
    )
    assert rows == [
        {"name": ["Gamma"], "link": ["https://shop.example/g"]},
        {"name": ["Delta"], "link": []},
    ]


def test_build_records_convenience_and_getter_flags():
    scraper = AutoScraper()
    rows = scraper.build_records(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict=WANTED,
        record_selector=".card",
    )
    assert rows == EXPECTED
    assert scraper.get_result_similar(html=RECORD_HTML, records=True) == EXPECTED
    assert (
        scraper.get_result_exact(html=RECORD_HTML, record_selector=".card")
        == EXPECTED
    )
    assert scraper.get_result(html=RECORD_HTML, records=True) == EXPECTED


def test_records_survive_save_and_load(tmp_path):
    scraper = AutoScraper()
    scraper.build(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict=WANTED,
        record_selector="div.card",
    )
    path = tmp_path / "record_model.json"
    scraper.save(path)

    with open(path) as model_file:
        model = json.load(model_file)
    assert model["record"]["selector"] == "div.card"
    assert model["record"]["aliases"] == list(WANTED.keys())

    loaded = AutoScraper()
    loaded.load(path)
    assert loaded.record_selector == "div.card"
    assert loaded.record_aliases == list(WANTED.keys())
    assert loaded.get_result_records(url=BASE_URL, html=RECORD_HTML) == EXPECTED


def test_learning_requires_a_sample_for_every_alias():
    scraper = AutoScraper()
    with pytest.raises(ValueError):
        scraper.build(
            html=RECORD_HTML,
            wanted_dict={"title": ["Alpha"], "price": []},
            record_selector=".card",
        )
    with pytest.raises(ValueError):
        scraper.build(
            html=RECORD_HTML,
            wanted_dict={"title": ["Alpha"], "price": [""]},
            record_selector=".card",
        )
    with pytest.raises(ValueError):
        scraper.build(html=RECORD_HTML, record_selector=".card")
    # The failed builds must not leave a half-learned model behind.
    assert scraper.record_selector is None
    assert scraper.record_aliases is None
    assert scraper.stack_list == []


def test_learning_fails_for_unknown_container_or_sample():
    with pytest.raises(ValueError):
        AutoScraper().build(
            html=RECORD_HTML,
            wanted_dict={"title": ["Alpha"]},
            record_selector=".does-not-exist",
        )
    with pytest.raises(ValueError):
        AutoScraper().build(
            html=RECORD_HTML,
            wanted_dict={"title": ["No such title"]},
            record_selector=".card",
        )


def test_load_invalid_model_fails_and_preserves_previous(tmp_path):
    scraper = AutoScraper()
    scraper.build(html="<ul><li>Banana</li><li>Apple</li></ul>", wanted_list=["Banana"])
    original_stack = list(scraper.stack_list)

    bad_models = [
        "not json",
        json.dumps(42),
        json.dumps({"record": {"selector": ".card", "aliases": []}}),
        json.dumps({"stack_list": "nope"}),
        json.dumps({"stack_list": [{"wanted_attr": None}]}),
        json.dumps(
            {
                "stack_list": [],
                "record": {"selector": ".card", "aliases": ["ok", 1]},
            }
        ),
        json.dumps(
            {
                "stack_list": [],
                "record": {"selector": 123, "aliases": []},
            }
        ),
    ]
    for index, payload in enumerate(bad_models):
        path = tmp_path / ("bad_%d.json" % index)
        path.write_text(payload)
        with pytest.raises(ValueError):
            scraper.load(path)
        assert scraper.stack_list == original_stack
        assert scraper.record_selector is None
        assert scraper.record_aliases is None

    # A valid model still loads afterwards.
    good = tmp_path / "good.json"
    scraper.save(good)
    scraper.load(good)
    assert len(scraper.stack_list) == len(original_stack)
    assert scraper.get_result_exact(
        html="<ul><li>Banana</li><li>Apple</li></ul>"
    ) == ["Banana"]


def test_old_model_files_and_old_extraction_are_unchanged(tmp_path):
    html = "<ul><li>Banana</li><li>Apple</li><li>Orange</li></ul>"
    scraper = AutoScraper()
    scraper.build(html=html, wanted_dict={"fruit": ["Banana"]})

    path = tmp_path / "old_model.json"
    scraper.save(path)
    with open(path) as model_file:
        model = json.load(model_file)
    assert "record" not in model

    loaded = AutoScraper()
    loaded.load(path)
    assert loaded.record_selector is None
    assert loaded.record_aliases is None
    assert loaded.get_result_similar(
        html=html, contain_sibling_leaves=True, unique=True
    ) == ["Banana", "Apple", "Orange"]
    assert loaded.get_result_exact(html=html) == ["Banana"]

    # Legacy list-form models load as well.
    list_path = tmp_path / "legacy_list_model.json"
    list_path.write_text(json.dumps(scraper.stack_list))
    legacy = AutoScraper()
    legacy.load(list_path)
    assert legacy.get_result_exact(html="<ul><li>Banana</li></ul>") == ["Banana"]

    # Old getter outputs are unchanged.
    grouped = scraper.get_result_similar(
        html=html, grouped=True, contain_sibling_leaves=True
    )
    rule_id = scraper.stack_list[0]["stack_id"]
    assert list(grouped.keys()) == [rule_id]
    grouped_by_alias = scraper.get_result_similar(
        html=html, group_by_alias=True, contain_sibling_leaves=True
    )
    assert grouped_by_alias == {"fruit": ["Banana", "Apple", "Orange"]}

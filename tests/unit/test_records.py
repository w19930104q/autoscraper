import json

from autoscraper import AutoScraper


RECORD_HTML = """
<html>
  <body>
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


def test_missing_field_is_empty_list_and_not_borrowed():
    # card-2 has no price.  It must not receive card-1's price.
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"price": ["$10"]},
        record_selector=".card",
    )
    assert rows == [{"price": ["$10"]}, {"price": []}, {"price": []}]


def test_duplicate_text_from_different_nodes_is_preserved():
    rows = AutoScraper().build(
        html=RECORD_HTML,
        wanted_dict={"tag": ["Sale"]},
        record_selector=".card",
    )
    # The two "Sale" <li> nodes in card-1 are different nodes and both stay.
    assert rows[0]["tag"] == ["Sale", "Sale", "New"]
    assert rows[0]["tag"].count("Sale") == 2
    assert rows[2]["tag"] == ["Sale"]


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
    assert rows[1]["primary"] == []
    assert rows[1]["secondary"] == []
    assert rows[2]["primary"] == ["Details"]
    assert rows[2]["secondary"] == ["Details"]


def test_first_rule_wins_for_same_node_and_alias():
    # Two wanted values hit the same <a> node.  The value of the rule learned
    # first (the text "Details") must be the only value for that alias.
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
    # Nested fields did not leak into the outer card.
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


def test_build_records_convenience():
    scraper = AutoScraper()
    rows = scraper.build_records(
        url=BASE_URL,
        html=RECORD_HTML,
        wanted_dict=WANTED,
        record_selector=".card",
    )
    assert rows == EXPECTED
    assert scraper.get_records(url=BASE_URL, html=RECORD_HTML) == EXPECTED


def test_getters_can_request_records_explicitly():
    scraper = AutoScraper()
    scraper.build(
        html=RECORD_HTML,
        wanted_dict={"price": ["$10"]},
        record_selector=".card",
    )
    expected = [{"price": ["$10"]}, {"price": []}, {"price": []}]
    assert scraper.get_result_similar(html=RECORD_HTML, records=True) == expected
    assert (
        scraper.get_result_exact(html=RECORD_HTML, record_selector=".card")
        == expected
    )
    assert scraper.get_result(html=RECORD_HTML, records=True) == expected


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

    loaded = AutoScraper()
    loaded.load(path)
    assert loaded.record_selector == "div.card"
    assert loaded.record_aliases == list(WANTED.keys())
    assert loaded.get_result_records(url=BASE_URL, html=RECORD_HTML) == EXPECTED


def test_old_models_still_load(tmp_path):
    # The previous save format was a bare {"stack_list": [...]} document.
    scraper = AutoScraper()
    scraper.build(html="<ul><li>Banana</li><li>Apple</li></ul>", wanted_list=["Banana"])

    path = tmp_path / "old_model.json"
    scraper.save(path)
    with open(path) as model_file:
        model = json.load(model_file)
    assert "record" not in model

    loaded = AutoScraper()
    loaded.load(path)
    assert loaded.record_selector is None
    assert loaded.get_result_similar(html="<ul><li>Banana</li><li>Apple</li></ul>") == [
        "Banana"
    ]

    # Legacy list-form models load as well.
    list_path = tmp_path / "legacy_list_model.json"
    list_path.write_text(json.dumps(scraper.stack_list))
    legacy = AutoScraper()
    legacy.load(list_path)
    assert legacy.get_result_exact(html="<ul><li>Banana</li></ul>") == ["Banana"]


def test_original_outputs_unchanged():
    html = "<ul><li>Banana</li><li>Apple</li><li>Orange</li></ul>"
    scraper = AutoScraper()
    scraper.build(html=html, wanted_dict={"fruit": ["Banana"]})

    similar = scraper.get_result_similar(
        html=html, contain_sibling_leaves=True, unique=True
    )
    assert similar == ["Banana", "Apple", "Orange"]

    grouped = scraper.get_result_similar(
        html=html, grouped=True, contain_sibling_leaves=True
    )
    rule_id = scraper.stack_list[0]["stack_id"]
    assert list(grouped.keys()) == [rule_id]

    grouped_by_alias = scraper.get_result_similar(
        html=html, group_by_alias=True, contain_sibling_leaves=True
    )
    assert grouped_by_alias == {"fruit": ["Banana", "Apple", "Orange"]}

    exact = scraper.get_result_exact(html=html)
    assert exact == ["Banana"]

import re
from collections import OrderedDict

import unicodedata

from difflib import SequenceMatcher


def unique_stack_list(stack_list):
    seen = set()
    unique_list = []
    for stack in stack_list:
        stack_hash = stack['hash']
        if stack_hash in seen:
            continue
        unique_list.append(stack)
        seen.add(stack_hash)
    return unique_list


def unique_hashable(hashable_items):
    """Removes duplicates from the list. Must preserve the orders."""
    return list(OrderedDict.fromkeys(hashable_items))


def get_non_rec_text(element):
    return ''.join(element.find_all(text=True, recursive=False)).strip()


def normalize(item):
    if not isinstance(item, str):
        return item
    return unicodedata.normalize("NFKD", item.strip())


def text_match(t1, t2, ratio_limit):
    if hasattr(t1, 'fullmatch'):
        return bool(t1.fullmatch(t2))
    if ratio_limit >= 1:
        return t1 == t2
    return SequenceMatcher(None, t1, t2).ratio() >= ratio_limit


class ResultItem():
    def __init__(self, text, index, node=None):
        self.text = text
        self.index = index
        self.node = node

    def __str__(self):
        return self.text


class FuzzyText(object):
    def __init__(self, text, ratio_limit):
        self.text = text
        self.ratio_limit = ratio_limit
        self.match = None

    def search(self, text):
        return SequenceMatcher(None, self.text, text).ratio() >= self.ratio_limit


# --- A small CSS selector engine -------------------------------------------
#
# The record based API needs to find the record containers in both a real
# BeautifulSoup document and the lightweight stand-in used by the test suite.
# Only the selector features that make sense for container selection are
# implemented (tag, #id, .class, [attr], [attr=value], [attr~=value] and the
# descendant/child combinators).  Comma separated selectors are supported.


def _attribute_values(node, name):
    value = node.attrs.get(name)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value).split()


def _attribute_string(node, name):
    if name not in node.attrs:
        return None
    value = node.attrs.get(name)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def _parse_compound(compound):
    tag = None
    ids = []
    classes = []
    attributes = []

    match = re.match(r"^(\*|[A-Za-z0-9_\-]+)", compound)
    if match:
        tag = match.group(1)
        compound = compound[match.end() :]

    while compound:
        if compound[0] == "#":
            match = re.match(r"#([A-Za-z0-9_\-]+)", compound)
            if not match:
                raise ValueError("Invalid id selector: %s" % compound)
            ids.append(match.group(1))
            compound = compound[match.end() :]
        elif compound[0] == ".":
            match = re.match(r"\.([A-Za-z0-9_\-]+)", compound)
            if not match:
                raise ValueError("Invalid class selector: %s" % compound)
            classes.append(match.group(1))
            compound = compound[match.end() :]
        elif compound[0] == "[":
            match = re.match(
                r"\[([A-Za-z0-9_\-]+)(?:\s*([~^$*]?=)\s*"
                r"(?:(['\"])(.*?)\3|([^\]\s]+)))?\s*\]",
                compound,
            )
            if not match:
                raise ValueError("Invalid attribute selector: %s" % compound)
            name, operator, _, quoted, unquoted = match.groups()
            attributes.append((name, operator, quoted if quoted is not None else unquoted))
            compound = compound[match.end() :]
        else:
            raise ValueError("Unsupported selector: %s" % compound)

    return tag, ids, classes, attributes


def _compound_matches(node, compound):
    tag, ids, classes, attributes = compound

    if tag and tag != "*" and (node.name or "").lower() != tag.lower():
        return False

    node_id = _attribute_string(node, "id")
    for wanted_id in ids:
        if node_id != wanted_id:
            return False

    node_classes = _attribute_values(node, "class")
    for wanted_class in classes:
        if wanted_class not in node_classes:
            return False

    for name, operator, wanted in attributes:
        values = _attribute_values(node, name)
        string_value = _attribute_string(node, name)
        if string_value is None:
            return False
        if operator is None:
            continue
        if operator == "=":
            if string_value != wanted:
                return False
        elif operator == "~=":
            if wanted not in values:
                return False
        elif operator == "^=":
            if not string_value.startswith(wanted):
                return False
        elif operator == "$=":
            if not string_value.endswith(wanted):
                return False
        elif operator == "*=":
            if wanted not in string_value:
                return False

    return True


def _parse_selector_group(group):
    group = group.replace(">", " > ")
    tokens = group.split()
    steps = []
    combinator = " "
    for token in tokens:
        if token == ">":
            combinator = ">"
            continue
        steps.append((combinator, _parse_compound(token)))
        combinator = " "
    if not steps:
        raise ValueError("Empty CSS selector")
    return steps


def _selector_group_matches(node, steps):
    if not _compound_matches(node, steps[-1][1]):
        return False

    current = node
    for index in range(len(steps) - 1, 0, -1):
        combinator = steps[index][0]
        compound = steps[index - 1][1]
        current = current.parent
        if combinator == ">":
            if current is None or not _compound_matches(current, compound):
                return False
        else:
            while current is not None and not _compound_matches(current, compound):
                current = current.parent
            if current is None:
                return False
    return True


def css_select(root, selector):
    """Return descendants of ``root`` matching a CSS selector.

    This intentionally implements a conservative subset of CSS instead of
    depending on ``soup.select``, because AutoScraper supports a minimal
    soup-like object in its own test-suite.
    """

    groups = [_parse_selector_group(x) for x in selector.split(",") if x.strip()]
    if not groups:
        raise ValueError("Empty CSS selector")

    matches = []
    for node in root.findChildren():
        for steps in groups:
            if _selector_group_matches(node, steps):
                matches.append(node)
                break
    return matches


def find_owning_record(node, record_ids):
    """Return the nearest record container containing ``node`` (or itself)."""

    current = node
    while current is not None:
        if id(current) in record_ids:
            return current
        current = getattr(current, "parent", None)
    return None

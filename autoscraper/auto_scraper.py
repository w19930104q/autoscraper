import hashlib
import json
from collections import defaultdict
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from autoscraper.utils import (
    FuzzyText,
    ResultItem,
    css_select,
    find_owning_record,
    get_non_rec_text,
    normalize,
    text_match,
    unique_hashable,
    unique_stack_list,
)


class AutoScraper(object):
    """
    AutoScraper : A Smart, Automatic, Fast and Lightweight Web Scraper for Python.
    AutoScraper automatically learns a set of rules required to extract the needed content
        from a web page. So the programmer doesn't need to explicitly construct the rules.

    Attributes
    ----------
    stack_list: list
        List of rules learned by AutoScraper

    Methods
    -------
    build() - Learns a set of rules represented as stack_list based on the wanted_list,
        which can be reused for scraping similar elements from other web pages in the future.
    get_result_similar() - Gets similar results based on the previously learned rules.
    get_result_exact() - Gets exact results based on the previously learned rules.
    get_results() - Gets exact and similar results based on the previously learned rules.
    get_result_records() - Gets one dictionary per record container, in DOM order.
    build_records() - Learns record rules and returns one dictionary per container.
    save() - Serializes the stack_list as JSON and saves it to disk.
    load() - De-serializes the JSON representation of the stack_list and loads it back.
    remove_rules() - Removes one or more learned rule[s] from the stack_list.
    keep_rules() - Keeps only the specified learned rules in the stack_list and removes the others.
    """

    request_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/537.36 \
            (KHTML, like Gecko) Chrome/84.0.4147.135 Safari/537.36"
    }

    def __init__(self, stack_list=None):
        self.stack_list = stack_list or []
        self.record_selector = None
        self.record_aliases = None

    def save(self, file_path):
        """
        Serializes the stack_list as JSON and saves it to the disk.

        Parameters
        ----------
        file_path: str
            Path of the JSON output

        Returns
        -------
        None
        """

        data = dict(stack_list=self.stack_list)
        if self.record_selector is not None:
            data["record"] = dict(
                selector=self.record_selector,
                aliases=list(self.record_aliases or []),
            )
        with open(file_path, "w") as f:
            json.dump(data, f)

    def load(self, file_path):
        """
        De-serializes the JSON representation of the stack_list and loads it back.

        Parameters
        ----------
        file_path: str
            Path of the JSON file to load stack_list from.

        Returns
        -------
        None
        """

        with open(file_path, "r") as f:
            data = json.load(f)

        # for backward compatibility
        if isinstance(data, list):
            self.stack_list = data
            self.record_selector = None
            self.record_aliases = None
            return

        self.stack_list = data["stack_list"]
        record = data.get("record") or {}
        self.record_selector = record.get("selector")
        self.record_aliases = record.get("aliases")

    @classmethod
    def _fetch_html(cls, url, request_args=None):
        request_args = request_args or {}
        headers = dict(cls.request_headers)
        if url:
            headers["Host"] = urlparse(url).netloc

        user_headers = request_args.pop("headers", {})
        headers.update(user_headers)
        res = requests.get(url, headers=headers, **request_args)
        if res.encoding == "ISO-8859-1" and not "ISO-8859-1" in res.headers.get(
            "Content-Type", ""
        ):
            res.encoding = res.apparent_encoding
        html = res.text
        return html

    @classmethod
    def _get_soup(cls, url=None, html=None, request_args=None):
        if html:
            html = normalize(unescape(html))
            return BeautifulSoup(html, "lxml")

        html = cls._fetch_html(url, request_args)
        html = normalize(unescape(html))

        return BeautifulSoup(html, "lxml")

    @staticmethod
    def _get_valid_attrs(item):
        key_attrs = {"class", "style"}
        return {
            k: v if v != [] else "" for k, v in item.attrs.items() if k in key_attrs
        }

    @staticmethod
    def _child_has_text(
        child, text, url, text_fuzz_ratio, allow_parent_equal=False
    ):
        child_text = child.getText().strip()

        if text_match(text, child_text, text_fuzz_ratio):
            parent_text = child.parent.getText().strip()
            if (
                child_text == parent_text
                and child.parent.parent
                and not allow_parent_equal
            ):
                return False

            child.wanted_attr = None
            return True

        if text_match(text, get_non_rec_text(child), text_fuzz_ratio):
            child.is_non_rec_text = True
            child.wanted_attr = None
            return True

        for key, value in child.attrs.items():
            if not isinstance(value, str):
                continue

            value = value.strip()
            if text_match(text, value, text_fuzz_ratio):
                child.wanted_attr = key
                return True

            if key in {"href", "src"}:
                full_url = urljoin(url, value)
                if text_match(text, full_url, text_fuzz_ratio):
                    child.wanted_attr = key
                    child.is_full_url = True
                    return True

        return False

    def _get_children(self, soup, text, url, text_fuzz_ratio):
        children = reversed(soup.findChildren())
        children = [
            x for x in children if self._child_has_text(x, text, url, text_fuzz_ratio)
        ]
        return children

    def _get_record_children(self, soup, text, url, text_fuzz_ratio):
        """Find the deepest nodes matching a record sample.

        Unlike the flat API, record rules are anchored to the innermost
        matching node.  This keeps a wrapper from producing an aggregated
        value and lets the same rule apply consistently inside every record.
        """

        candidates = [
            node
            for node in soup.findChildren()
            if self._child_has_text(
                node, text, url, text_fuzz_ratio, allow_parent_equal=True
            )
        ]

        candidate_ids = {id(node) for node in candidates}
        has_matching_descendant = set()
        for node in candidates:
            ancestor = getattr(node, "parent", None)
            while ancestor is not None:
                if id(ancestor) in candidate_ids:
                    has_matching_descendant.add(id(ancestor))
                ancestor = getattr(ancestor, "parent", None)

        return [
            node for node in candidates if id(node) not in has_matching_descendant
        ]

    @staticmethod
    def _ancestor_distance(node, ancestor):
        distance = 0
        current = node
        while current is not None:
            if current is ancestor:
                return distance
            current = getattr(current, "parent", None)
            distance += 1
        return None

    @staticmethod
    def _unique_record_stack_list(stack_list):
        """Deduplicate rules inside a record model.

        The same structural rule may intentionally belong to more than one
        alias, so aliases are part of the identity here.  Duplicate rules
        learned for the same alias are still collapsed.
        """

        seen = set()
        unique_list = []
        for stack in stack_list:
            key = (stack["hash"], stack.get("alias", ""))
            if key in seen:
                continue
            unique_list.append(stack)
            seen.add(key)
        return unique_list

    def build(
        self,
        url=None,
        wanted_list=None,
        wanted_dict=None,
        html=None,
        request_args=None,
        update=False,
        text_fuzz_ratio=1.0,
        record_selector=None,
        record_container=None,
    ):
        """
        Automatically constructs a set of rules to scrape the specified target[s] from a web page.
            The rules are represented as stack_list.

        Parameters:
        ----------
        url: str, optional
            URL of the target web page. You should either pass url or html or both.

        wanted_list: list of strings or compiled regular expressions, optional
            A list of needed contents to be scraped.
                AutoScraper learns a set of rules to scrape these targets. If specified,
                wanted_dict will be ignored.

        wanted_dict: dict, optional
            A dict of needed contents to be scraped. Keys are aliases and values are list of target texts
                or compiled regular expressions.
                AutoScraper learns a set of rules to scrape these targets and sets its aliases.

        html: str, optional
            An HTML string can also be passed instead of URL.
                You should either pass url or html or both.

        request_args: dict, optional
            A dictionary used to specify a set of additional request parameters used by requests
                module. You can specify proxy URLs, custom headers etc.

        update: bool, optional, defaults to False
            If True, new learned rules will be added to the previous ones.
            If False, all previously learned rules will be removed.

        text_fuzz_ratio: float in range [0, 1], optional, defaults to 1.0
            The fuzziness ratio threshold for matching the wanted contents.

        record_selector: str, optional
            A CSS selector selecting the record containers (for example product
            cards).  When it is supplied, learning is restricted to nodes
            inside those containers and the return value is a list of
            dictionaries, one per container in DOM order.  The selector is
            stored with the learned model, so get_result_records() can be
            called without passing it again.

        record_container: str, optional
            Alias for record_selector.

        Returns:
        --------
        List of similar results, or a list of dictionaries when
        record_selector is supplied.
        """

        if record_selector is None:
            record_selector = record_container

        if not wanted_list and not (wanted_dict and any(wanted_dict.values())):
            raise ValueError("No targets were supplied")

        soup = self._get_soup(url=url, html=html, request_args=request_args)

        result_list = []

        if wanted_list:
            wanted_dict = {"": wanted_list}

        record_aliases = list(wanted_dict.keys())

        active_record_selector = record_selector
        if active_record_selector is None and update:
            active_record_selector = self.record_selector

        if update is False:
            self.stack_list = []
            if record_selector is None:
                self.record_selector = None
                self.record_aliases = None
            else:
                self.record_selector = record_selector
                self.record_aliases = record_aliases
        elif active_record_selector is not None:
            self.record_selector = active_record_selector
            existing_aliases = list(self.record_aliases or [])
            for alias in record_aliases:
                if alias not in existing_aliases:
                    existing_aliases.append(alias)
            self.record_aliases = existing_aliases

        record_ids = None
        if active_record_selector is not None:
            record_ids = {
                id(node) for node in css_select(soup, active_record_selector)
            }

        wanted_list = []

        for alias, wanted_items in wanted_dict.items():
            wanted_items = [normalize(w) for w in wanted_items]
            wanted_list += wanted_items

            for wanted in wanted_items:
                if record_ids is not None:
                    children = self._get_record_children(
                        soup, wanted, url, text_fuzz_ratio
                    )
                    children = [
                        child
                        for child in children
                        if find_owning_record(child, record_ids) is not None
                    ]
                else:
                    children = self._get_children(
                        soup, wanted, url, text_fuzz_ratio
                    )

                for child in children:
                    result, stack = self._get_result_for_child(child, soup, url)
                    stack["alias"] = alias
                    if record_ids is not None:
                        owner = find_owning_record(child, record_ids)
                        if owner is not None:
                            stack["record_level"] = self._ancestor_distance(
                                child, owner
                            )
                    result_list += result
                    self.stack_list.append(stack)

        result_list = [item.text for item in result_list]
        result_list = unique_hashable(result_list)

        if record_ids is not None:
            self.stack_list = self._unique_record_stack_list(self.stack_list)
        else:
            self.stack_list = unique_stack_list(self.stack_list)

        if record_selector is not None:
            return self.get_result_records(soup=soup, url=url)
        return result_list

    @classmethod
    def _build_stack(cls, child, url):
        content = [(child.name, cls._get_valid_attrs(child))]

        parent = child
        while True:
            grand_parent = parent.findParent()
            if not grand_parent:
                break

            children = grand_parent.findAll(
                parent.name, cls._get_valid_attrs(parent), recursive=False
            )
            for i, c in enumerate(children):
                if c == parent:
                    content.insert(
                        0, (grand_parent.name, cls._get_valid_attrs(grand_parent), i)
                    )
                    break

            if not grand_parent.parent:
                break

            parent = grand_parent

        wanted_attr = getattr(child, "wanted_attr", None)
        is_full_url = getattr(child, "is_full_url", False)
        is_non_rec_text = getattr(child, "is_non_rec_text", False)
        stack = dict(
            content=content,
            wanted_attr=wanted_attr,
            is_full_url=is_full_url,
            is_non_rec_text=is_non_rec_text,
        )
        stack["url"] = url if is_full_url else ""
        stack["hash"] = hashlib.sha256(str(stack).encode("utf-8")).hexdigest()
        stack["stack_id"] = "rule_" + stack["hash"][:8]
        return stack

    def _get_result_for_child(self, child, soup, url):
        stack = self._build_stack(child, url)
        result = self._get_result_with_stack(stack, soup, url, 1.0)
        return result, stack

    @staticmethod
    def _fetch_result_from_child(child, wanted_attr, is_full_url, url, is_non_rec_text):
        if wanted_attr is None:
            if is_non_rec_text:
                return get_non_rec_text(child)
            return child.getText().strip()

        if wanted_attr not in child.attrs:
            return None

        if is_full_url:
            return urljoin(url, child.attrs[wanted_attr])

        return child.attrs[wanted_attr]

    @staticmethod
    def _get_fuzzy_attrs(attrs, attr_fuzz_ratio):
        attrs = dict(attrs)
        for key, val in attrs.items():
            if isinstance(val, str) and val:
                val = FuzzyText(val, attr_fuzz_ratio)
            elif isinstance(val, (list, tuple)):
                val = [FuzzyText(x, attr_fuzz_ratio) if x else x for x in val]
            attrs[key] = val
        return attrs

    def _get_result_with_stack(self, stack, soup, url, attr_fuzz_ratio, **kwargs):
        parents = [soup]
        stack_content = stack["content"]
        contain_sibling_leaves = kwargs.get("contain_sibling_leaves", False)
        for index, item in enumerate(stack_content):
            children = []
            if item[0] == "[document]":
                continue
            for parent in parents:

                attrs = item[1]
                if attr_fuzz_ratio < 1.0:
                    attrs = self._get_fuzzy_attrs(attrs, attr_fuzz_ratio)

                found = parent.findAll(item[0], attrs, recursive=False)
                if not found:
                    continue

                if not contain_sibling_leaves and index == len(stack_content) - 1:
                    idx = min(len(found) - 1, stack_content[index - 1][2])
                    found = [found[idx]]

                children += found

            parents = children

        wanted_attr = stack["wanted_attr"]
        is_full_url = stack["is_full_url"]
        is_non_rec_text = stack.get("is_non_rec_text", False)
        result = [
            ResultItem(
                self._fetch_result_from_child(
                    i, wanted_attr, is_full_url, url, is_non_rec_text
                ),
                getattr(i, "child_index", 0),
                node=i,
            )
            for i in parents
        ]
        if not kwargs.get("keep_blank", False):
            result = [x for x in result if x.text]
        return result

    def _get_result_with_stack_index_based(
        self, stack, soup, url, attr_fuzz_ratio, **kwargs
    ):
        p = soup.findChildren(recursive=False)[0]
        stack_content = stack["content"]
        for index, item in enumerate(stack_content[:-1]):
            if item[0] == "[document]":
                continue
            content = stack_content[index + 1]
            attrs = content[1]
            if attr_fuzz_ratio < 1.0:
                attrs = self._get_fuzzy_attrs(attrs, attr_fuzz_ratio)
            p = p.findAll(content[0], attrs, recursive=False)
            if not p:
                return []
            idx = min(len(p) - 1, item[2])
            p = p[idx]

        result = [
            ResultItem(
                self._fetch_result_from_child(
                    p,
                    stack["wanted_attr"],
                    stack["is_full_url"],
                    url,
                    stack["is_non_rec_text"],
                ),
                getattr(p, "child_index", 0),
                node=p,
            )
        ]
        if not kwargs.get("keep_blank", False):
            result = [x for x in result if x.text]
        return result

    def _get_result_by_func(
        self,
        func,
        url,
        html,
        soup,
        request_args,
        grouped,
        group_by_alias,
        unique,
        attr_fuzz_ratio,
        **kwargs
    ):
        if not soup:
            soup = self._get_soup(url=url, html=html, request_args=request_args)

        keep_order = kwargs.get("keep_order", False)

        if group_by_alias or (keep_order and not grouped):
            for index, child in enumerate(soup.findChildren()):
                setattr(child, "child_index", index)

        result_list = []
        grouped_result = defaultdict(list)
        for stack in self.stack_list:
            if not url:
                url = stack.get("url", "")

            result = func(stack, soup, url, attr_fuzz_ratio, **kwargs)

            if not grouped and not group_by_alias:
                result_list += result
                continue

            group_id = stack.get("alias", "") if group_by_alias else stack["stack_id"]
            grouped_result[group_id] += result

        return self._clean_result(
            result_list, grouped_result, grouped, group_by_alias, unique, keep_order
        )

    @staticmethod
    def _clean_result(
        result_list, grouped_result, grouped, grouped_by_alias, unique, keep_order
    ):
        if not grouped and not grouped_by_alias:
            if unique is None:
                unique = True
            if keep_order:
                result_list = sorted(result_list, key=lambda x: x.index)
            result = [x.text for x in result_list]
            if unique:
                result = unique_hashable(result)
            return result

        for k, val in grouped_result.items():
            if grouped_by_alias:
                val = sorted(val, key=lambda x: x.index)
            val = [x.text for x in val]
            if unique:
                val = unique_hashable(val)
            grouped_result[k] = val

        return dict(grouped_result)

    def get_result_similar(
        self,
        url=None,
        html=None,
        soup=None,
        request_args=None,
        grouped=False,
        group_by_alias=False,
        unique=None,
        attr_fuzz_ratio=1.0,
        keep_blank=False,
        keep_order=False,
        contain_sibling_leaves=False,
        record_selector=None,
        record_container=None,
        records=False,
    ):
        """
        Gets similar results based on the previously learned rules.

        Parameters:
        ----------
        url: str, optional
            URL of the target web page. You should either pass url or html or both.

        html: str, optional
            An HTML string can also be passed instead of URL.
                You should either pass url or html or both.

        request_args: dict, optional
            A dictionary used to specify a set of additional request parameters used by requests
                module. You can specify proxy URLs, custom headers etc.

        grouped: bool, optional, defaults to False
            If set to True, the result will be a dictionary with the rule_ids as keys
                and a list of scraped data per rule as values.

        group_by_alias: bool, optional, defaults to False
            If set to True, the result will be a dictionary with the rule alias as keys
                and a list of scraped data per alias as values.

        unique: bool, optional, defaults to True for non grouped results and
                False for grouped results.
            If set to True, will remove duplicates from returned result list.

        attr_fuzz_ratio: float in range [0, 1], optional, defaults to 1.0
            The fuzziness ratio threshold for matching html tag attributes.

        keep_blank: bool, optional, defaults to False
            If set to True, missing values will be returned as empty strings.

        keep_order: bool, optional, defaults to False
            If set to True, the results will be ordered as they are present on the web page.

        contain_sibling_leaves: bool, optional, defaults to False
            If set to True, the results will also contain the sibling leaves of the wanted elements.

        record_selector: str, optional
            When supplied (or when records=True), a list of record dictionaries
            is returned instead. See get_result_records().

        record_container: str, optional
            Alias for record_selector.

        records: bool, optional, defaults to False
            If set to True, return record dictionaries using the selector
            learned by build().

        Returns:
        --------
        List of similar results scraped from the web page.
        Dictionary if grouped=True or group_by_alias=True.
        List of dictionaries if records=True or a record selector is supplied.
        """

        if records or record_selector is not None or record_container is not None:
            return self.get_result_records(
                url=url,
                html=html,
                soup=soup,
                request_args=request_args,
                record_selector=record_selector,
                record_container=record_container,
                attr_fuzz_ratio=attr_fuzz_ratio,
                keep_blank=keep_blank,
                unique=bool(unique),
            )

        func = self._get_result_with_stack
        return self._get_result_by_func(
            func,
            url,
            html,
            soup,
            request_args,
            grouped,
            group_by_alias,
            unique,
            attr_fuzz_ratio,
            keep_blank=keep_blank,
            keep_order=keep_order,
            contain_sibling_leaves=contain_sibling_leaves,
        )

    def get_result_exact(
        self,
        url=None,
        html=None,
        soup=None,
        request_args=None,
        grouped=False,
        group_by_alias=False,
        unique=None,
        attr_fuzz_ratio=1.0,
        keep_blank=False,
        record_selector=None,
        record_container=None,
        records=False,
    ):
        """
        Gets exact results based on the previously learned rules.

        Parameters:
        ----------
        url: str, optional
            URL of the target web page. You should either pass url or html or both.

        html: str, optional
            An HTML string can also be passed instead of URL.
                You should either pass url or html or both.

        request_args: dict, optional
            A dictionary used to specify a set of additional request parameters used by requests
                module. You can specify proxy URLs, custom headers etc.

        grouped: bool, optional, defaults to False
            If set to True, the result will be a dictionary with the rule_ids as keys
                and a list of scraped data per rule as values.

        group_by_alias: bool, optional, defaults to False
            If set to True, the result will be a dictionary with the rule alias as keys
                and a list of scraped data per alias as values.

        unique: bool, optional, defaults to True for non grouped results and
                False for grouped results.
            If set to True, will remove duplicates from returned result list.

        attr_fuzz_ratio: float in range [0, 1], optional, defaults to 1.0
            The fuzziness ratio threshold for matching html tag attributes.

        keep_blank: bool, optional, defaults to False
            If set to True, missing values will be returned as empty strings.

        Returns:
        --------
        List of exact results scraped from the web page.
        Dictionary if grouped=True or group_by_alias=True.
        List of dictionaries if records=True or a record selector is supplied.
        """

        if records or record_selector is not None or record_container is not None:
            return self.get_result_records(
                url=url,
                html=html,
                soup=soup,
                request_args=request_args,
                record_selector=record_selector,
                record_container=record_container,
                attr_fuzz_ratio=attr_fuzz_ratio,
                keep_blank=keep_blank,
                unique=bool(unique),
            )

        func = self._get_result_with_stack_index_based
        return self._get_result_by_func(
            func,
            url,
            html,
            soup,
            request_args,
            grouped,
            group_by_alias,
            unique,
            attr_fuzz_ratio,
            keep_blank=keep_blank,
        )

    @staticmethod
    def _record_content(content):
        """Copy stack content without synthetic empty attributes."""

        result = []
        for item in content:
            attrs = {
                key: value
                for key, value in item[1].items()
                if not (isinstance(value, str) and value == "")
            }
            if len(item) > 2:
                result.append((item[0], attrs, item[2]))
            else:
                result.append((item[0], attrs))
        return result

    @classmethod
    def _record_stack(cls, stack):
        """Return a copy of a stack without synthetic empty attributes.

        Older saved models stored empty ``class``/``style`` placeholders for
        attributes that were absent on the element.  Modern BeautifulSoup
        treats an empty attribute value as a real value and therefore refuses
        to match an element that does not have that attribute at all.  Record
        extraction ignores those placeholders; the original stack is left
        untouched.
        """

        relaxed = dict(stack)
        relaxed["content"] = cls._record_content(stack["content"])
        return relaxed

    def get_result_records(
        self,
        url=None,
        html=None,
        soup=None,
        request_args=None,
        record_selector=None,
        record_container=None,
        attr_fuzz_ratio=1.0,
        keep_blank=False,
        unique=False,
    ):
        """
        Gets one dictionary per record container, in container DOM order.

        Every alias learned for the record is present in each dictionary.  Its
        value is the list of values found inside that particular record; a
        missing field is represented by an empty list.  Values from nested
        record containers never leak into an outer record.

        Parameters:
        ----------
        url: str, optional
            URL of the target web page. You should either pass url or html or both.

        html: str, optional
            An HTML string can also be passed instead of URL.

        soup: BeautifulSoup, optional
            An already parsed soup object.

        request_args: dict, optional
            Additional request parameters used by the requests module.

        record_selector: str, optional
            CSS selector of the record containers.  When omitted, the selector
            learned by build() is used.

        record_container: str, optional
            Alias for record_selector.

        attr_fuzz_ratio: float in range [0, 1], optional, defaults to 1.0
            The fuzziness ratio threshold for matching html tag attributes.

        keep_blank: bool, optional, defaults to False
            If set to True, matched but empty values are kept as empty strings.

        unique: bool, optional, defaults to False
            If set to True, duplicate values are removed inside each alias.
            By default duplicates coming from different nodes are preserved.

        Returns:
        --------
        List of dictionaries, one per matched record container.
        """

        if record_selector is None:
            record_selector = record_container
        if record_selector is None:
            record_selector = self.record_selector

        if record_selector is None:
            raise ValueError("No record selector was supplied or learned")

        if soup is None:
            soup = self._get_soup(url=url, html=html, request_args=request_args)

        record_nodes = css_select(soup, record_selector)

        aliases = list(self.record_aliases or [])
        for stack in self.stack_list:
            alias = stack.get("alias", "")
            if alias not in aliases:
                aliases.append(alias)

        record_ids = {id(node) for node in record_nodes}
        node_indexes = {
            id(node): index for index, node in enumerate(soup.findChildren())
        }

        values = {
            id(record): {alias: [] for alias in aliases} for record in record_nodes
        }
        claimed_nodes = {
            id(record): {alias: set() for alias in aliases}
            for record in record_nodes
        }

        for stack in self.stack_list:
            alias = stack.get("alias", "")
            for record in record_nodes:
                if alias not in values[id(record)]:
                    # A rule may gain a new alias at runtime (e.g. through
                    # set_rule_aliases).
                    values[id(record)][alias] = []
                    claimed_nodes[id(record)][alias] = set()

            stack_url = url or stack.get("url", "")
            content = stack["content"]
            record_level = stack.get("record_level")

            for record in record_nodes:
                if record_level is not None:
                    # Learn the part of the rule that is relative to its
                    # record container, then apply that part inside each
                    # record.  This lets a card rule also populate nested
                    # cards of the same kind.
                    owner_index = len(content) - 1 - record_level
                    if owner_index < 0 or owner_index >= len(content):
                        continue
                    relative_content = content[owner_index + 1 :]
                    if not relative_content:
                        continue
                    scoped_stack = dict(stack)
                    scoped_stack["content"] = self._record_content(
                        relative_content
                    )
                    search_root = record
                else:
                    # Models saved before record rules were introduced do not
                    # store record_level; evaluate them against the whole
                    # document and assign the results by their owning record.
                    scoped_stack = self._record_stack(stack)
                    search_root = soup

                result_items = self._get_result_with_stack(
                    scoped_stack,
                    search_root,
                    stack_url,
                    attr_fuzz_ratio,
                    keep_blank=True,
                    contain_sibling_leaves=True,
                )

                for item in result_items:
                    node = getattr(item, "node", None)
                    if node is None:
                        continue

                    owner = find_owning_record(node, record_ids)
                    if owner is not record:
                        # Fields of a nested record never leak into the outer
                        # one, even if the structural rule would match them.
                        continue

                    claimed = claimed_nodes[id(record)][alias]
                    if id(node) in claimed:
                        # The first learned rule that hits a node wins for this
                        # alias.  The same node may still be used by other
                        # aliases.
                        continue
                    claimed.add(id(node))

                    if item.text is None or (item.text == "" and not keep_blank):
                        continue

                    values[id(record)][alias].append(
                        (node_indexes.get(id(node), 0), item.text)
                    )

        result = []
        for record in record_nodes:
            row = {}
            for alias in aliases:
                alias_values = values[id(record)][alias]
                alias_values = sorted(alias_values, key=lambda pair: pair[0])
                texts = [text for _, text in alias_values]
                if unique:
                    texts = unique_hashable(texts)
                row[alias] = texts
            result.append(row)

        return result

    def build_records(
        self,
        url=None,
        wanted_list=None,
        wanted_dict=None,
        html=None,
        request_args=None,
        update=False,
        text_fuzz_ratio=1.0,
        record_selector=None,
        record_container=None,
    ):
        """
        Learns record rules and returns one dictionary per record container.

        This is a convenience wrapper around build() with record_selector.
        See build() and get_result_records() for the meaning of the
        parameters.
        """

        if record_selector is None:
            record_selector = record_container

        return self.build(
            url=url,
            wanted_list=wanted_list,
            wanted_dict=wanted_dict,
            html=html,
            request_args=request_args,
            update=update,
            text_fuzz_ratio=text_fuzz_ratio,
            record_selector=record_selector,
        )

    # Convenience aliases for callers that prefer a singular name.
    def get_result_record(self, *args, **kwargs):
        return self.get_result_records(*args, **kwargs)

    def get_records(self, *args, **kwargs):
        return self.get_result_records(*args, **kwargs)

    def get_result_by_record(self, *args, **kwargs):
        return self.get_result_records(*args, **kwargs)

    def get_result(
        self,
        url=None,
        html=None,
        request_args=None,
        grouped=False,
        group_by_alias=False,
        unique=None,
        attr_fuzz_ratio=1.0,
        record_selector=None,
        record_container=None,
        records=False,
    ):
        """
        Gets similar and exact results based on the previously learned rules.

        Parameters:
        ----------
        url: str, optional
            URL of the target web page. You should either pass url or html or both.

        html: str, optional
            An HTML string can also be passed instead of URL.
                You should either pass url or html or both.

        request_args: dict, optional
            A dictionary used to specify a set of additional request parameters used by requests
                module. You can specify proxy URLs, custom headers etc.

        grouped: bool, optional, defaults to False
            If set to True, the result will be dictionaries with the rule_ids as keys
                and a list of scraped data per rule as values.

        group_by_alias: bool, optional, defaults to False
            If set to True, the result will be a dictionary with the rule alias as keys
                and a list of scraped data per alias as values.

        unique: bool, optional, defaults to True for non grouped results and
                False for grouped results.
            If set to True, will remove duplicates from returned result list.

        attr_fuzz_ratio: float in range [0, 1], optional, defaults to 1.0
            The fuzziness ratio threshold for matching html tag attributes.

        Returns:
        --------
        Pair of (similar, exact) results.
        List of dictionaries if records=True or a record selector is supplied.
        See get_result_similar and get_result_exact methods.
        """

        if records or record_selector is not None or record_container is not None:
            return self.get_result_records(
                url=url,
                html=html,
                request_args=request_args,
                record_selector=record_selector,
                record_container=record_container,
                attr_fuzz_ratio=attr_fuzz_ratio,
                unique=bool(unique),
            )

        soup = self._get_soup(url=url, html=html, request_args=request_args)
        args = dict(
            url=url,
            soup=soup,
            grouped=grouped,
            group_by_alias=group_by_alias,
            unique=unique,
            attr_fuzz_ratio=attr_fuzz_ratio,
        )
        similar = self.get_result_similar(**args)
        exact = self.get_result_exact(**args)
        return similar, exact

    def remove_rules(self, rules):
        """
        Removes a list of learned rules from stack_list.

        Parameters:
        ----------
        rules : list
            A list of rules to be removed

        Returns:
        --------
        None
        """

        self.stack_list = [x for x in self.stack_list if x["stack_id"] not in rules]

    def keep_rules(self, rules):
        """
        Removes all other rules except the specified ones.

        Parameters:
        ----------
        rules : list
            A list of rules to keep in stack_list and removing the rest.

        Returns:
        --------
        None
        """

        self.stack_list = [x for x in self.stack_list if x["stack_id"] in rules]

    def set_rule_aliases(self, rule_aliases):
        """
        Sets the specified alias for each rule

        Parameters:
        ----------
        rule_aliases : dict
            A dictionary with keys of rule_id and values of alias

        Returns:
        --------
        None
        """

        id_to_stack = {stack["stack_id"]: stack for stack in self.stack_list}
        for rule_id, alias in rule_aliases.items():
            id_to_stack[rule_id]["alias"] = alias

    def generate_python_code(self):
        # deprecated
        print("This function is deprecated. Please use save() and load() instead.")

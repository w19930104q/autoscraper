"""Force the real BeautifulSoup for the tests in this directory.

The repository's ``tests/conftest.py`` installs a lightweight stand-in for
``bs4`` so the historical unit tests can run without a parser.  The record
interface must be verified against a real BeautifulSoup document, so any
stand-in (and modules that already imported it) is removed here before the
test module is imported.
"""

import sys


def _purge(*prefixes):
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            del sys.modules[name]


_purge("bs4", "autoscraper", "requests")

import bs4  # noqa: F401,E402  (the real BeautifulSoup)
import requests  # noqa: F401,E402

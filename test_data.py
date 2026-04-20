from __future__ import annotations

import pytest


MISSING_FIELD_CASES = [
    pytest.param("sellerId", id="нет-поля-sellerId"),
    pytest.param("name", id="нет-поля-name"),
    pytest.param("price", id="нет-поля-price"),
    pytest.param("statistics", id="нет-поля-statistics"),
]


WRONG_TYPE_CASES = [
    pytest.param("sellerId", "not-an-integer", id="sellerId-строка"),
    pytest.param("name", 12345, id="name-число"),
    pytest.param("price", "100", id="price-строка"),
    pytest.param("statistics", "not-an-object", id="statistics-строка"),
]


MISSING_STATS_CASES = [
    pytest.param("likes", id="нет-statistics-likes"),
    pytest.param("viewCount", id="нет-statistics-viewCount"),
    pytest.param("contacts", id="нет-statistics-contacts"),
]


VERY_LONG_NAME = "длинное-имя-" + ("x" * 512)

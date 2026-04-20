from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatisticsSchema:
    likes: int
    view_count: int
    contacts: int


@dataclass(frozen=True)
class ItemSchema:
    id: str
    seller_id: int
    name: str
    price: int
    statistics: StatisticsSchema
    created_at: str


@dataclass(frozen=True)
class ErrorResultSchema:
    message: str
    messages: Any | None


@dataclass(frozen=True)
class ErrorSchema:
    status: str
    result: ErrorResultSchema


def check_item_schema(data: Any) -> ItemSchema:
    assert isinstance(data, dict), (
        f"В строгой проверке объявления ожидался JSON-объект, получено {type(data).__name__}: {data!r}"
    )
    _assert_exact_keys(data, {"id", "sellerId", "name", "price", "statistics", "createdAt"}, "объявления")

    item_id = _assert_uuid_string(data["id"], "id объявления")
    seller_id = _assert_int(data["sellerId"], "sellerId")
    name = _assert_non_empty_string(data["name"], "name")
    price = _assert_int(data["price"], "price")
    statistics = check_stats_schema(data["statistics"])
    created_at = _assert_non_empty_string(data["createdAt"], "createdAt")

    return ItemSchema(
        id=item_id,
        seller_id=seller_id,
        name=name,
        price=price,
        statistics=statistics,
        created_at=created_at,
    )


def check_items_schema(data: Any, *, expected_len: int | None = None) -> list[ItemSchema]:
    assert isinstance(data, list), (
        f"В строгой проверке ожидался список объявлений, получено {type(data).__name__}: {data!r}"
    )
    if expected_len is not None:
        assert len(data) == expected_len, (
            f"Ожидалась длина списка {expected_len}, получено {len(data)}. Ответ: {data!r}"
        )
    return [check_item_schema(item) for item in data]


def check_stats_schema(data: Any) -> StatisticsSchema:
    assert isinstance(data, dict), (
        f"В строгой проверке statistics ожидался JSON-объект, получено {type(data).__name__}: {data!r}"
    )
    _assert_exact_keys(data, {"likes", "viewCount", "contacts"}, "statistics")

    return StatisticsSchema(
        likes=_assert_int(data["likes"], "likes"),
        view_count=_assert_int(data["viewCount"], "viewCount"),
        contacts=_assert_int(data["contacts"], "contacts"),
    )


def check_stats_list_schema(data: Any, *, expected_len: int | None = None) -> list[StatisticsSchema]:
    assert isinstance(data, list), (
        f"В строгой проверке ожидался список statistics, получено {type(data).__name__}: {data!r}"
    )
    if expected_len is not None:
        assert len(data) == expected_len, (
            f"Ожидалась длина списка {expected_len}, получено {len(data)}. Ответ: {data!r}"
        )
    return [check_stats_schema(item) for item in data]


def check_error_schema(
    data: Any,
    *,
    expected_http_status: int | None = None,
    require_consistent_status: bool = True,
    require_non_empty_message: bool = False,
) -> ErrorSchema:
    assert isinstance(data, dict), (
        f"В строгой проверке ошибки ожидался JSON-объект, получено {type(data).__name__}: {data!r}"
    )
    _assert_exact_keys(data, {"result", "status"}, "ошибки")

    result = data["result"]
    assert isinstance(result, dict), (
        f"Поле result в ошибке должно быть объектом, получено {type(result).__name__}: {result!r}"
    )
    _assert_exact_keys(result, {"message", "messages"}, "result ошибки")

    status = _assert_non_empty_string(data["status"], "status")
    message = _assert_string(result["message"], "result.message")
    if require_non_empty_message:
        assert message.strip(), f"Поле result.message не должно быть пустым: {result!r}"

    if require_consistent_status and expected_http_status is not None:
        assert status == str(expected_http_status), (
            "Поле status в теле ошибки должно совпадать с HTTP-кодом ответа. "
            f"Ожидалось {expected_http_status}, получено {status!r}."
        )

    return ErrorSchema(
        status=status,
        result=ErrorResultSchema(message=message, messages=result["messages"]),
    )


def _assert_exact_keys(data: dict[str, Any], expected_keys: set[str], context: str) -> None:
    actual_keys = set(data)
    missing = expected_keys - actual_keys
    unexpected = actual_keys - expected_keys
    assert not missing, f"В строгой проверке для {context} не хватает полей {sorted(missing)}: {data!r}"
    assert not unexpected, f"В строгой проверке для {context} есть лишние поля {sorted(unexpected)}: {data!r}"


def _assert_uuid_string(value: Any, field_name: str) -> str:
    raw_value = _assert_non_empty_string(value, field_name)
    try:
        uuid.UUID(raw_value)
    except ValueError as exc:
        raise AssertionError(f"Поле {field_name} должно быть валидным UUID, получено {raw_value!r}") from exc
    return raw_value


def _assert_non_empty_string(value: Any, field_name: str) -> str:
    raw_value = _assert_string(value, field_name)
    assert raw_value.strip(), f"Поле {field_name} не должно быть пустым: {value!r}"
    return raw_value


def _assert_string(value: Any, field_name: str) -> str:
    assert isinstance(value, str), (
        f"Поле {field_name} должно быть строкой, получено {type(value).__name__}: {value!r}"
    )
    return value


def _assert_int(value: Any, field_name: str) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"Поле {field_name} должно быть int, получено {type(value).__name__}: {value!r}"
    )
    return value

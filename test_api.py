from __future__ import annotations

import pytest

from helpers import (
    ApiClient,
    ApiCall,
    DataFactory,
    assert_error_response,
    assert_item_matches_payload,
    assert_json_content_type,
    assert_statistics_match,
    build_request_body,
    build_request_body_without_fields,
    debug_response,
    get_unused_seller_id,
    make_malformed_item_id,
    make_unknown_item_id,
    try_extract_item_id,
    parse_json,
)
from schemas import (
    check_error_schema,
    check_item_schema,
    check_items_schema,
    check_stats_list_schema,
)
from test_data import MISSING_FIELD_CASES, MISSING_STATS_CASES, VERY_LONG_NAME, WRONG_TYPE_CASES


def assert_create_does_not_blow_up(
    api: ApiClient,
    envelope: ApiCall,
    payload: dict,
) -> dict | None:
    assert envelope.response.status_code in {200, 400}, (
        "Сервис должен либо сохранить объявление, либо вернуть клиентскую ошибку, "
        f"но не 5xx/неожиданный статус.\n{debug_response(envelope.response, envelope.elapsed_ms)}"
    )
    if envelope.response.status_code == 400:
        assert_error_response(envelope, 400)
        return None

    created_item_id = try_extract_item_id(parse_json(envelope.response))
    assert created_item_id is not None, "При успешном создании должен извлекаться id объявления."
    created_item, _ = api.get_item_by_id(created_item_id)
    assert created_item["sellerId"] == payload["sellerId"], created_item
    assert created_item["price"] == payload["price"], created_item
    assert created_item["statistics"] == payload["statistics"], created_item
    return created_item


@pytest.mark.smoke
@pytest.mark.regression
def test_can_create_and_read_item(create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="create-and-get", statistics={"likes": 10, "viewCount": 20, "contacts": 30})

    item, _ = create_item(payload)

    assert_item_matches_payload(item, payload)


@pytest.mark.smoke
@pytest.mark.regression
def test_can_get_item_by_id(api: ApiClient, create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="get-by-id", statistics={"likes": 4, "viewCount": 5, "contacts": 6})
    created_item, _ = create_item(payload)

    fetched_item, _ = api.get_item_by_id(created_item["id"])

    assert fetched_item["id"] == created_item["id"]
    assert_item_matches_payload(fetched_item, payload)


@pytest.mark.regression
def test_new_seller_has_empty_list(api: ApiClient, test_data: DataFactory) -> None:
    seller_id = get_unused_seller_id(api, test_data)

    items, _ = api.get_items_by_seller(seller_id)

    assert items == [], f"Для неиспользованного sellerId={seller_id} ожидался пустой список, получено: {items!r}"


@pytest.mark.smoke
@pytest.mark.regression
def test_seller_list_contains_created_items(api: ApiClient, create_item, test_data: DataFactory) -> None:
    seller_id = get_unused_seller_id(api, test_data)
    payload_1 = test_data.payload(tag="seller-list-a", seller_id=seller_id, name_prefix="список-продавца-a")
    payload_2 = test_data.payload(tag="seller-list-b", seller_id=seller_id, name_prefix="список-продавца-b")

    item_1, _ = create_item(payload_1)
    item_2, _ = create_item(payload_2)

    items, _ = api.get_items_by_seller(seller_id)

    returned_ids = {item["id"] for item in items}
    assert returned_ids == {item_1["id"], item_2["id"]}, (
        f"Для sellerId={seller_id} ожидалось ровно два созданных объявления, получено: {items!r}"
    )
    assert all(item["sellerId"] == seller_id for item in items), items


@pytest.mark.smoke
@pytest.mark.regression
def test_stats_v1_match_created_values(api: ApiClient, create_item, test_data: DataFactory) -> None:
    expected_statistics = {"likes": 7, "viewCount": 11, "contacts": 13}
    payload = test_data.payload(tag="statistics-v1", statistics=expected_statistics)
    created_item, _ = create_item(payload)

    statistics, _ = api.get_statistics_v1(created_item["id"])

    assert_statistics_match(statistics, expected_statistics)


@pytest.mark.smoke
@pytest.mark.regression
def test_stats_v2_match_v1(api: ApiClient, create_item, test_data: DataFactory) -> None:
    expected_statistics = {"likes": 9, "viewCount": 15, "contacts": 21}
    payload = test_data.payload(tag="statistics-v2", statistics=expected_statistics)
    created_item, _ = create_item(payload)

    statistics_v1, _ = api.get_statistics_v1(created_item["id"])
    statistics_v2, _ = api.get_statistics_v2(created_item["id"])

    assert_statistics_match(statistics_v1, expected_statistics)
    assert statistics_v2 == statistics_v1, (
        f"Статистика v2 должна быть обратно совместима с v1. v1={statistics_v1!r}, v2={statistics_v2!r}"
    )


@pytest.mark.regression
def test_same_payload_creates_two_items(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    seller_id = get_unused_seller_id(api, test_data)
    payload = test_data.payload(
        tag="same-payload",
        seller_id=seller_id,
        name="дублируемый-бизнес-пейлоад",
        price=777,
        statistics={"likes": 1, "viewCount": 1, "contacts": 1},
    )

    item_1, _ = create_item(payload)
    item_2, _ = create_item(payload)
    items_by_seller, _ = api.get_items_by_seller(seller_id)

    assert item_1["id"] != item_2["id"], (
        f"При повторном создании ожидались разные id, но получен один и тот же id={item_1['id']!r}"
    )
    returned_ids = {item["id"] for item in items_by_seller}
    assert returned_ids == {item_1["id"], item_2["id"]}, items_by_seller


@pytest.mark.regression
def test_repeated_get_returns_same_item(api: ApiClient, create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="repeatable-get", statistics={"likes": 2, "viewCount": 8, "contacts": 16})
    created_item, _ = create_item(payload)

    first_read, _ = api.get_item_by_id(created_item["id"])
    second_read, _ = api.get_item_by_id(created_item["id"])

    assert first_read == second_read, f"Повторный GET вернул разные данные: {first_read!r} vs {second_read!r}"


@pytest.mark.smoke
@pytest.mark.regression
def test_delete_removes_item(api: ApiClient, create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="delete")
    created_item, _ = create_item(payload)

    delete_envelope = api.delete_item(created_item["id"])
    get_after_delete = api.get_item_by_id_raw(created_item["id"])

    assert delete_envelope.response.status_code == 200
    assert_error_response(get_after_delete, 404)


@pytest.mark.regression
def test_repeated_delete_keeps_item_deleted(api: ApiClient, create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="delete-idempotent")
    created_item, _ = create_item(payload)

    first_delete = api.delete_item(created_item["id"])
    second_delete = api.delete_item_raw(created_item["id"])
    get_after_second_delete = api.get_item_by_id_raw(created_item["id"])

    assert first_delete.response.status_code == 200
    assert second_delete.response.status_code in {200, 404}, (
        "Повторный DELETE не должен приводить к серверной ошибке.\n"
        f"Фактический статус={second_delete.response.status_code}, тело={second_delete.response.text!r}"
    )
    assert second_delete.response.status_code < 500, debug_response(second_delete.response, second_delete.elapsed_ms)
    assert_error_response(get_after_second_delete, 404)


@pytest.mark.known_bugs
@pytest.mark.xfail(
    strict=True,
    reason="BUG-API-007: сервис трактует нулевые значения statistics как отсутствие обязательных полей",
)
def test_create_item_with_zero_statistics_succeeds(create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="zero-statistics", statistics={"likes": 0, "viewCount": 0, "contacts": 0})

    item, _ = create_item(payload)

    assert_item_matches_payload(item, payload)


@pytest.mark.regression
def test_create_item_with_large_positive_values_succeeds(create_item, test_data: DataFactory) -> None:
    payload = test_data.payload(
        tag="large-values",
        price=2_147_483_647,
        statistics={"likes": 999_999, "viewCount": 888_888, "contacts": 777_777},
    )

    item, _ = create_item(payload)

    assert_item_matches_payload(item, payload)


@pytest.mark.regression
def test_create_item_with_empty_name_is_handled_without_5xx(api: ApiClient, test_data: DataFactory) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="empty-name", name="")
    body = build_request_body(payload, field_name)

    envelope = api.create_item_raw(body)
    created_item = assert_create_does_not_blow_up(api, envelope, payload)

    if created_item is not None:
        assert created_item["name"] == ""


@pytest.mark.regression
def test_create_item_with_long_name_is_handled_without_5xx(api: ApiClient, test_data: DataFactory) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="long-name", name=VERY_LONG_NAME)
    body = build_request_body(payload, field_name)

    envelope = api.create_item_raw(body)
    created_item = assert_create_does_not_blow_up(api, envelope, payload)

    if created_item is not None:
        assert created_item["name"] == VERY_LONG_NAME


@pytest.mark.regression
def test_create_item_with_unexpected_extra_field_is_handled_without_5xx(api: ApiClient, test_data: DataFactory) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="extra-field")
    body = build_request_body(payload, field_name)
    body["unexpectedField"] = {"nested": True}

    envelope = api.create_item_raw(body)
    created_item = assert_create_does_not_blow_up(api, envelope, payload)

    if created_item is not None:
        assert "unexpectedField" not in created_item


@pytest.mark.regression
@pytest.mark.parametrize("missing_field", MISSING_FIELD_CASES)
def test_create_item_missing_required_field_returns_400(
    api: ApiClient,
    test_data: DataFactory,
    missing_field: str,
) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag=f"missing-{missing_field}")
    body = build_request_body_without_fields(payload, field_name, fields_to_remove={missing_field})

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)


@pytest.mark.regression
@pytest.mark.parametrize(("field_name_key", "invalid_value"), WRONG_TYPE_CASES)
def test_create_item_with_wrong_field_type_returns_400(
    api: ApiClient,
    test_data: DataFactory,
    field_name_key: str,
    invalid_value: object,
) -> None:
    request_field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag=f"wrong-type-{field_name_key}")
    body = build_request_body(payload, request_field_name)
    request_key = request_field_name if field_name_key == "sellerId" else field_name_key
    body[request_key] = invalid_value

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)


@pytest.mark.regression
@pytest.mark.parametrize("missing_statistics_field", MISSING_STATS_CASES)
def test_create_item_with_missing_statistics_field_returns_400(
    api: ApiClient,
    test_data: DataFactory,
    missing_statistics_field: str,
) -> None:
    request_field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag=f"missing-statistics-{missing_statistics_field}")
    body = build_request_body(payload, request_field_name)
    body["statistics"] = dict(body["statistics"])
    body["statistics"].pop(missing_statistics_field, None)

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)


@pytest.mark.regression
def test_get_unknown_item_returns_404(api: ApiClient) -> None:
    envelope = api.get_item_by_id_raw(make_unknown_item_id())

    assert_error_response(envelope, 404)


@pytest.mark.regression
def test_get_malformed_item_returns_400(api: ApiClient) -> None:
    envelope = api.get_item_by_id_raw(make_malformed_item_id())

    assert_error_response(envelope, 400)


@pytest.mark.regression
def test_get_items_by_invalid_seller_id_returns_400(api: ApiClient) -> None:
    envelope = api.get_items_by_seller_raw("not-a-number")

    assert_error_response(envelope, 400)


@pytest.mark.regression
def test_get_statistics_v1_for_unknown_item_returns_404(api: ApiClient) -> None:
    envelope = api.get_statistics_v1_raw(make_unknown_item_id())

    assert_error_response(envelope, 404)


@pytest.mark.regression
def test_get_statistics_v1_for_malformed_item_returns_400(api: ApiClient) -> None:
    envelope = api.get_statistics_v1_raw(make_malformed_item_id())

    assert_error_response(envelope, 400)


@pytest.mark.regression
def test_get_statistics_v2_for_unknown_item_returns_404(api: ApiClient) -> None:
    envelope = api.get_statistics_v2_raw(make_unknown_item_id())

    assert_error_response(envelope, 404)


@pytest.mark.contract
def test_get_item_by_id_response_matches_collection_shape(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    payload = test_data.payload(tag="strict-get-item")
    created_item, _ = create_item(payload)

    envelope = api.get_item_by_id_raw(created_item["id"])

    assert envelope.response.status_code == 200, debug_response(envelope.response, envelope.elapsed_ms)
    assert_json_content_type(envelope.response)
    items = check_items_schema(parse_json(envelope.response), expected_len=1)
    item = items[0]
    assert item.seller_id == payload["sellerId"]
    assert item.name == payload["name"]
    assert item.price == payload["price"]
    assert item.statistics.likes == payload["statistics"]["likes"]
    assert item.statistics.view_count == payload["statistics"]["viewCount"]
    assert item.statistics.contacts == payload["statistics"]["contacts"]


@pytest.mark.contract
def test_get_items_by_seller_response_matches_collection_shape(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    seller_id = get_unused_seller_id(api, test_data)
    payload_1 = test_data.payload(tag="strict-seller-a", seller_id=seller_id)
    payload_2 = test_data.payload(tag="strict-seller-b", seller_id=seller_id)
    item_1, _ = create_item(payload_1)
    item_2, _ = create_item(payload_2)

    envelope = api.get_items_by_seller_raw(seller_id)

    assert envelope.response.status_code == 200, debug_response(envelope.response, envelope.elapsed_ms)
    assert_json_content_type(envelope.response)
    items = check_items_schema(parse_json(envelope.response))
    returned_ids = {item.id for item in items}
    assert returned_ids == {item_1["id"], item_2["id"]}


@pytest.mark.contract
def test_get_statistics_v1_response_matches_collection_shape(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    payload = test_data.payload(tag="strict-statistics-v1", statistics={"likes": 3, "viewCount": 6, "contacts": 9})
    created_item, _ = create_item(payload)

    envelope = api.get_statistics_v1_raw(created_item["id"])

    assert envelope.response.status_code == 200, debug_response(envelope.response, envelope.elapsed_ms)
    assert_json_content_type(envelope.response)
    statistics = check_stats_list_schema(parse_json(envelope.response), expected_len=1)[0]
    assert statistics.likes == payload["statistics"]["likes"]
    assert statistics.view_count == payload["statistics"]["viewCount"]
    assert statistics.contacts == payload["statistics"]["contacts"]


@pytest.mark.contract
def test_get_statistics_v2_response_matches_collection_shape(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    payload = test_data.payload(tag="strict-statistics-v2", statistics={"likes": 5, "viewCount": 10, "contacts": 15})
    created_item, _ = create_item(payload)

    envelope = api.get_statistics_v2_raw(created_item["id"])

    assert envelope.response.status_code == 200, debug_response(envelope.response, envelope.elapsed_ms)
    assert_json_content_type(envelope.response)
    statistics = check_stats_list_schema(parse_json(envelope.response), expected_len=1)[0]
    assert statistics.likes == payload["statistics"]["likes"]
    assert statistics.view_count == payload["statistics"]["viewCount"]
    assert statistics.contacts == payload["statistics"]["contacts"]


@pytest.mark.contract
def test_get_unknown_item_error_matches_error_schema(api: ApiClient) -> None:
    envelope = api.get_item_by_id_raw(make_unknown_item_id())

    assert_error_response(envelope, 404)
    check_error_schema(parse_json(envelope.response), expected_http_status=404)


@pytest.mark.contract
def test_get_malformed_item_error_matches_error_schema(api: ApiClient) -> None:
    envelope = api.get_item_by_id_raw(make_malformed_item_id())

    assert_error_response(envelope, 400)
    check_error_schema(parse_json(envelope.response), expected_http_status=400)


@pytest.mark.contract
def test_get_statistics_v1_unknown_item_error_matches_error_schema(api: ApiClient) -> None:
    envelope = api.get_statistics_v1_raw(make_unknown_item_id())

    assert_error_response(envelope, 404)
    check_error_schema(parse_json(envelope.response), expected_http_status=404)


@pytest.mark.contract
def test_get_statistics_v1_malformed_item_error_matches_error_schema(api: ApiClient) -> None:
    envelope = api.get_statistics_v1_raw(make_malformed_item_id())

    assert_error_response(envelope, 400)
    check_error_schema(parse_json(envelope.response), expected_http_status=400)


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(strict=True, reason="BUG-API-001: POST /api/1/item возвращает status-строку вместо объекта объявления")
def test_create_item_response_matches_collection_contract(api: ApiClient, test_data: DataFactory) -> None:
    payload = test_data.payload(tag="bug-api-001", statistics={"likes": 12, "viewCount": 24, "contacts": 36})
    field_name = api.ensure_seller_field_name()
    body = build_request_body(payload, field_name)

    envelope = api.create_item_raw(body)

    assert envelope.response.status_code == 200, debug_response(envelope.response, envelope.elapsed_ms, body)
    assert_json_content_type(envelope.response)
    created_item = check_item_schema(parse_json(envelope.response))
    assert created_item.seller_id == payload["sellerId"]
    assert created_item.name == payload["name"]
    assert created_item.price == payload["price"]
    assert created_item.statistics.likes == payload["statistics"]["likes"]
    assert created_item.statistics.view_count == payload["statistics"]["viewCount"]
    assert created_item.statistics.contacts == payload["statistics"]["contacts"]


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(
    strict=True,
    reason="BUG-API-002: повторный DELETE возвращает HTTP 404, но status=500 в теле ошибки",
)
def test_repeated_delete_returns_consistent_not_found_payload(
    api: ApiClient,
    create_item,
    test_data: DataFactory,
) -> None:
    payload = test_data.payload(tag="bug-api-002")
    created_item, _ = create_item(payload)

    api.delete_item(created_item["id"])
    second_delete = api.delete_item_raw(created_item["id"])

    assert_error_response(second_delete, 404)
    check_error_schema(parse_json(second_delete.response), expected_http_status=404)


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(strict=True, reason="BUG-API-003: сервис принимает отрицательный price")
def test_create_item_with_negative_price_returns_400(api: ApiClient, test_data: DataFactory) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="bug-api-003", price=-1)
    body = build_request_body(payload, field_name)

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(strict=True, reason="BUG-API-004: сервис принимает отрицательные значения statistics")
def test_create_item_with_negative_likes_returns_400(api: ApiClient, test_data: DataFactory) -> None:
    field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="bug-api-004", statistics={"likes": -1, "viewCount": 2, "contacts": 3})
    body = build_request_body(payload, field_name)

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(
    strict=True,
    reason="BUG-API-005: malformed UUID в /api/2/statistic/{id} возвращает неконсистентные HTTP/body status",
)
def test_get_statistics_v2_for_malformed_item_has_consistent_error_status(api: ApiClient) -> None:
    envelope = api.get_statistics_v2_raw(make_malformed_item_id())

    assert 400 <= envelope.response.status_code < 500, (
        f"Некорректный id должен возвращать клиентскую ошибку, получено {envelope.response.status_code}.\n"
        + debug_response(envelope.response, envelope.elapsed_ms)
    )
    check_error_schema(parse_json(envelope.response), expected_http_status=envelope.response.status_code)


@pytest.mark.contract
@pytest.mark.known_bugs
@pytest.mark.xfail(
    strict=True,
    reason="BUG-API-006: type-validation ошибки возвращают неконсистентный status и пустой result.message",
)
def test_create_item_type_validation_error_matches_strict_error_schema(
    api: ApiClient,
    test_data: DataFactory,
) -> None:
    request_field_name = api.ensure_seller_field_name()
    payload = test_data.payload(tag="bug-api-006")
    body = build_request_body(payload, request_field_name)
    body[request_field_name] = "not-an-integer"

    envelope = api.create_item_raw(body)

    assert_error_response(envelope, 400)
    check_error_schema(
        parse_json(envelope.response),
        expected_http_status=400,
        require_consistent_status=True,
        require_non_empty_message=True,
    )

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests


BASE_URL = os.getenv("BASE_URL", "https://qa-internship.avito.com").rstrip("/")
API_TIMEOUT_SECONDS = float(os.getenv("API_TIMEOUT_SECONDS", "15"))
SELLER_FIELD_MODE = os.getenv("SELLER_FIELD_MODE", "auto").strip().lower()
TEST_RUN_ID = os.getenv("TEST_RUN_ID", uuid.uuid4().hex[:8])
UUID_IN_TEXT_RE = re.compile(
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


@dataclass(frozen=True)
class ApiCall:
    response: requests.Response
    elapsed_ms: float


class CleanupTracker:
    def __init__(self, api: ApiClient) -> None:
        self.api = api
        self.created_item_ids: set[str] = set()

    def register(self, item_id: str) -> None:
        self.created_item_ids.add(str(item_id))

    def cleanup(self) -> None:
        for item_id in reversed(sorted(self.created_item_ids)):
            self.api.delete_item_best_effort(item_id)


class DataFactory:
    def __init__(self, run_id: str, nodeid: str) -> None:
        self.run_id = run_id
        self.nodeid = nodeid

    def seller_id(self, tag: str = "default") -> int:
        return 111_111 + (self._seed_int("seller_id", tag) % 888_889)

    def name(self, prefix: str = "тест-объявление", tag: str = "default") -> str:
        return f"{prefix}-{self.run_id}-{self._seed_hex('name', tag)[:12]}"

    def payload(
        self,
        *,
        tag: str = "default",
        seller_tag: str = "default",
        seller_id: int | None = None,
        name: str | None = None,
        price: int = 12345,
        statistics: dict[str, int] | None = None,
        name_prefix: str = "тест-объявление",
    ) -> dict[str, Any]:
        return make_payload(
            seller_id=seller_id if seller_id is not None else self.seller_id(seller_tag),
            name=name if name is not None else self.name(name_prefix, tag),
            price=price,
            statistics=statistics,
            name_prefix=name_prefix,
        )

    def _seed_hex(self, kind: str, tag: str) -> str:
        source = f"{self.run_id}|{self.nodeid}|{kind}|{tag}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    def _seed_int(self, kind: str, tag: str) -> int:
        return int(self._seed_hex(kind, tag)[:16], 16)


class ApiClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout_seconds: float = API_TIMEOUT_SECONDS,
        seller_field_mode: str = SELLER_FIELD_MODE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.seller_field_mode = seller_field_mode
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.seller_field_name: str | None = None if seller_field_mode == "auto" else seller_field_mode
        self.cleanup_tracker: CleanupTracker | None = None

    def close(self) -> None:
        self.session.close()

    def attach_cleanup(self, tracker: CleanupTracker) -> None:
        self.cleanup_tracker = tracker

    def detach_cleanup(self, tracker: CleanupTracker | None = None) -> None:
        if tracker is None or self.cleanup_tracker is tracker:
            self.cleanup_tracker = None

    def request(self, method: str, path: str, **kwargs: Any) -> ApiCall:
        url = f"{self.base_url}{path}"
        started = time.perf_counter()
        response = self.session.request(method=method, url=url, timeout=self.timeout_seconds, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ApiCall(response=response, elapsed_ms=elapsed_ms)

    def ensure_seller_field_name(self) -> str:
        if self.seller_field_name == "sellerid":
            self.seller_field_name = "sellerId"
        if self.seller_field_name in {"sellerId", "sellerID"}:
            return self.seller_field_name

        assert self.seller_field_mode == "auto", (
            "Неподдерживаемое значение SELLER_FIELD_MODE. "
            "Используйте auto, sellerId или sellerID. "
            f"Получено: {self.seller_field_mode!r}"
        )

        probe_payload = make_payload(name_prefix="проверка")
        candidate_fields = ["sellerID", "sellerId"]
        attempts: list[str] = []
        created_item_id: str | None = None
        chosen_field: str | None = None

        for field_name in candidate_fields:
            body = build_request_body(probe_payload, field_name=field_name)
            envelope = self.request(
                "POST",
                "/api/1/item",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            attempts.append(debug_response(envelope.response, envelope.elapsed_ms, field_name))
            if envelope.response.status_code == 200:
                assert_json_content_type(envelope.response)
                parsed = parse_json(envelope.response)
                created_item_id = extract_item_id(parsed)
                chosen_field = field_name
                break

        assert chosen_field is not None, (
            "Не удалось автоматически определить имя поля продавца в запросе. "
            "Были проверены sellerID и sellerId. Попытки:\n\n" + "\n\n".join(attempts)
        )

        self.seller_field_name = chosen_field

        if created_item_id is not None:
            self.delete_item_best_effort(created_item_id)

        return self.seller_field_name

    def create_item(self, payload: dict[str, Any]) -> tuple[dict[str, Any], ApiCall]:
        field_name = self.ensure_seller_field_name()
        body = build_request_body(payload, field_name=field_name)
        envelope = self.create_item_raw(body)
        assert envelope.response.status_code == 200, (
            "Не удалось создать объявление.\n" + debug_response(envelope.response, envelope.elapsed_ms, body)
        )
        assert_json_content_type(envelope.response)
        created_item_id = extract_item_id(parse_json(envelope.response))
        item, _ = self.get_item_by_id(created_item_id)
        return item, envelope

    def create_item_raw(self, body: dict[str, Any]) -> ApiCall:
        envelope = self.request(
            "POST",
            "/api/1/item",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        self._register_created_item_from_envelope(envelope)
        return envelope

    def get_item_by_id(self, item_id: str) -> tuple[dict[str, Any], ApiCall]:
        envelope = self.request("GET", f"/api/1/item/{item_id}")
        assert envelope.response.status_code == 200, (
            f"Не удалось получить объявление по id={item_id!r}.\n"
            + debug_response(envelope.response, envelope.elapsed_ms)
        )
        assert_json_content_type(envelope.response)
        item = parse_item_response(parse_json(envelope.response), expected_id=item_id)
        return item, envelope

    def get_item_by_id_raw(self, item_id: str) -> ApiCall:
        return self.request("GET", f"/api/1/item/{item_id}")

    def get_items_by_seller(self, seller_id: int) -> tuple[list[dict[str, Any]], ApiCall]:
        envelope = self.request("GET", f"/api/1/{seller_id}/item")
        assert envelope.response.status_code == 200, (
            f"Не удалось получить объявления продавца seller_id={seller_id!r}.\n"
            + debug_response(envelope.response, envelope.elapsed_ms)
        )
        assert_json_content_type(envelope.response)
        items = parse_items_list(parse_json(envelope.response))
        return items, envelope

    def get_items_by_seller_raw(self, seller_id: Any) -> ApiCall:
        return self.request("GET", f"/api/1/{seller_id}/item")

    def get_statistics_v1(self, item_id: str) -> tuple[dict[str, int], ApiCall]:
        envelope = self.request("GET", f"/api/1/statistic/{item_id}")
        assert envelope.response.status_code == 200, (
            f"Не удалось получить статистику v1 для id={item_id!r}.\n"
            + debug_response(envelope.response, envelope.elapsed_ms)
        )
        assert_json_content_type(envelope.response)
        statistics = parse_stats_response(parse_json(envelope.response))
        return statistics, envelope

    def get_statistics_v1_raw(self, item_id: str) -> ApiCall:
        return self.request("GET", f"/api/1/statistic/{item_id}")

    def get_statistics_v2(self, item_id: str) -> tuple[dict[str, int], ApiCall]:
        envelope = self.request("GET", f"/api/2/statistic/{item_id}")
        assert envelope.response.status_code == 200, (
            f"Не удалось получить статистику v2 для id={item_id!r}.\n"
            + debug_response(envelope.response, envelope.elapsed_ms)
        )
        assert_json_content_type(envelope.response)
        statistics = parse_stats_response(parse_json(envelope.response))
        return statistics, envelope

    def get_statistics_v2_raw(self, item_id: str) -> ApiCall:
        return self.request("GET", f"/api/2/statistic/{item_id}")

    def delete_item(self, item_id: str) -> ApiCall:
        envelope = self.request("DELETE", f"/api/2/item/{item_id}")
        assert envelope.response.status_code == 200, (
            f"Не удалось удалить объявление с id={item_id!r}.\n"
            + debug_response(envelope.response, envelope.elapsed_ms)
        )
        return envelope

    def delete_item_raw(self, item_id: str) -> ApiCall:
        return self.request("DELETE", f"/api/2/item/{item_id}")

    def delete_item_best_effort(self, item_id: str) -> None:
        try:
            envelope = self.delete_item_raw(item_id)
            if envelope.response.status_code not in {200, 404}:
                print(
                    "[ПРЕДУПРЕЖДЕНИЕ] Очистка через DELETE вернула неожиданный статус:\n"
                    + debug_response(envelope.response, envelope.elapsed_ms)
                )
        except Exception as exc:  # pragma: no cover - best effort cleanup
            print(f"[ПРЕДУПРЕЖДЕНИЕ] Не удалось выполнить очистку для id={item_id!r}: {exc}")

    def _register_created_item_from_envelope(self, envelope: ApiCall) -> None:
        if self.cleanup_tracker is None or envelope.response.status_code != 200:
            return

        try:
            parsed = parse_json(envelope.response)
        except AssertionError:
            return

        created_item_id = try_extract_item_id(parsed)
        if created_item_id is not None:
            self.cleanup_tracker.register(created_item_id)


def make_unique_seller_id() -> int:
    return 111_111 + (uuid.uuid4().int % 888_889)


def make_unique_name(prefix: str = "тест-объявление") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def make_payload(
    *,
    seller_id: int | None = None,
    name: str | None = None,
    price: int = 12345,
    statistics: dict[str, int] | None = None,
    name_prefix: str = "тест-объявление",
) -> dict[str, Any]:
    return {
        "sellerId": seller_id if seller_id is not None else make_unique_seller_id(),
        "name": name if name is not None else make_unique_name(name_prefix),
        "price": price,
        "statistics": statistics if statistics is not None else {"likes": 1, "viewCount": 2, "contacts": 3},
    }


def build_request_body(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    assert field_name in {"sellerID", "sellerId"}, f"Неожиданное имя поля продавца: {field_name!r}"
    body = {
        field_name: payload["sellerId"],
        "name": payload["name"],
        "price": payload["price"],
        "statistics": payload["statistics"],
    }
    return body


def build_request_body_without_fields(
    payload: dict[str, Any],
    field_name: str,
    fields_to_remove: set[str] | None = None,
) -> dict[str, Any]:
    fields_to_remove = fields_to_remove or set()
    body = build_request_body(payload, field_name)
    normalized_to_request_key = {
        "sellerId": field_name,
        "sellerID": field_name,
        "name": "name",
        "price": "price",
        "statistics": "statistics",
    }
    for field in fields_to_remove:
        body.pop(normalized_to_request_key[field], None)
    return body


def pick_single_item(data: Any) -> dict[str, Any]:
    return parse_item_response(data, expected_id=None)


def extract_item_id(data: Any) -> str:
    candidate_id = try_extract_item_id(data)
    assert candidate_id is not None, f"Не удалось извлечь id созданного объявления из ответа: {data!r}"
    return candidate_id


def try_extract_item_id(data: Any) -> str | None:
    if isinstance(data, dict):
        raw_id = data.get("id")
        if raw_id:
            return str(raw_id)

        status_text = data.get("status")
        if isinstance(status_text, str):
            match = UUID_IN_TEXT_RE.search(status_text)
            if match:
                return match.group("uuid")

    if isinstance(data, list):
        try:
            return pick_single_item(data)["id"]
        except AssertionError:
            return None

    return None


def parse_item_response(data: Any, expected_id: str | None) -> dict[str, Any]:
    if isinstance(data, dict):
        candidate_items = [data]
    elif isinstance(data, list):
        candidate_items = [item for item in data if isinstance(item, dict)]
    else:
        raise AssertionError(
            f"Ожидался объект или список в ответе объявления, получено {type(data).__name__}: {data!r}"
        )

    assert candidate_items, f"Ответ объявления не содержит объектов: {data!r}"

    if expected_id is None:
        assert len(candidate_items) == 1, (
            "Ожидался один объект созданного объявления в ответе, но получено несколько: "
            + json.dumps(data, ensure_ascii=False)
        )
        raw_item = candidate_items[0]
    else:
        matches = [item for item in candidate_items if str(item.get("id")) == str(expected_id)]
        assert len(matches) == 1, (
            f"Ожидался ровно один объект с id={expected_id!r}, но найдено {len(matches)} совпадений. "
            f"Полный ответ: {json.dumps(data, ensure_ascii=False)}"
        )
        raw_item = matches[0]

    return parse_item(raw_item)


def parse_items_list(data: Any) -> list[dict[str, Any]]:
    assert isinstance(data, list), (
        f"Ожидался список в ответе объявлений продавца, получено {type(data).__name__}: {data!r}"
    )
    return [parse_item(item) for item in data]


def parse_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    required_fields = ["id", "name", "price", "statistics", "createdAt"]
    missing = [field for field in required_fields if field not in raw_item]
    assert not missing, f"В ответе объявления отсутствуют поля {missing}: {raw_item!r}"

    seller_value = raw_item.get("sellerId", raw_item.get("sellerID"))
    assert seller_value is not None, f"В ответе объявления отсутствует sellerId/sellerID: {raw_item!r}"

    item = {
        "id": str(raw_item["id"]),
        "sellerId": int(seller_value),
        "name": str(raw_item["name"]),
        "price": int(raw_item["price"]),
        "statistics": parse_stats(raw_item["statistics"]),
        "createdAt": str(raw_item["createdAt"]),
    }
    assert_created_at_present(item["createdAt"])
    return item


def parse_stats_response(data: Any) -> dict[str, int]:
    if isinstance(data, dict):
        candidate_stats = [data]
    elif isinstance(data, list):
        candidate_stats = [item for item in data if isinstance(item, dict)]
    else:
        raise AssertionError(
            f"Ожидался объект или список в ответе статистики, получено {type(data).__name__}: {data!r}"
        )

    assert candidate_stats, f"Ответ статистики не содержит объектов: {data!r}"

    normalized = [parse_stats(item) for item in candidate_stats]
    first = normalized[0]
    for idx, current in enumerate(normalized[1:], start=1):
        assert current == first, (
            "Ответ статистики содержит противоречивые объекты. "
            f"Индекс 0: {first!r}, индекс {idx}: {current!r}, исходный ответ={data!r}"
        )
    return first


def parse_stats(raw_statistics: Any) -> dict[str, int]:
    assert isinstance(raw_statistics, dict), (
        f"Ожидался объект statistics, получено {type(raw_statistics).__name__}: {raw_statistics!r}"
    )
    required_fields = ["likes", "viewCount", "contacts"]
    missing = [field for field in required_fields if field not in raw_statistics]
    assert not missing, f"В объекте statistics отсутствуют поля {missing}: {raw_statistics!r}"

    normalized = {field: int(raw_statistics[field]) for field in required_fields}
    for field, value in normalized.items():
        assert value >= 0, f"Поле statistics {field!r} должно быть неотрицательным, получено {value!r}"
    return normalized


def parse_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise AssertionError(
            f"Ответ не является валидным JSON. код_ответа={response.status_code}, тело={response.text!r}"
        ) from exc


def assert_json_content_type(response: requests.Response) -> None:
    content_type = response.headers.get("Content-Type", "")
    assert "application/json" in content_type.lower(), (
        f"Ожидался Content-Type с application/json, получено {content_type!r}. Тело: {response.text!r}"
    )


def assert_error_response(envelope: ApiCall, expected_status: int) -> dict[str, Any]:
    assert envelope.response.status_code == expected_status, (
        f"Ожидался статус {expected_status}, получен {envelope.response.status_code}.\n"
        + debug_response(envelope.response, envelope.elapsed_ms)
    )
    assert_json_content_type(envelope.response)
    body = parse_json(envelope.response)
    assert isinstance(body, dict), f"Ожидался JSON-объект в теле ошибки, получено: {body!r}"
    return body


def assert_item_matches_payload(item: dict[str, Any], payload: dict[str, Any]) -> None:
    assert item["sellerId"] == payload["sellerId"], f"Не совпадает sellerId: {item!r} vs {payload!r}"
    assert item["name"] == payload["name"], f"Не совпадает name: {item!r} vs {payload!r}"
    assert item["price"] == payload["price"], f"Не совпадает price: {item!r} vs {payload!r}"
    assert item["statistics"] == payload["statistics"], f"Не совпадает statistics: {item!r} vs {payload!r}"
    assert item["id"].strip(), f"Поле id не должно быть пустым: {item!r}"
    assert item["createdAt"].strip(), f"Поле createdAt не должно быть пустым: {item!r}"


def assert_statistics_match(actual: dict[str, int], expected: dict[str, int]) -> None:
    assert actual == expected, f"Не совпадает statistics: actual={actual!r}, expected={expected!r}"


def assert_created_at_present(value: str) -> None:
    assert isinstance(value, str), f"Поле createdAt должно быть строкой, получено {type(value).__name__}: {value!r}"
    assert value.strip(), f"Поле createdAt не должно быть пустым: {value!r}"


def make_unknown_item_id() -> str:
    return "00000000-0000-0000-0000-000000000000"


def make_malformed_item_id() -> str:
    return "not-a-uuid"


def get_unused_seller_id(api: ApiClient, data_factory: DataFactory, attempts: int = 10) -> int:
    last_observed_size: int | None = None
    for attempt in range(attempts):
        seller_id = data_factory.seller_id(f"unused-seller-{attempt}")
        items, _ = api.get_items_by_seller(seller_id)
        last_observed_size = len(items)
        if not items:
            return seller_id
    raise AssertionError(
        "Не удалось найти свободный sellerId за несколько попыток. "
        f"Последний размер списка был {last_observed_size!r}."
    )


def debug_response(response: requests.Response, elapsed_ms: float, payload: Any | None = None) -> str:
    try:
        body_text = response.text
    except Exception:
        body_text = "<не удалось прочитать тело ответа>"

    pieces = [
        f"статус={response.status_code}",
        f"время_мс={elapsed_ms:.2f}",
        f"заголовки={dict(response.headers)!r}",
        f"тело={body_text[:1000]!r}",
    ]
    if payload is not None:
        pieces.append(f"данные={payload!r}")
    return "\n".join(pieces)

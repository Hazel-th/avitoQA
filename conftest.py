from __future__ import annotations

import pytest

from helpers import ApiClient, CleanupTracker, DataFactory, TEST_RUN_ID


@pytest.fixture(scope="session")
def api() -> ApiClient:
    client = ApiClient()
    client.ensure_seller_field_name()
    yield client
    client.close()


@pytest.fixture(autouse=True)
def cleanup_tracker(api: ApiClient):
    tracker = CleanupTracker(api)
    api.attach_cleanup(tracker)
    yield tracker
    api.detach_cleanup(tracker)
    tracker.cleanup()


@pytest.fixture
def test_data(request) -> DataFactory:
    return DataFactory(run_id=TEST_RUN_ID, nodeid=request.node.nodeid)


@pytest.fixture
def create_item(api: ApiClient, cleanup_tracker: CleanupTracker):
    _ = cleanup_tracker

    def _create(payload: dict):
        return api.create_item(payload)

    return _create

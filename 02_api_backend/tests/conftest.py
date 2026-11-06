import pytest

import api_backend.main as main


@pytest.fixture(autouse=True)
def _reset_ask_limit():
    """Tests share one client address; start each test with an empty per-visitor question count."""
    main._ask_times.clear()
    main._route_times.clear()
    yield

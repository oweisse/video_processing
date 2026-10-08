import pytest

from tests import fixtures


def pytest_collection_modifyitems(config, items):
    if not fixtures.have_tools():
        skip = pytest.mark.skip(reason="needs macOS `say` and ffmpeg on PATH")
        for item in items:
            if "fx" in item.fixturenames:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    """The synthetic fixture set, built once per test session."""
    return fixtures.build(tmp_path_factory.mktemp("fixture"))

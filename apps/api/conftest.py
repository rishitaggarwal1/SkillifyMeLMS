"""Root conftest: shared fixtures live in tests/fixtures.py so module tests
(app/modules/*/tests) can use them too."""

pytest_plugins = ["tests.fixtures", "tests.content_world"]

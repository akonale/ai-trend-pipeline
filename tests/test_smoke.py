"""Smoke tests for the project skeleton."""


def test_package_imports() -> None:
    import ai_trend_pipeline

    assert isinstance(ai_trend_pipeline.__version__, str)
    assert ai_trend_pipeline.__version__ != ""


def test_core_deps_importable() -> None:
    import pydantic
    import temporalio

    assert temporalio is not None
    assert pydantic.VERSION.startswith("2")

def test_passes() -> None:
    assert 1 + 1 == 2


def test_fails() -> None:
    assert "needle" in "haystack"

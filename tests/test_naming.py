from pathlib import Path

from qvd_mcp.naming import normalize, view_name_for


def test_normalize_basic() -> None:
    assert normalize("Sales") == "sales"


def test_normalize_spaces_and_punct() -> None:
    assert normalize("Sales 2024") == "sales_2024"
    assert normalize("Sales-2024_Q1") == "sales_2024_q1"


def test_normalize_leading_digit_gets_underscore() -> None:
    assert normalize("7eleven") == "_7eleven"


def test_normalize_reserved_word_gets_view_suffix() -> None:
    assert normalize("Order") == "order_view"
    assert normalize("SELECT") == "select_view"
    assert normalize("user") == "user_view"


def test_normalize_empty_stems_fall_back_to_qvd() -> None:
    assert normalize("") == "qvd"
    assert normalize("___") == "qvd"
    assert normalize("---") == "qvd"


def test_normalize_unicode_collapses_to_underscore() -> None:
    # Non-ASCII runs collapse into single underscores, per the identifier regex.
    assert normalize("Försäljning") == "f_rs_ljning"


def test_normalize_multiple_separators_collapse() -> None:
    assert normalize("a--b  c__d") == "a_b_c_d"


def test_view_name_for_first_use(tmp_path: Path) -> None:
    assert view_name_for(tmp_path / "Sales.qvd", taken=set()) == "sales"


def test_view_name_for_single_collision(tmp_path: Path) -> None:
    assert view_name_for(tmp_path / "Sales.qvd", taken={"sales"}) == "sales_2"


def test_view_name_for_multiple_collisions(tmp_path: Path) -> None:
    assert view_name_for(tmp_path / "sales.qvd", taken={"sales", "sales_2"}) == "sales_3"

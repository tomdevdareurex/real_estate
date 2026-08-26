import pytest

from aruodas_scraper.pipelines.online import selected_categories


@pytest.mark.unit
@pytest.mark.parametrize(
    ("property_type", "deal_type", "expected"),
    (
        ("apartments", "sale", ("apartments",)),
        ("houses", "sale", ("houses",)),
        ("all", "sale", ("apartments", "houses")),
        ("apartments", "rent", ("apartments_rent",)),
        ("houses", "rent", ("houses_rent",)),
        ("all", "rent", ("apartments_rent", "houses_rent")),
        ("apartments", "all", ("apartments", "apartments_rent")),
        ("all", "all", ("apartments", "houses", "apartments_rent", "houses_rent")),
    ),
)
def test_the_two_axes_resolve_to_the_categories_a_run_walks(
    property_type: str, deal_type: str, expected: tuple[str, ...]
) -> None:
    assert selected_categories(property_type, deal_type) == expected


@pytest.mark.unit
def test_a_run_that_names_no_deal_type_still_walks_the_sale_side() -> None:
    assert selected_categories("all") == ("apartments", "houses")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("property_type", "deal_type"),
    (("flats", "sale"), ("all", "lease")),
)
def test_an_unknown_axis_is_refused_rather_than_silently_narrowed(
    property_type: str, deal_type: str
) -> None:
    with pytest.raises(ValueError):
        selected_categories(property_type, deal_type)

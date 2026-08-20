from pathlib import Path

import pytest

from aruodas_scraper.parsers.apartment import parse_apartment
from aruodas_scraper.parsers.house import parse_house

FIXTURES = Path("tests/fixtures/html")


@pytest.mark.unit
def test_apartment_parser_extracts_primary_listing_only() -> None:
    html = (FIXTURES / "apartment_detail.html").read_text(encoding="utf-8")

    record, unknown = parse_apartment(html, source_search_url="saved://apartments", page=1)

    assert record.listing_id == "1-1234567"
    assert record.property_type == "apartment"
    assert record.price_eur == 148500.0
    assert record.apartment_total_area_sqm == 45.14
    assert record.rooms == 2
    assert record.floor == 10
    assert record.total_floors == 12
    assert record.building_type == "brick"
    assert record.condition == "fully_finished"
    assert record.saved_by_users_count == 26
    assert record.views_count == 1234
    assert record.latitude == 54.712345
    assert record.coordinate_precision == "exact"
    assert record.title_en is None
    assert record.raw_attributes_json["Naujas bandymo laukas"] == "Bandymo reikšmė"
    assert unknown[0].label_lt == "Naujas bandymo laukas"


@pytest.mark.unit
def test_house_parser_separates_house_and_plot_area() -> None:
    html = (FIXTURES / "house_detail.html").read_text(encoding="utf-8")

    record, unknown = parse_house(html, source_search_url="saved://houses", page=1)

    assert not unknown
    assert record.listing_id == "2-1795101"
    assert record.property_type == "house"
    assert record.house_total_area_sqm == 176.7
    assert record.total_area_sqm == 176.7
    assert record.plot_area_original == "10 a"
    assert record.plot_area_sqm == 1000.0
    assert record.plot_area_ares == 10.0
    assert record.number_of_floors == 2
    assert record.energy_class == "A++"
    assert record.garage is True
    assert record.terrace is True
    assert record.coordinate_precision == "approximate"


@pytest.mark.unit
def test_parser_accepts_current_markup_primary_container() -> None:
    html = """
    <!doctype html>
    <html lang="lt">
      <head>
        <link rel="canonical" href="https://www.aruodas.lt/butai-vilniuje-1-1234567/" />
      </head>
      <body>
        <div class="main-content-in-object">
          <h1>Vilnius, Fabijoniškės</h1>
          <dl class="obj-details">
            <dt>Kambarių sk.:</dt><dd>2</dd>
            <dt>Plotas:</dt><dd>45.14 m²</dd>
          </dl>
          <div class="price">148 500 €</div>
        </div>
      </body>
    </html>
    """

    record, _ = parse_apartment(html, source_search_url="saved://apartments", page=1)

    assert record.listing_id == "1-1234567"
    assert record.property_type == "apartment"
    assert record.rooms == 2
    assert record.price_eur == 148500.0


@pytest.mark.unit
def test_parser_extracts_modern_markup_price_description_coordinates_and_stats() -> None:
    html = """
    <!doctype html>
    <html lang="lt">
      <head>
        <link rel="canonical" href="https://www.aruodas.lt/butai-vilniuje-testine-g-butas-1-9990001/" />
        <meta property="og:title" content="Vilnius, Testinė, Bandymo g., 3 kambarių butas" />
        <meta property="og:description" content="Atsarginis apra\u0161ymas i\u0161 meta \u017eymos." />
        <script type="application/ld+json">
        {"@context":"http://schema.org","@type":["Product","Apartment"],"name":"Test","description":"",
         "Offers":{"@type":"Offer","price":"199000","priceCurrency":"EUR"}}
        </script>
        <script>
          const coordinates = '54.700000,25.300000';
        </script>
      </head>
      <body>
        <div class="main-content-in-object">
          <h1 class="obj-header-text-address">Vilnius, Testin\u0117, Bandymo g.</h1>
          <div class="price-block">
            <div class="price-main">
              <span class="price-eur">199 000 \u20ac</span>
              <span class="price-per">(4 200 \u20ac/m\u00b2)</span>
            </div>
          </div>
          <dl class="obj-details">
            <dt>Plotas:</dt><dd>47,4 m\u00b2</dd>
            <dt>Kambari\u0173 sk.:</dt><dd>3</dd>
          </dl>
          <section class="features-list-in-object-section">
            <ul class="features-list-in-object-list">
              <li class="features-list-in-object-item">Balkonas</li>
              <li class="features-list-in-object-item">Liftas</li>
            </ul>
          </section>
          <div id="collapsedTextBlock" class="obj-comment bordered-panel">
            <div id="collapsedText">Testinis skelbimo apra\u0161ymas su naujausiu \u017eym\u0117jimu.</div>
          </div>
        </div>
        <div class="object-info-stats-container">
          <div class="object-info-stats-row">
            <span class="object-info-stats-label">\u012ed\u0117tas</span>
            <span class="object-info-stats-value">2026-01-05</span>
          </div>
          <div class="object-info-stats-row">
            <span class="object-info-stats-label">Redaguotas</span>
            <span class="object-info-stats-value">2026-01-20</span>
          </div>
          <div class="object-info-stats-row">
            <span class="object-info-stats-label">\u012esimin\u0117</span>
            <div class="object-info-stats-value"><span class="remembered-heart">12</span></div>
          </div>
          <div class="object-info-stats-row">
            <span class="object-info-stats-label">Per\u017ei\u016br\u0117jo</span>
            <span class="object-info-stats-value">321/4 (i\u0161 viso/\u0161iandien)</span>
          </div>
        </div>
      </body>
    </html>
    """

    record, _ = parse_apartment(html, source_search_url="saved://apartments", page=1)

    assert record.listing_id == "1-9990001"
    assert record.price_eur == 199000.0
    assert record.price_per_sqm_eur == 4200.0
    assert record.description_lt == "Testinis skelbimo aprašymas su naujausiu žymėjimu."
    assert record.latitude == 54.7
    assert record.longitude == 25.3
    assert record.listing_created_date is not None
    assert record.listing_created_date.isoformat() == "2026-01-05"
    assert record.listing_updated_date is not None
    assert record.listing_updated_date.isoformat() == "2026-01-20"
    assert record.saved_by_users_count == 12
    assert record.views_count == 321
    assert record.balcony is True
    assert record.lift is True
    assert record.total_area_sqm == 47.4
    assert record.apartment_total_area_sqm == 47.4
    assert record.rooms == 3


@pytest.mark.unit
def test_parser_falls_back_to_dom_price_when_json_ld_price_absent() -> None:
    html = """
    <!doctype html>
    <html lang="lt">
      <head>
        <link rel="canonical" href="https://www.aruodas.lt/butai-vilniuje-testine-g-butas-1-9990002/" />
      </head>
      <body>
        <div class="main-content-in-object">
          <h1 class="obj-header-text-address">Vilnius, Testin\u0117, Bandymo g.</h1>
          <div class="price-block">
            <div class="price-main">
              <span class="price-eur">210 000 \u20ac</span>
              <span class="price-per">(4 500 \u20ac/m\u00b2)</span>
            </div>
          </div>
          <dl class="obj-details">
            <dt>Kambari\u0173 sk.:</dt><dd>2</dd>
          </dl>
        </div>
      </body>
    </html>
    """

    record, _ = parse_apartment(html, source_search_url="saved://apartments", page=1)

    assert record.price_eur == 210000.0
    assert record.price_per_sqm_eur == 4500.0


@pytest.mark.unit
def test_parser_extracts_coordinates_from_map_display_destructuring() -> None:
    html = """
    <!doctype html>
    <html lang="lt">
      <head>
        <link rel="canonical" href="https://www.aruodas.lt/butai-vilniuje-testine-g-butas-1-9990003/" />
      </head>
      <body>
        <div class="main-content-in-object">
          <h1 class="obj-header-text-address">Vilnius, Testin\u0117, Bandymo g.</h1>
          <script>
            const staticMapSrc = 'https://aruodas-files.dgn.lt/maps_0_281565/file.webp';
            const useMapKit = false;
            const isMobile = false;
            const [lat, lng] = '54.720077,25.296482'.split(',');
            const mapDisplay = new MapDisplay(document.body, staticMapSrc, labels, '1', '3673712', isMobile, lat, lng);
          </script>
        </div>
      </body>
    </html>
    """

    record, _ = parse_apartment(html, source_search_url="saved://apartments", page=1)

    assert record.latitude == 54.720077
    assert record.longitude == 25.296482


@pytest.mark.unit
def test_parser_ignores_unrelated_script_coordinates() -> None:
    html = (FIXTURES / "apartment_detail.html").read_text(encoding="utf-8")
    contaminated = html.replace(
        "<script>window.mapConfig",
        '<script>const advertisement = {"lat": 55.1, "lng": 24.1};</script>'
        "<script>window.mapConfig",
    )

    record, _ = parse_apartment(contaminated, source_search_url="saved://apartments", page=1)

    assert record.latitude == 54.712345
    assert record.longitude == 25.301234


@pytest.mark.unit
def test_parser_anchors_coordinates_after_map_marker_in_same_script() -> None:
    html = (FIXTURES / "apartment_detail.html").read_text(encoding="utf-8")
    contaminated = html.replace(
        "window.mapConfig =",
        'const advertisement = {"lat": 55.1, "lng": 24.1}; window.mapConfig =',
    )

    record, _ = parse_apartment(contaminated, source_search_url="saved://apartments", page=1)

    assert record.latitude == 54.712345
    assert record.longitude == 25.301234


@pytest.mark.unit
def test_parser_redacts_contact_details_from_description() -> None:
    html = (FIXTURES / "apartment_detail.html").read_text(encoding="utf-8")
    with_contacts = html.replace(
        "Originalus lietuviškas aprašymas.",
        "Rašykite owner@example.com arba skambinkite +370 612 34567.",
    )

    record, _ = parse_apartment(with_contacts, source_search_url="saved://apartments", page=1)

    assert "owner@example.com" not in (record.description_lt or "")
    assert "+370 612 34567" not in (record.description_lt or "")
    assert "[REDACTED_EMAIL]" in (record.description_lt or "")
    assert "[REDACTED_PHONE]" in (record.description_lt or "")

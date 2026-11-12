"""Tests End-to-End (E2E) de l'interface utilisateur web et mobile (PWA) via Playwright."""
import os
import pytest
from playwright.sync_api import Page, sync_playwright

INDEX_HTML_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))
INDEX_URL = f"file:///{INDEX_HTML_PATH.replace(os.sep, '/')}"


@pytest.fixture(scope="module")
def browser_instance():
    """Lance une instance de navigateur Chromium headless pour les tests E2E."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"Navigateur Chromium Playwright non disponible : {e}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(browser_instance):
    """Crée une nouvelle page / onglet isolé avec intercepteur d'API mockées."""
    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()

    # Mock préventif des appels API pour des tests E2E déterministes et instantanés
    def handle_route(route):
        url = route.request.url
        if "/api/latest" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body='''{
                    "status_code": 5,
                    "status_label": "TRAVAIL (Normal)",
                    "temp_ambient": 20.5,
                    "temp_amb_setpoint": 21.0,
                    "temp_fumes": 165.0,
                    "pellet_hopper_pct": 82.0,
                    "pellet_daily_kg": 4.5,
                    "comm_status": {"connected": true, "comm_mode": "simulation"}
                }''',
            )
        elif "/api/silence/status" in url:
            route.fulfill(status=200, content_type="application/json", body='{"active": false}')
        elif "/api/dashboard" in url:
            route.fulfill(status=200, content_type="application/json", body='{"series": []}')
        else:
            route.fulfill(status=200, content_type="application/json", body='{"status": "ok"}')

    page.route("**/api/**", handle_route)
    yield page
    context.close()


def test_ui_page_title_and_main_structure(page: Page):
    """Vérifie le chargement de la page d'accueil, son titre et le conteneur principal."""
    page.goto(INDEX_URL)
    assert "Nobis.Supervisor" in page.title()

    # Le conteneur principal et le premier onglet actif (Cockpit / Control) doivent être présents
    main = page.locator("main")
    assert main.is_visible()
    tab_control = page.locator("#tab-control")
    assert tab_control.is_visible()


def test_ui_tab_navigation(page: Page):
    """Vérifie le basculement fonctionnel entre les différents onglets de l'application."""
    page.goto(INDEX_URL)

    # Vérification de l'existence des sections d'onglets
    assert page.locator("#tab-control").count() > 0
    assert page.locator("#tab-pellets").count() > 0
    assert page.locator("#tab-chrono").count() > 0
    assert page.locator("#tab-dashboard").count() > 0
    assert page.locator("#tab-installer").count() > 0

    # Clic sur un bouton d'onglet (ex: Pellets / Granulés)
    pellet_btn = page.locator("button[data-tab='tab-pellets']").first
    if pellet_btn.is_visible():
        pellet_btn.click()
        assert page.locator("#tab-pellets").is_visible()


def test_ui_modals_presence(page: Page):
    """Vérifie la présence des fenêtres modales (Silence, Matter, Actions)."""
    page.goto(INDEX_URL)
    assert page.locator("#silence-modal").count() > 0
    assert page.locator("#matter-modal").count() > 0
    assert page.locator("#action-modal").count() > 0


def test_ui_mobile_viewport_and_bottom_nav(browser_instance):
    """Vérifie la disposition responsive sur smartphone (iPhone/Pixel 375x667)."""
    context = browser_instance.new_context(viewport={"width": 375, "height": 667})
    page = context.new_page()
    page.goto(INDEX_URL)

    # La barre de navigation inférieure mobile (fixed bottom) doit être visible
    bottom_nav = page.locator("nav.fixed.bottom-0")
    assert bottom_nav.is_visible()
    context.close()

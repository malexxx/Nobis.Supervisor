"""Tests End-to-End (E2E) de l'interface utilisateur web et mobile (PWA) via Playwright."""
import mimetypes
import os

from playwright.sync_api import Page, sync_playwright
import pytest

from config import STATIC_DIR
from routers.dashboard import render_spa_html

APP_ORIGIN = "http://nobis.local"


def _attach_app_routes(page: Page) -> None:
    """Sert la SPA assemblée, les fichiers /static/* depuis le disque et mocke les endpoints /api/*."""
    assembled_html = render_spa_html()

    def handle_route(route):
        url = route.request.url
        if url.startswith(f"{APP_ORIGIN}/api/"):
            if "/api/latest" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='''{
                        "latest": {
                            "status_code": 5,
                            "status_label": "TRAVAIL (Normal)",
                            "temp_ambient": 20.5,
                            "temp_amb_setpoint": 21.0,
                            "temp_fumes": 165.0,
                            "pellet_hopper_pct": 82.0,
                            "pellet_daily_kg": 4.5
                        },
                        "comm_status": {"connected": true, "comm_mode": "simulation"}
                    }''',
                )
            elif "/api/silence/status" in url:
                route.fulfill(status=200, content_type="application/json", body='{"active": false}')
            elif "/api/dashboard" in url:
                route.fulfill(status=200, content_type="application/json", body='{"series": [], "latest": {"status_code": 5}}')
            elif "/api/events" in url:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json", body='{"status": "ok"}')
            return

        if url.startswith(f"{APP_ORIGIN}/static/"):
            rel_path = url.split("/static/", 1)[1].split("?", 1)[0]
            disk_path = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
            static_root = os.path.realpath(STATIC_DIR)
            if disk_path.startswith(static_root + os.sep) and os.path.isfile(disk_path):
                ctype, _ = mimetypes.guess_type(disk_path)
                with open(disk_path, "rb") as fh:
                    route.fulfill(status=200, content_type=ctype or "application/octet-stream", body=fh.read())
                return
            route.fulfill(status=404, body="Not Found")
            return

        if url == APP_ORIGIN or url == f"{APP_ORIGIN}/":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=assembled_html)
            return

        # Ne pas dépendre de CDN externes hors-ligne dans les tests E2E
        route.abort()

    page.route("**/*", handle_route)


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
    """Crée une nouvelle page / onglet isolé avec intercepteur SPA/Static/API."""
    context = browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    _attach_app_routes(page)
    yield page
    context.close()


def test_ui_page_title_and_main_structure(page: Page):
    """Vérifie le chargement 100 % hors-ligne (0 CDN externe), Tailwind statique, Chart.js local et 0 erreur JS."""
    page_errors = []
    external_urls = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.on("request", lambda req: external_urls.append(req.url) if not req.url.startswith(APP_ORIGIN) and not req.url.startswith("data:") else None)
    page.goto(f"{APP_ORIGIN}/", wait_until="load")
    assert "Nobis.Supervisor" in page.title()

    main = page.locator("main")
    assert main.is_visible()
    tab_control = page.locator("#tab-control")
    assert tab_control.is_visible()

    # Aucune requête vers des CDN tiers (Tailwind / jsDelivr) — PWA 100 % autonome
    assert external_urls == [], f"Requêtes externes détectées : {external_urls}"
    # Chart.js local chargé et styles Tailwind pré-compilés effectivement appliqués
    assert page.evaluate("() => typeof window.Chart === 'function'")
    assert page.evaluate("() => getComputedStyle(document.querySelector('#tab-pellets')).display") == "none"
    assert page.evaluate("() => getComputedStyle(document.querySelector('header')).display") == "flex"
    assert page_errors == [], f"Erreurs JS au chargement modulaire : {page_errors}"


def test_ui_tab_navigation(page: Page):
    """Vérifie le basculement fonctionnel entre les différents onglets de l'application."""
    page.goto(f"{APP_ORIGIN}/", wait_until="domcontentloaded")

    assert page.locator("#tab-control").count() > 0
    assert page.locator("#tab-pellets").count() > 0
    assert page.locator("#tab-chrono").count() > 0
    assert page.locator("#tab-dashboard").count() > 0
    assert page.locator("#tab-installer").count() > 0

    pellet_btn = page.locator("button[data-tab='tab-pellets']").first
    if pellet_btn.is_visible():
        pellet_btn.click()
        assert page.locator("#tab-pellets").is_visible()


def test_ui_modals_presence(page: Page):
    """Vérifie la présence des fenêtres modales (Silence, Matter, Actions)."""
    page.goto(f"{APP_ORIGIN}/", wait_until="domcontentloaded")
    assert page.locator("#silence-modal").count() > 0
    assert page.locator("#matter-modal").count() > 0
    assert page.locator("#action-modal").count() > 0


def test_ui_mobile_viewport_and_bottom_nav(browser_instance):
    """Vérifie la disposition responsive sur smartphone (iPhone/Pixel 375x667)."""
    context = browser_instance.new_context(viewport={"width": 375, "height": 667})
    page = context.new_page()
    _attach_app_routes(page)
    page.goto(f"{APP_ORIGIN}/", wait_until="domcontentloaded")

    bottom_nav = page.locator("nav.fixed.bottom-0")
    assert bottom_nav.is_visible()
    context.close()


def test_ui_measured_temp_formatting_omits_trailing_zero(page: Page):
    """Vérifie que formatMeasuredTemp masque .0 pour les valeurs entières et conserve les décimales réelles."""
    page.goto(f"{APP_ORIGIN}/", wait_until="load")

    # Vérification unitaire de l'utilitaire JS et du rendu Tri-Arc (entiers vs décimales)
    results = page.evaluate("""() => {
        const unit = {
            int22: formatMeasuredTemp(22.0),
            int17: formatMeasuredTemp(17),
            frac205: formatMeasuredTemp(20.5),
            zero: formatMeasuredTemp(0),
            fallback: formatMeasuredTemp(null, 19),
        };
        applyDashboardState({
            latest: {
                status_code: 5,
                status_label: "TRAVAIL (Normal)",
                temp_ambient: 22.0,
                temp_amb_setpoint: 19.0,
                duct_left_temp: 17.0,
                duct_left_setpoint: 20.0,
                duct_right_temp: 18.0,
                duct_right_setpoint: 20.0,
            }
        }, false);
        const ambHtmlInt = document.getElementById("triarc-slide-AMB").textContent;
        const sxHtmlInt = document.getElementById("triarc-slide-SX").textContent;

        applyDashboardState({
            latest: {
                status_code: 5,
                status_label: "TRAVAIL (Normal)",
                temp_ambient: 20.5,
                temp_amb_setpoint: 21.0,
                duct_left_temp: 17.5,
                duct_left_setpoint: 20.0,
                duct_right_temp: 18.0,
                duct_right_setpoint: 20.0,
            }
        }, false);
        const ambHtmlFrac = document.getElementById("triarc-slide-AMB").textContent;
        return { unit, ambHtmlInt, sxHtmlInt, ambHtmlFrac };
    }""")

    assert results["unit"] == {
        "int22": "22",
        "int17": "17",
        "frac205": "20.5",
        "zero": "0",
        "fallback": "19",
    }
    assert "22°" in results["ambHtmlInt"]
    assert "22.0°" not in results["ambHtmlInt"]
    assert "17°" in results["ambHtmlInt"]
    assert "17.0°" not in results["ambHtmlInt"]
    assert "18°" in results["ambHtmlInt"]
    assert "18.0°" not in results["ambHtmlInt"]
    assert "17°" in results["sxHtmlInt"]
    assert "17.0°" not in results["sxHtmlInt"]
    assert "20.5°" in results["ambHtmlFrac"]
    assert "17.5°" in results["ambHtmlFrac"]

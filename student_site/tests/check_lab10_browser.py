"""Isolated Chromium UI tests of actual Jinja-rendered templates + real JavaScript.

Requires existing Playwright and Chromium. No downloads or installation performed.
Run render_lab10_fixture.py first, then:
 python tests/check_lab10_browser.py --fixtures /tmp/lab10-ui --chromium /usr/bin/chromium
This does NOT test FastAPI, authentication, session cookies or SQL operations.
"""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
KEY = "studentflow.lab10.plan.v1"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--screenshots", type=Path, default=ROOT / "docs/screenshots")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    errors = []
    origin = "http://studentflow.test"

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(executable_path=args.chromium, headless=True,
                                               args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1100})

        async def serve(route):
            path = urlparse(route.request.url).path
            pages = {"/lab10": "editor.html", "/lab10/plan": "plan.html", "/lab10/map": "map.html"}
            if path in pages:
                await route.fulfill(path=str(args.fixtures / pages[path]), content_type="text/html; charset=utf-8")
            elif path in ["/static/style.css", "/static/app.js", "/static/lab10.js"]:
                await route.fulfill(path=str(ROOT / path.lstrip("/")), content_type="text/css" if path.endswith("css") else "application/javascript")
            else:
                await route.fulfill(status=404, body="Fixture: not a server endpoint")

        await context.route("**/*", serve)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(origin + "/lab10")
        assert await page.locator("#course-catalog article").count() == 6
        assert await page.locator("#course-id option").count() == 7
        checks.append("DOM: six cards and six select choices built from array.")
        await page.get_by_role("button", name="JavaScript оқиғалары: таңдау", exact=True).click()
        await page.locator("#plan-hours").fill("5")
        await page.locator("#plan-priority").select_option("high")
        assert "5 сағат" in await page.locator("#form-preview").inner_text()
        await page.locator("#save-plan").click()
        stored = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", KEY)
        assert stored == {"version": 1, "items": [{"courseId": "javascript", "hours": 5, "priority": "high"}]}
        assert page.url == origin + "/lab10"
        checks.append("Form: submit prevented navigation; FormData values saved as allowlisted JSON only.")
        await page.screenshot(path=str(args.screenshots / "01_form_saved.png"), full_page=True)
        await page.get_by_role("link", name="Сақталған жоспарды ашу →", exact=True).click()
        assert page.url == origin + "/lab10/plan"
        assert await page.locator("#plan-rows tr").count() == 1
        assert await page.locator("#plan-total").inner_text() == "5"
        assert await page.locator("#plan-high").inner_text() == "1"
        await page.reload()
        assert await page.locator("#plan-total").inner_text() == "5"
        checks.append("Separate page: saved plan displayed and persisted after reload.")
        # Repeated submit updates, rather than duplicates, the course.
        await page.goto(origin + "/lab10")
        await page.locator("#course-id").select_option("javascript")
        await page.locator("#plan-hours").fill("7")
        await page.locator("#save-plan").click()
        await page.locator("#save-plan").click()
        stored = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", KEY)
        assert len(stored["items"]) == 1 and stored["items"][0]["hours"] == 7
        for invalid in ["0", "41", "1.5", ""]:
            await page.locator("#plan-hours").fill(invalid)
            await page.locator("#save-plan").click()
            assert await page.evaluate("key => JSON.parse(localStorage.getItem(key))", KEY) == stored
        checks.append("Repeated submit updates one course; invalid 0/41/fraction/empty hours do not save.")
        await page.locator("#course-id").select_option("storage")
        await page.locator("#plan-hours").fill("3")
        await page.locator("#plan-priority").select_option("high")
        await page.locator("#save-plan").click()
        await page.goto(origin + "/lab10/plan")
        assert await page.locator("#plan-count").inner_text() == "2"
        assert await page.locator("#plan-total").inner_text() == "10"
        await page.screenshot(path=str(args.screenshots / "02_other_page_plan.png"), full_page=True)
        await page.goto(origin + "/lab10/map")
        assert await page.get_by_role("heading", name="Беттер арасындағы байланыс", exact=True).count() == 1
        await page.screenshot(path=str(args.screenshots / "03_navigation_map.png"), full_page=True)
        await page.go_back()
        assert await page.locator("#plan-count").inner_text() == "2"
        checks.append("Navigation map and browser Back preserve displayed saved plan.")
        second = await context.new_page()
        await second.goto(origin + "/lab10")
        await second.locator("#course-id").select_option("css")
        await second.locator("#plan-hours").fill("2")
        await second.locator("#save-plan").click()
        await page.wait_for_function("document.getElementById('plan-count').textContent === '3'")
        checks.append("A storage event updates the other open tab without refresh.")
        await second.close()
        await page.evaluate("localStorage.setItem('other-app-test', 'keep')")
        page.once("dialog", lambda dialog: dialog.dismiss())
        await page.locator("#clear-plan").click()
        assert await page.locator("#plan-count").inner_text() == "3"
        page.once("dialog", lambda dialog: dialog.accept())
        await page.locator("#clear-plan").click()
        assert await page.locator("#plan-empty").is_visible()
        assert await page.evaluate("localStorage.getItem('other-app-test')") == "keep"
        await page.screenshot(path=str(args.screenshots / "04_empty_state.png"), full_page=True)
        checks.append("Clear cancel preserves plan; confirm removes only the module key and shows empty state.")
        await page.evaluate("key => localStorage.setItem(key, '{broken-json')", KEY)
        await page.reload()
        assert "оқылмады" in await page.locator("#lab-status").inner_text()
        assert await page.locator("#plan-count").inner_text() == "—"
        await page.screenshot(path=str(args.screenshots / "05_invalid_storage.png"), full_page=True)
        await page.goto(origin + "/lab10")
        assert await page.locator("#save-plan").is_disabled()
        assert await page.evaluate("key => localStorage.getItem(key)", KEY) == "{broken-json"
        # Tampered schema / payload must not be rendered or overwritten automatically.
        for payload in [{"version": 1, "items": [{"courseId": "<img src=x onerror=alert(1)>", "hours": 2, "priority": "high"}]},
                        {"version": 1, "items": [{"courseId": "html", "hours": 2, "priority": "high", "password": "not-real"}]},
                        {"version": 1, "items": [{"courseId": "html", "hours": 2, "priority": "high"}] * 2}]:
            await page.evaluate("([key,data]) => localStorage.setItem(key, JSON.stringify(data))", [KEY, payload])
            await page.goto(origin + "/lab10/plan")
            assert await page.locator("#plan-rows tr").count() == 0
            assert "оқылмады" in await page.locator("#lab-status").inner_text()
        checks.append("Malformed JSON, extra fields, unknown course/XSS payload and duplicate IDs are rejected safely.")
        await page.evaluate("key => localStorage.removeItem(key)", KEY)
        await page.goto(origin + "/lab10")
        await page.evaluate("Storage.prototype.setItem = function () { throw new DOMException('quota', 'QuotaExceededError'); }")
        await page.locator("#course-id").select_option("html")
        await page.locator("#save-plan").click()
        assert "сақталмады" in await page.locator("#lab-status").inner_text()
        assert await page.evaluate("key => localStorage.getItem(key)", KEY) is None
        checks.append("Quota/write failure reports failure and never claims the plan was saved.")
        await page.reload()
        await page.set_viewport_size({"width": 390, "height": 844})
        for path in ["/lab10", "/lab10/plan", "/lab10/map"]:
            await page.goto(origin + path)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
        await page.goto(origin + "/lab10")
        await page.get_by_role("button", name="Мәзірді ашу", exact=True).click()
        assert await page.locator(".mobile-menu").get_attribute("aria-expanded") == "true"
        await page.locator(".sidebar a[href='/lab10/plan']").click()
        assert page.url == origin + "/lab10/plan"
        await page.goto(origin + "/lab10")
        await page.screenshot(path=str(args.screenshots / "06_mobile_catalog.png"), full_page=True)
        checks.append("390px viewport: all three pages fit; mobile menu opens and navigates.")
        assert not errors, errors
        checks.append("No uncaught JavaScript errors in tested flows.")
        await context.close()
        # Separate context with storage access denied before scripts execute.
        blocked = await browser.new_context()
        await blocked.route("**/*", serve)
        await blocked.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('blocked', 'SecurityError'); }});")
        denied_page = await blocked.new_page()
        await denied_page.goto(origin + "/lab10")
        assert await denied_page.locator("#save-plan").is_disabled()
        assert "тыйым салды" in await denied_page.locator("#lab-status").inner_text()
        await denied_page.goto(origin + "/lab10/plan")
        assert await denied_page.locator("#clear-plan").is_disabled()
        checks.append("Storage access denial is visible on both pages and disables unsafe writes.")
        await blocked.close()
        await browser.close()
    result = {"scope": "Isolated Chromium UI fixtures. Real templates and JS; no FastAPI/authentication tested.", "checks": checks, "javascript_errors": errors}
    (args.screenshots.parent / "browser_checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

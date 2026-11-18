"""Browser-level smoke test of the real FastAPI app (requires Playwright + Chromium).

Install with: python -m pip install -r requirements-dev.txt
              python -m playwright install chromium
Run with:     python tests/e2e_studentflow.py
The test copies the seed DB to a temporary directory and never edits ак.db.
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "E2ePassword123!"


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def prepare_database(path: Path) -> None:
    shutil.copy2(ROOT / "ак.db", path)
    encoded = PasswordHasher().hash(TEST_PASSWORD)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE users SET password_hash = ?", (encoded,))


def wait_for_server(url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited early. See {log_path}")
        try:
            with urlopen(url, timeout=1):
                return
        except (URLError, TimeoutError):
            time.sleep(0.25)
    raise TimeoutError(f"StudentFlow did not start at {url}. See {log_path}")


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Playwright is missing. Install requirements-dev.txt and Chromium.") from exc

    with tempfile.TemporaryDirectory(prefix="studentflow-e2e-") as temp:
        temp_dir = Path(temp)
        test_db = temp_dir / "studentflow.db"
        log_path = temp_dir / "uvicorn.log"
        prepare_database(test_db)
        port = available_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "STUDENTFLOW_DB": str(test_db),
            "SESSION_SECRET": "e2e-only-secret-key-not-for-production-0123456789",
            "COOKIE_SECURE": "0",
            "COOKIE_SAMESITE": "lax",
            "APP_ENV": "test",
        })
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_server(base_url + "/login", process, log_path)
                with sync_playwright() as playwright:
                    launch_options = {"headless": True}
                    if executable := os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
                        launch_options["executable_path"] = executable
                        launch_options["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
                    browser = playwright.chromium.launch(**launch_options)
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    page_errors: list[str] = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))

                    page.goto(base_url + "/register")
                    page.get_by_label("Аты-жөні").fill("E2E Студент")
                    page.get_by_label("Логин").fill("e2e_student")
                    page.get_by_label("Тобы").fill("QA-101")
                    page.get_by_label("Құпиясөз", exact=True).fill(TEST_PASSWORD)
                    page.get_by_label("Құпиясөзді қайталаңыз").fill(TEST_PASSWORD)
                    page.get_by_role("button", name="Тіркелу").click()
                    page.wait_for_url("**/student")
                    assert "E2E Студент" in page.locator("main").inner_text()
                    assert "QA-101" in page.locator("main").inner_text()
                    assert page.locator('a[href="/admin"]').count() == 0
                    print("PASS: registration, auto-login, profile group and role-hidden admin navigation")

                    page.goto(base_url + "/admin")
                    page.wait_for_url("**/student")
                    print("PASS: student direct access to /admin returns to own dashboard")

                    page.goto(base_url + "/account")
                    page.get_by_label("Қазіргі құпиясөз").fill(TEST_PASSWORD)
                    page.locator('input[name="new_password"]').fill("NewE2ePassword123!")
                    page.locator('input[name="password_confirm"]').fill("NewE2ePassword123!")
                    page.get_by_role("button", name="Құпиясөзді жаңарту").click()
                    page.wait_for_url("**/login")
                    print("PASS: password change logs out and redirects to login")

                    page.get_by_label("Логин").fill("e2e_student")
                    page.get_by_label("Құпиясөз").fill("NewE2ePassword123!")
                    page.get_by_role("button", name="Жүйеге кіру").click()
                    page.wait_for_url("**/student")
                    page.locator('form[action="/logout"] button').click()
                    page.wait_for_url("**/login")

                    page.get_by_label("Логин").fill("teacher1")
                    page.get_by_label("Құпиясөз").fill(TEST_PASSWORD)
                    page.get_by_role("button", name="Жүйеге кіру").click()
                    page.wait_for_url("**/teacher")
                    page.locator('.filter-bar input[name="q"]').fill("Ермеков")
                    page.locator('.filter-bar select[name="subject_id"]').select_option("1")
                    page.get_by_role("button", name="Сүзу", exact=True).click()
                    page.wait_for_url("**/teacher?**")
                    assert page.locator(".score-badge").all_text_contents() == ["92"]
                    page.goto(base_url + "/admin")
                    page.wait_for_url("**/teacher")
                    print("PASS: teacher dashboard, journal filter and admin authorization")
                    page.locator('form[action="/logout"] button').click()
                    page.wait_for_url("**/login")

                    page.get_by_label("Логин").fill("admin")
                    page.get_by_label("Құпиясөз").fill(TEST_PASSWORD)
                    page.get_by_role("button", name="Жүйеге кіру").click()
                    page.wait_for_url("**/admin")
                    page.goto(base_url + "/admin?q=student1")
                    assert "@student1" in page.locator("main").inner_text()
                    assert "@teacher1" not in page.locator("main").inner_text()
                    assert not page_errors, f"Browser errors: {page_errors}"
                    print("PASS: admin account and user search; no uncaught browser errors")

                    mobile = browser.new_page(viewport={"width": 390, "height": 844})
                    mobile.on("pageerror", lambda error: page_errors.append(str(error)))
                    mobile.goto(base_url + "/login")
                    mobile.get_by_label("Логин").fill("e2e_student")
                    mobile.get_by_label("Құпиясөз").fill("NewE2ePassword123!")
                    mobile.get_by_role("button", name="Жүйеге кіру").click()
                    mobile.wait_for_url("**/student")
                    assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    mobile.locator(".mobile-menu").click()
                    assert "sidebar-open" in mobile.locator(".sidebar").get_attribute("class")
                    mobile.get_by_role("link", name="Аккаунт және пароль").click()
                    mobile.wait_for_url("**/account")
                    assert not page_errors, f"Browser errors: {page_errors}"
                    print("PASS: mobile layout, menu toggle and account navigation")
                    browser.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == "__main__":
    main()

"""Render real templates for isolated browser testing without starting FastAPI.

Run with a Python interpreter that already has Jinja2 installed:
    python tests/render_lab10_fixture.py OUTPUT_DIRECTORY
These fixtures do not test authentication, FastAPI or SQLite.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html", "xml"]))
    for template in env.list_templates():
        env.get_template(template)  # Syntax-check every existing and new template.
    for name, route in [("editor", "/lab10"), ("plan", "/lab10/plan"), ("map", "/lab10/map")]:
        html = env.get_template(f"lab10_{name}.html").render(
            request=SimpleNamespace(url=SimpleNamespace(path=route)),
            user={"full_name": "Оқу үлгісі", "role": "student"},
            roles={"student": "Студент"}, csrf_token="fixture-not-a-server-token",
            flash=None, url_for=lambda name, path: f"/static/{path}",
        )
        (output / f"{name}.html").write_text(html, encoding="utf-8")
    print("All templates parsed; 3 isolated UI fixtures rendered.")


if __name__ == "__main__":
    main()

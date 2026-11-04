"""Build an allowlisted static Pages site from the project's canonical documents."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from string import Template
from zipfile import ZIP_DEFLATED, ZipFile

import markdown

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://quantifiedcognition.jollyjoyjourney.com"
DOCUMENTS = {
    "privacy": ("docs/PRIVACY.md", "Privacy policy"),
    "research": ("docs/RESEARCH_INFORMATION.md", "Research information"),
    "methods": ("site/methods.md", "Methods"),
}


def render_markdown(source: str) -> str:
    for route, (path, _) in DOCUMENTS.items():
        source = source.replace(f"]({Path(path).name})", f"](/{route}/)")
    return markdown.markdown(source, extensions=["tables", "fenced_code", "sane_lists"])


def build(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    frame = Template((ROOT / "site/frame.html").read_text("utf-8"))
    files = {}

    def write(name, value):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = value.encode("utf-8") if isinstance(value, str) else value
        path.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()

    def page(route, title, description, body):
        write(
            (route + "/" if route else "") + "index.html",
            frame.substitute(
                title=html.escape(title + " · Quantified Cognition" if route else title),
                description=html.escape(description, quote=True),
                canonical=BASE_URL + ("/" + route if route else "") + "/",
                body=body,
            ),
        )

    page(
        "",
        "Quantified Cognition — Discover how your ability to learn varies",
        "Daily memory formation and recall estimates from your existing Anki history. "
        "Explore changes in learning, personal experiments, sleep and activity.",
        (ROOT / "site/home.html").read_text("utf-8"),
    )
    for route, (relative_path, title) in DOCUMENTS.items():
        source_path = ROOT / relative_path
        filename = source_path.name
        source = source_path.read_text("utf-8")
        links = "".join(
            f'<a href="/{item}/"'
            + (' aria-current="page"' if route == item else "")
            + f">{label}</a>"
            for item, label in (
                ("research", "Research information"),
                ("methods", "Methods"),
                ("privacy", "Privacy policy"),
                ("contact", "Contact"),
            )
        )
        body = (
            '<div class="wrap doc-layout"><aside class="doc-aside"><p>ABOUT THE PROJECT</p>'
            + links
            + '</aside><article class="document">'
            + render_markdown(source)
            + f'<p class="doc-note"><a href="/documents/{filename}">Download this document</a>'
            + "</p></article></div>"
        )
        page(
            route,
            title,
            f"Quantified Cognition {title.lower()}, operated by JollyJoyJourney.",
            body,
        )
        write("documents/" + filename, source_path.read_bytes())
    page(
        "contact",
        "Contact",
        "Contact JollyJoyJourney, the operator of Quantified Cognition.",
        (ROOT / "site/contact.html").read_text("utf-8"),
    )
    source = (ROOT / "site/website-privacy.md").read_text("utf-8")
    page(
        "website-privacy",
        "Website privacy",
        "Privacy information for this Cloudflare Pages website.",
        '<article class="plain-page wrap document">' + render_markdown(source) + "</article>",
    )
    write("style.css", (ROOT / "site/style.css").read_bytes())
    write(
        "icon.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
        '<rect width="40" height="40" rx="9" fill="#213d35"/>'
        '<path d="M10 27V21M17 27V15M24 27V10M31 27V18" fill="none" '
        'stroke="#d1e6a7" stroke-width="2.5" stroke-linecap="round"/></svg>',
    )
    write("license.txt", (ROOT / "LICENSE").read_bytes())
    write(
        "_redirects",
        "/operations /research/ 301\n"
        "/operations/ /research/ 301\n"
        "/operations/index.html /research/ 301\n"
        "/research-controls /privacy/ 301\n"
        "/research-controls/ /privacy/ 301\n"
        "/research-controls/index.html /privacy/ 301\n"
        "/documents/RESEARCH_OPERATIONS.md /research/ 301\n"
        "/documents/RESEARCH_CONTROLS.md /privacy/ 301\n"
        "/documents/RESEARCH_DESIGN.md /methods/ 301\n",
    )
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    routes = ["", *DOCUMENTS, "contact", "website-privacy"]
    write(
        "sitemap.xml",
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(
            f"<url><loc>{BASE_URL}/{route + '/' if route else ''}</loc></url>" for route in routes
        )
        + "</urlset>",
    )
    write(
        "404.html",
        frame.substitute(
            title="Page not found · Quantified Cognition",
            description="This page could not be found.",
            canonical=BASE_URL + "/",
            body='<article class="plain-page wrap"><h1>Page not found</h1>'
            '<p><a href="/">Return to Quantified Cognition</a>.</p></article>',
        ),
    )
    write(
        "_headers",
        "/*\n  Cache-Control: public, max-age=0, must-revalidate, no-transform\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Content-Security-Policy: default-src 'none'; style-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'\n"
        "  Permissions-Policy: camera=(), microphone=(), geolocation=()\n",
    )
    # Refuse unexpected leftovers instead of publishing anything outside this build.
    existing = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if existing != set(files):
        raise RuntimeError(
            "Unexpected files in website output; choose a new empty output directory"
        )
    report = {"base_url": BASE_URL, "files": files}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/website")
    parser.add_argument("--zip", type=Path, help="Optional Cloudflare Direct Upload archive")
    arguments = parser.parse_args()
    result = build(arguments.output)
    if arguments.zip:
        arguments.zip.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(arguments.zip, "w", ZIP_DEFLATED) as archive:
            for name in sorted(result["files"]):
                archive.write(arguments.output / name, name)
    print(
        json.dumps(
            {
                "base_url": result["base_url"],
                "files": len(result["files"]),
                "output": str(arguments.output),
                "zip": str(arguments.zip),
            }
        )
    )

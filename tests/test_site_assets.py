"""
Tests that every image, video, and asset referenced in the site HTML files
actually exists on disk — so broken-image regressions are caught before
deployment to GitHub Pages.

The test resolves paths relative to the docs/ directory (where index.html
and plugins.html live) and also checks against the _site/ build layout
that the gh-pages workflow produces.

Run: /usr/bin/python3 -m pytest tests/test_site_assets.py -v
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCS_DIR  = REPO_ROOT / "docs"

# HTML pages to audit
SITE_PAGES = [
    DOCS_DIR / "index.html",
    DOCS_DIR / "plugins.html",
]

# Patterns to extract from HTML (src=, href= for local files)
_SRC_RE    = re.compile(r'src=["\']([^"\']+)["\']')
_HREF_RE   = re.compile(r'href=["\']([^"\'#][^"\']*)["\']')
_CSS_URL_RE = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)')


def _local_refs(html: str) -> list[str]:
    """Extract all local asset references from an HTML string."""
    refs = []
    for pattern in (_SRC_RE, _HREF_RE, _CSS_URL_RE):
        for m in pattern.finditer(html):
            ref = m.group(1).strip()
            # Skip: absolute URLs, data URIs, anchors, mailto
            if ref.startswith(("http://", "https://", "data:", "#", "mailto:")):
                continue
            refs.append(ref)
    return refs


def _collect_missing(page: Path) -> list[tuple[str, str]]:
    """
    Return a list of (page_name, missing_ref) for every referenced
    local asset that does not exist relative to docs/.
    """
    if not page.exists():
        return [(str(page), "PAGE FILE MISSING")]
    html = page.read_text(encoding="utf-8", errors="replace")
    missing = []
    for ref in _local_refs(html):
        # Resolve relative to the page's directory (all pages are in docs/)
        candidate = (DOCS_DIR / ref).resolve()
        if not candidate.exists():
            missing.append((page.name, ref))
    return missing


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_index_html_exists():
    """index.html must exist in docs/."""
    assert (DOCS_DIR / "index.html").exists(), "docs/index.html missing"


def test_plugins_html_exists():
    """plugins.html must exist in docs/."""
    assert (DOCS_DIR / "plugins.html").exists(), "docs/plugins.html missing"


def test_no_broken_assets_index():
    """Every local asset referenced in index.html must exist on disk."""
    missing = _collect_missing(DOCS_DIR / "index.html")
    if missing:
        report = "\n".join(f"  {page}: {ref}" for page, ref in missing)
        raise AssertionError(
            f"{len(missing)} broken asset(s) in index.html:\n{report}"
        )


def test_no_broken_assets_plugins():
    """Every local asset referenced in plugins.html must exist on disk."""
    missing = _collect_missing(DOCS_DIR / "plugins.html")
    if missing:
        report = "\n".join(f"  {page}: {ref}" for page, ref in missing)
        raise AssertionError(
            f"{len(missing)} broken asset(s) in plugins.html:\n{report}"
        )


def test_gh_pages_workflow_copies_all_referenced_assets():
    """
    Simulate the gh-pages workflow's _site assembly and verify every
    asset referenced in the HTML pages would exist in _site/.

    The workflow does:
      cp docs/index.html  _site/
      cp docs/plugins.html _site/
      cp -r assets        _site/assets      (repo-root assets)
      cp app/assets/tenstorrent.png _site/assets/tenstorrent.png
      cp -r docs/assets/. _site/assets      (docs assets, merged)

    So _site/assets/ = union of root assets/ + docs/assets/.
    """
    # Build the set of files that would be in _site/assets/
    available: set[str] = set()

    root_assets = REPO_ROOT / "assets"
    if root_assets.exists():
        for f in root_assets.rglob("*"):
            if f.is_file():
                rel = f.relative_to(root_assets)
                available.add(f"assets/{rel}")

    docs_assets = DOCS_DIR / "assets"
    if docs_assets.exists():
        for f in docs_assets.rglob("*"):
            if f.is_file():
                rel = f.relative_to(docs_assets)
                available.add(f"assets/{rel}")

    # tenstorrent.png from app/assets
    app_tt_png = REPO_ROOT / "app" / "assets" / "tenstorrent.png"
    if app_tt_png.exists():
        available.add("assets/tenstorrent.png")

    missing = []
    for page in SITE_PAGES:
        if not page.exists():
            continue
        html = page.read_text(encoding="utf-8", errors="replace")
        for ref in _local_refs(html):
            # Only check asset references (not inter-page links)
            if ref.startswith("assets/"):
                if ref not in available:
                    missing.append((page.name, ref))
            elif ref.endswith((".html", ".md")):
                # Inter-page link — check it would be in _site/
                target = DOCS_DIR / ref
                if not target.exists():
                    missing.append((page.name, ref))

    if missing:
        report = "\n".join(f"  {page}: {ref}" for page, ref in missing)
        raise AssertionError(
            f"{len(missing)} asset(s) would be missing from _site/:\n{report}"
        )


def test_inter_page_links_resolve():
    """Internal HTML links between site pages must point to existing files."""
    broken = []
    for page in SITE_PAGES:
        if not page.exists():
            continue
        html = page.read_text(encoding="utf-8", errors="replace")
        for ref in _href_RE_local(html):
            target = (DOCS_DIR / ref).resolve()
            if not target.exists():
                broken.append((page.name, ref))
    if broken:
        report = "\n".join(f"  {page}: {ref}" for page, ref in broken)
        raise AssertionError(f"{len(broken)} broken inter-page link(s):\n{report}")


def _href_RE_local(html: str) -> list[str]:
    """Return href values that are local .html/.md files (not anchors or external)."""
    refs = []
    for m in _HREF_RE.finditer(html):
        ref = m.group(1).strip()
        if ref.startswith(("http", "#", "data:", "mailto:")):
            continue
        if ref.endswith((".html", ".md")):
            refs.append(ref)
    return refs

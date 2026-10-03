import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
README_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
REPOSITORY_PATH_PREFIX = "/hasanzaibak/jzpack/blob/main/"
DOCUMENTATION_FILES = {"BENCHMARKS.md", "docs/WRITER.md", "FORMAT.md", "ROADMAP.md"}


def test_readme_repository_links_are_absolute_and_target_existing_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = README_LINK.findall(readme)

    assert links, "README should expose links to repository documentation"
    repository_targets = set()
    for link in links:
        if link.startswith("#"):
            continue
        parsed = urlsplit(link)
        assert parsed.scheme in {"http", "https"} and parsed.netloc, (
            f"README repository link must be absolute: {link}"
        )
        if parsed.netloc != "github.com" or not parsed.path.startswith("/hasanzaibak/jzpack/"):
            continue
        assert parsed.scheme == "https" and parsed.path.startswith(REPOSITORY_PATH_PREFIX), (
            f"README repository file link must use the canonical GitHub URL: {link}"
        )

        target = parsed.path.removeprefix(REPOSITORY_PATH_PREFIX)
        assert (ROOT / target).is_file(), f"README link target does not exist in the repository: {link}"
        repository_targets.add(target)

    assert DOCUMENTATION_FILES <= repository_targets

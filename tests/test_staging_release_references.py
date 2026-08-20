"""Keep staging installers, updater, and user-facing links on staging."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = "RT-Internal-DS/Agent8088-Features-added"
OLD_REPO = "tayyabimam1/Agent8088-Features-added"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_windows_installer_defaults_to_staging_repository():
    source = _read("install.ps1")
    assert 'else { "staging" }' in source
    assert f'$RepoSlug = "{REPO}"' in source
    assert OLD_REPO not in source


def test_unix_installer_defaults_to_staging_repository():
    source = _read("install.sh")
    assert f'REPO_URL="https://github.com/{REPO}.git"' in source
    assert 'REPO_BRANCH="${AGENT8088_BRANCH:-staging}"' in source
    assert f"https://raw.githubusercontent.com/{REPO}/$BRANCH/install.sh" in source
    assert OLD_REPO not in source


def test_installed_cli_updates_from_staging():
    assert 'UPDATE_BRANCH = "staging"' in _read("src/agent8088/cli.py")


def test_readme_installs_and_badges_staging():
    readme = _read("README.md")
    quick_start = readme.split("## Quick start", 1)[1].split("## How Agent8088", 1)[0]
    assert "Install the staging branch" in quick_start
    assert f"{REPO}/staging/install.sh" in quick_start
    assert f"{REPO}/staging/install.ps1" in quick_start
    assert "AGENT8088_BRANCH=staging" in quick_start
    assert 'AGENT8088_BRANCH = "staging"' in quick_start
    assert f"{REPO}/tree/staging" in readme


def test_published_references_do_not_use_the_old_repository():
    paths = (
        "README.md",
        "install.ps1",
        "install.sh",
        "docs/wiki/01-getting-started.md",
        "docs/wiki/14-contributing.md",
        "docs/wiki/README.md",
        "scripts/sync_wiki.py",
    )
    for path in paths:
        assert OLD_REPO not in _read(path), path

"""One version, declared in five places, asserted to be one version.

The CHANGELOG says the PyPI package, the npm packages and the widget bundle are
versioned together and released from one tag. That was true as an intention and
false as a fact: every `package.json` and `pyproject.toml` in this repository
declared `0.1.0` through v1.0 and v2.0, while the changelog described two major
releases. Nothing noticed, because nothing looked.

A released artefact whose version disagrees with its changelog is worse than an
unversioned one. A customer reporting a bug against "2.0.0" would have been
running a wheel that called itself 0.1.0, and the first question of any support
conversation — *which version are you on* — had no answer that matched anything.

So this is a gate rather than a convention. It runs in the `quality` job with
everything else that must never go yellow.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGES = ("loader", "widget", "dashboard")


def declared_python_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert match, "pyproject.toml declares no version"
    return match.group(1)


def declared_npm_version(package: str) -> str:
    return json.loads((ROOT / package / "package.json").read_text())["version"]


def latest_changelog_version() -> str:
    for line in (ROOT / "CHANGELOG.md").read_text().splitlines():
        if match := re.match(r"^## \[(\d+\.\d+\.\d+)\]", line):
            return match.group(1)
    raise AssertionError("CHANGELOG.md has no released version heading")


@pytest.mark.parametrize("package", PACKAGES)
def test_every_npm_package_matches_the_python_package(package):
    assert declared_npm_version(package) == declared_python_version(), (
        f"{package}/package.json disagrees with pyproject.toml. These ship from one tag, "
        "so they are one version."
    )


def test_the_declared_version_matches_the_changelog():
    """The top released heading in the changelog is what the packages claim to be.

    An `[Unreleased]` section is fine and does not count — this compares against
    the newest heading that names a version.
    """
    assert declared_python_version() == latest_changelog_version(), (
        "The packages and CHANGELOG.md name different versions. Whichever is wrong, a "
        "customer asking 'which version am I on' needs one answer."
    )


def test_the_version_is_not_the_scaffold_default():
    """`0.1.0` is what every package said while two major releases were described.

    Pinned explicitly so a future reset to the scaffold value fails loudly rather
    than passing every other check in this file.
    """
    assert declared_python_version() != "0.1.0"

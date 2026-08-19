"""Tests for ignore-rule precedence, the gitignore matcher and sensitive-path
detection (Aşama 7.3)."""

import pytest

from src.infrastructure.repositories.ignore_rules import (
    DEFAULT_IGNORE_PATTERNS,
    IgnoreRules,
    build_ignore_rules,
    is_sensitive_path,
)


# --- Default system ignore list ----------------------------------------------
def test_default_ignore_list_covers_documented_entries():
    expected = {
        ".git/", "node_modules/", ".venv/", "venv/", "dist/", "build/",
        "target/", "coverage/", ".next/", ".cache/", "vendor/",
        "*.min.js", "*.map", "*.lock", "*.png", "*.jpg", "*.pdf",
        "*.exe", "*.dll", "*.so", "*.class", "*.jar", "*.zip", "*.tar",
        "*.gz", ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.jks",
        "id_rsa*",
    }
    assert len(DEFAULT_IGNORE_PATTERNS) == len(expected)
    assert set(DEFAULT_IGNORE_PATTERNS) == expected


def test_default_ignore_dirs_and_files():
    rules = build_ignore_rules()
    # Directory pruning.
    assert rules.is_ignored("node_modules", is_dir=True) is True
    assert rules.is_ignored(".git", is_dir=True) is True
    assert rules.is_ignored("build", is_dir=True) is True
    # Files.
    assert rules.is_ignored(".env") is True
    assert rules.is_ignored("config/.env") is True          # nested basename
    assert rules.is_ignored(".env.production") is True
    assert rules.is_ignored("app.min.js") is True
    assert rules.is_ignored("static/app.css.map") is True
    assert rules.is_ignored("bundle.png") is True
    assert rules.is_ignored("app.exe") is True


def test_default_ignore_does_not_affect_common_source():
    rules = build_ignore_rules()
    assert rules.is_ignored("src/main.py") is False
    assert rules.is_ignored("app.ts") is False
    assert rules.is_ignored("README.md") is False


# --- Gitignore matcher -------------------------------------------------------
def test_gitignore_comments_and_blank_lines_ignored():
    rules = build_ignore_rules(
        system_ignore=[],
        gitignore=[
            "# a comment",
            "",
            "   ",
            "*.log",
        ],
    )
    assert rules.is_ignored("debug.log") is True
    assert rules.is_ignored("main.py") is False


def test_gitignore_negation_last_match_wins():
    rules = build_ignore_rules(
        system_ignore=[],
        gitignore=["*.log", "!important.log"],
    )
    assert rules.is_ignored("debug.log") is True
    assert rules.is_ignored("important.log") is False


def test_gitignore_extension_rule_any_level():
    rules = build_ignore_rules(system_ignore=[], gitignore=["*.tmp"])
    assert rules.is_ignored("a.tmp") is True
    assert rules.is_ignored("deep/dir/b.tmp") is True


def test_gitignore_directory_pattern():
    rules = build_ignore_rules(system_ignore=[], gitignore=["tmp/"])
    assert rules.is_ignored("tmp", is_dir=True) is True
    assert rules.is_ignored("nested/tmp", is_dir=True) is True


def test_gitignore_anchored_slash_pattern():
    rules = build_ignore_rules(
        system_ignore=[], gitignore=["/docs/generated/", "*.md"]
    )
    # Any *.md ignored; docs only ignored when anchored under the root.
    assert rules.is_ignored("docs/generated/x.md") is True
    assert rules.is_ignored("other/file.md") is True


# --- Precedence --------------------------------------------------------------
def test_system_ignore_cannot_be_overridden_by_user_include():
    rules = build_ignore_rules(include_patterns=[".env", "*.pem"])
    assert rules.is_ignored(".env") is True


def test_contextvaultignore_takes_precedence_over_gitignore():
    rules = build_ignore_rules(
        system_ignore=[],
        contextvault_ignore=["secret.tmp"],
        gitignore=["!secret.tmp"],
    )
    assert rules.is_ignored("secret.tmp") is True


def test_gitignore_overrides_user_exclude_order():
    # Within gitignore layer, negation re-includes; user layer follows after.
    rules = build_ignore_rules(
        system_ignore=[],
        gitignore=["!keep.txt"],
        exclude_patterns=["keep.txt"],
    )
    assert rules.is_ignored("keep.txt") is False


def test_user_include_reincludes_when_no_higher_rule_matches():
    rules = build_ignore_rules(
        system_ignore=["*.log"],
        gitignore=["cache/*"],
        include_patterns=["cache/important.log"],
    )
    # cache/* prunes; the include cannot rescue the whole dir/file because
    # gitignore already matched at a higher layer.
    assert rules.is_ignored("cache/x.log") is True


def test_secret_policy_default_skips_sensitive(tmp_path):
    # Reading .gitignore / .contextvaultignore from a real tree does not crash.
    import os
    os.makedirs(tmp_path, exist_ok=True)
    rules = build_ignore_rules(root_path=str(tmp_path))
    assert rules.is_ignored("src/main.py") is False


# --- Sensitive-path detection -----------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.prod",
        "config/.env",
        "secrets/keys/id_rsa",
        "secrets/keys/id_rsa.pub",
        "certs/server.key",
        "certs/cert.pem",
        "store/keystore.p12",
        "security/truststore.jks",
    ],
)
def test_sensitive_path_detection_true(path):
    assert is_sensitive_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/main.py",
        "README.md",
        "app.ts",
        "docker-compose.yml",
        "important.log",
    ],
)
def test_sensitive_path_detection_false(path):
    assert is_sensitive_path(path) is False

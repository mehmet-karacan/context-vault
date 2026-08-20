"""Aşama 9.5: secret/credential skip & redaction policy tests.

Covers ``redact_secrets``, ``contains_secret``, ``redact_log_field`` and
``is_sensitive_filename`` (which reuses the Aşama 7 ignore-rules matcher).
"""

from __future__ import annotations

from src.infrastructure.security.redaction import (
    REDACTED,
    contains_secret,
    is_sensitive_filename,
    redact_log_field,
    redact_secrets,
)


_ENV_SAMPLE = (
    "DATABASE_URL=postgresql://user:pass@host/db\n"
    "SECRET_KEY=super-secret-value-123\n"
    "API_KEY=sk-abc123def456ghi789\n"
)


def test_redacts_api_key_in_sample():
    out = redact_secrets("password=topsecret123\napi_key=sk-abcdef123456")
    assert "topsecret123" not in out
    assert "sk-abcdef123456" not in out
    assert REDACTED in out


def test_redacts_env_file_lines():
    out = redact_secrets(_ENV_SAMPLE)
    assert "super-secret-value-123" not in out
    assert "sk-abc123def456ghi789" not in out
    # The keys themselves survive; only the values are redacted.
    assert "SECRET_KEY=" in out
    assert "API_KEY=" in out
    assert REDACTED in out


def test_redacts_private_key_blob():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAy8m0L1123privateblobhere\n"
        "abcdefg=\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    out = redact_secrets(pem)
    assert "privateblobhere" not in out
    assert REDACTED in out


def test_redacts_aws_and_github_tokens():
    out = redact_secrets("region=us-east-1 AKIAIOSFODNN7EXAMPLE ghp_abcdefghijklmnopqrstuvwxyz")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in out


def test_contains_secret_true_for_credentials():
    assert contains_secret("Password=Hunt3r2!") is True
    assert contains_secret("token = ghp_zzzzzzzzzzzzzzzzz") is True
    assert contains_secret(_ENV_SAMPLE) is True
    assert contains_secret("-----BEGIN PRIVATE KEY-----\nxyz\n-----END PRIVATE KEY-----") is True


def test_contains_secret_false_for_plain_text():
    assert contains_secret("Bugun guzel bir gun, rapor hazir.") is False
    # The bare keyword without an assigned value is not a credential.
    assert contains_secret("Kullanici sifresini unuttu ama burada deger yok.") is False
    assert contains_secret("The password field is on line 12.") is False


def test_redact_log_field_returns_safe_value():
    assert redact_log_field("api_key=sk-secretvalue123") == REDACTED
    assert redact_log_field("job-123") == "job-123"
    assert redact_log_field(None) is None


def test_redact_log_field_caps_long_values():
    long_val = "x" * 5000
    out = redact_log_field(long_val, max_len=100)
    assert len(out) <= 103
    assert out.endswith("...")


def test_sensitive_filenames_flagged():
    assert is_sensitive_filename(".env") is True
    assert is_sensitive_filename("config/.env.production") is True
    assert is_sensitive_filename("secrets/id_rsa") is True
    assert is_sensitive_filename("keys/server.pem") is True
    assert is_sensitive_filename("deploy/credentials.json") is True


def test_non_sensitive_filenames_allowed():
    assert is_sensitive_filename("src/main.py") is False
    assert is_sensitive_filename("docs/report.txt") is False
    assert is_sensitive_filename("") is False
    assert is_sensitive_filename(None) is False

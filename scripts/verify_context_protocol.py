#!/usr/bin/env python3
"""Run the context protocol verifier through the shared contract."""

from verifier_core import run_named_cli


if __name__ == "__main__":
    raise SystemExit(run_named_cli("verify_context_protocol"))

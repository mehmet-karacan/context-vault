# CV3 Baseline CI Summary

GitHub was queried read-only at the baseline. Four quality workflows are
manual-only (`workflow_dispatch`); only commit ownership still runs on push and
pull requests.

| Workflow | Run | SHA | Result | First terminal step |
|---|---:|---|---|---|
| Backend | `33400923781` | `05ebb46a048c963cc978a66d98269d8cd09df67b` | FAIL | Ruff format check |
| RAG eval | `33400923547` | `05ebb46a048c963cc978a66d98269d8cd09df67b` | FAIL | Migration upgrade |
| Security | `33400923851` | `05ebb46a048c963cc978a66d98269d8cd09df67b` | FAIL | Secret scan |
| Frontend | `33400923778` | `05ebb46a048c963cc978a66d98269d8cd09df67b` | PASS | All declared steps passed |
| Commit ownership | `33401641746` | `6b99a53d19e7f5f7b07b403e751c629f79ab7663` | PASS | Completed |

The backend and RAG failures were reproduced locally in a new temporary Python
3.12 environment installed from the committed requirement files:

- `ruff format --check .`: exit `1`; `97` files would be reformatted and `43`
  were already formatted.
- `alembic upgrade head` with no LLM key: exit `1`; settings validation required
  `LITELLM_API_KEY` before a database connection could be attempted.
- Private Ruff log SHA-256:
  `35270db698651a51dee04ae4ae5bfc0eca701ea42d008e79784bc1e38e3d963e`
- Private Alembic log SHA-256:
  `7e15e56540363bb53c951f08471e559a64f342c0b6b7e3bb863be46400cc2389`

Raw logs are stored outside the public repository with owner-only permissions.
The public artifact records only opaque evidence references and hashes.

No CI failure was skipped, disabled, reformatted, or otherwise repaired during
this package.


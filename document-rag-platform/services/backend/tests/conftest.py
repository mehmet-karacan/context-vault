"""Shared pytest fixtures / environment setup for the backend test suite.

``src.config.Settings`` requires ``DATABASE_URL`` and ``LITELLM_API_KEY`` at
import time (Aşama 1 acceptance criterion: missing required credentials must
fail loudly). Importing ``src.main`` (and everything it pulls in — ``src.db``,
``src.llm``, the infrastructure adapters) therefore requires *some* value for
both, even though no test in this suite makes a real database connection or a
real LLM-gateway call:

- DB-touching endpoints (``/health``) override the ``get_db`` FastAPI
  dependency with an in-memory fake session — see ``tests/test_health.py``.
- The FastAPI ``startup`` event (``init_db()``, which *would* try to reach a
  real Postgres) is monkeypatched out per-test before the app's lifespan
  runs — see ``tests/test_main_app.py``.
- No test calls ``embed_text`` / ``embed_texts`` / ``generate_answer``, so the
  dummy ``LITELLM_API_KEY`` is never used to make an outbound request; the
  OpenAI SDK client only needs *a* string to construct, not a valid key.

These are only defaults (``setdefault``): if the suite is run inside an
environment that already has real values configured (e.g. the compose
``backend`` container), those real values are used unchanged and nothing here
overrides them.
"""

import os
import sys
from pathlib import Path

# Make `import src...` work regardless of the directory pytest is invoked
# from (tests/ sits next to src/ under services/backend/).
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("LITELLM_API_KEY", "test-key-not-used")

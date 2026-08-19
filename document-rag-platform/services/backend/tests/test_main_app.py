"""Aşama 1 kabul kriteri: "main.py uygulama oluşturma ve router bağlama
dışında iş kuralı içermez" / "Unit testler ve backend startup testi geçer".

These tests check ``src.main`` from the outside (app assembly + startup
wiring), and inspect ``main.py``'s source to guard the "no business logic in
main.py" acceptance criterion structurally rather than just by inspection.
"""

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import app


def test_startup_event_calls_init_db(monkeypatch):
    """The FastAPI startup hook must still call `init_db()` — this is the
    "backend startup testi" itself, exercised with a stubbed init_db so no
    real database connection happens in the test suite."""
    calls = []
    monkeypatch.setattr("src.main.init_db", lambda: calls.append(True))

    with TestClient(app) as client:
        # Entering the TestClient context runs the app's lifespan/startup.
        response = client.get("/")
        assert response.status_code == 200

    assert calls == [True], "startup hook did not call init_db()"


def test_all_expected_routers_are_mounted():
    paths = {route.path for route in app.routes}
    expected = {
        "/",
        "/health",
        "/projects",
        "/documents",
        "/documents/upload",
        "/documents/{doc_id}",
        "/documents/{doc_id}/status",
        "/documents/{doc_id}/delete",
        "/chat/query",
        "/chat/models",
    }
    missing = expected - paths
    assert not missing, f"routes missing from the mounted app: {missing}"


def test_cors_middleware_is_configured():
    middleware_classes = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in middleware_classes


def test_main_module_contains_no_business_logic():
    """Structural guard for the Aşama 1 acceptance criterion: main.py may
    define the FastAPI app, add middleware, include the router and register
    the startup hook — nothing else. In particular it must not define its
    own route handlers (`@app.get(...)` etc.) or import DB/model/LLM
    symbols directly.
    """
    main_path = Path(__file__).resolve().parents[1] / "src" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    # No route decorators directly on `app` (e.g. @app.get, @app.post) other
    # than the startup event registration.
    disallowed_decorator_attrs = {
        "get", "post", "put", "delete", "patch", "options", "head",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                # decorator is e.g. `app.get("/")` -> Call(func=Attribute(...))
                func = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(func, ast.Attribute) and func.attr in disallowed_decorator_attrs:
                    raise AssertionError(
                        f"main.py defines a route handler directly "
                        f"({func.attr}) — business logic belongs in api/v1/*"
                    )

    # No direct model/business-logic imports — only app assembly + router
    # include + DB startup hook.
    source = main_path.read_text(encoding="utf-8")
    assert "from .models" not in source
    assert "from .llm" not in source
    assert "sqlalchemy" not in source.lower()

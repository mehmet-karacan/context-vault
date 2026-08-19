"""Aşama 7.5: CodeParser — line/symbol-aware code parsing to NormalizedSource.

Covers: source_type/title/language, file_header + code + symbol units with
correct line ranges and symbol names, PL/SQL symbol detection, ParserRouter
routing of code extensions, and the NormalizedSource to_dict/from_dict round
trip.
"""

from src.domain.normalized_content import NormalizedSource, UnitType
from src.infrastructure.parsers.code_parser import CodeParser
from src.infrastructure.parsers.router import ParserRouter

PY = (
    "def greet(name):\n"
    "    return 'hello'\n"
    "\n"
    "class Greeter:\n"
    "    def hi(self):\n"
    "        pass\n"
)

PLS = (
    "CREATE OR REPLACE PACKAGE emp_pkg IS\n"
    "  PROCEDURE raise_salary (p_empno NUMBER);\n"
    "END emp_pkg;\n"
    "/\n"
    "CREATE OR REPLACE PACKAGE BODY emp_pkg IS\n"
    "  PROCEDURE raise_salary (p_empno NUMBER) IS\n"
    "  BEGIN\n"
    "    NULL;\n"
    "  END raise_salary;\n"
    "END emp_pkg;\n"
    "/\n"
)


def test_python_parses_to_code_normalized_source(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(PY, encoding="utf-8")
    source = CodeParser().parse(str(path), "sample.py")

    assert source.source_type == "code"
    assert source.title == "sample.py"
    assert source.language == "python"
    assert source.metadata["language"] == "python"
    assert source.metadata["file_path"] == str(path)

    types = [u.unit_type for u in source.units]
    assert UnitType.FILE_HEADER in types
    assert UnitType.CODE in types
    assert UnitType.SYMBOL in types

    # first unit is the file header carrying file/language/mime metadata
    header = source.units[0]
    assert header.unit_type is UnitType.FILE_HEADER
    assert header.metadata["file_path"] == str(path)
    assert header.metadata["language"] == "python"

    # the code unit preserves the whole-file line range
    code_units = [u for u in source.units if u.unit_type is UnitType.CODE]
    assert len(code_units) == 1
    assert code_units[0].locator.line_start == 1
    assert code_units[0].locator.line_end == 6


def test_python_symbol_units_have_names_and_line_ranges():
    import tempfile, os

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(PY)
        p = fh.name
    try:
        source = CodeParser().parse(p, os.path.basename(p))
    finally:
        os.unlink(p)

    symbols = [u for u in source.units if u.unit_type is UnitType.SYMBOL]
    by_name = {u.locator.symbol_name: u for u in symbols}
    assert set(by_name) == {"greet", "Greeter"}

    greet = by_name["greet"]
    assert greet.locator.symbol_type == "function"
    assert greet.locator.line_start == 1
    assert greet.locator.line_end == 3  # up to the next top-level symbol
    assert greet.locator.symbol_name == "greet"

    greeter = by_name["Greeter"]
    assert greeter.locator.symbol_type == "class"
    assert greeter.locator.line_start == 4
    assert greeter.locator.line_end == 6


def test_round_trip_to_dict_from_dict(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(PY, encoding="utf-8")
    source = CodeParser().parse(str(path), "sample.py")

    data = source.to_dict()
    restored = NormalizedSource.from_dict(data)

    assert restored.source_id == source.source_id
    assert restored.source_type == "code"
    assert restored.language == "python"
    assert len(restored.units) == len(source.units)
    for a, b in zip(restored.units, source.units):
        assert a.unit_type is b.unit_type
        assert a.text == b.text
        assert a.order == b.order
        assert a.metadata == b.metadata
        assert a.locator.symbol_name == b.locator.symbol_name
        assert a.locator.line_start == b.locator.line_start
        assert a.locator.line_end == b.locator.line_end


def test_plsql_parse_detects_package_and_body_symbols(tmp_path):
    path = tmp_path / "emp.pks"
    path.write_text(PLS, encoding="utf-8")
    source = CodeParser().parse(str(path), "emp.pks")

    assert source.source_type == "code"
    assert source.language == "plsql"

    symbols = [u for u in source.units if u.unit_type is UnitType.SYMBOL]
    types = {u.locator.symbol_type for u in symbols}
    assert any("PACKAGE" in t for t in types)
    assert any("PACKAGE BODY" in t for t in types)
    assert all(u.locator.symbol_name == "EMP_PKG" for u in symbols)


def test_router_routes_code_extensions(tmp_path):
    router = ParserRouter()
    assert router.detect_source_type(filename="app.py") == "code"
    assert router.detect_source_type(filename="pkg.pks") == "code"
    assert router.detect_source_type(filename="schema.sql") == "code"
    assert router.detect_source_type(filename="config.json") == "code"

    path = tmp_path / "app.py"
    path.write_text(PY, encoding="utf-8")
    result = router.parse(str(path), "app.py")
    assert result.source_type == "code"
    assert result.units[0].unit_type is UnitType.FILE_HEADER

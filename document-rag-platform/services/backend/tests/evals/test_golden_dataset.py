from golden_spec import (
    DEFAULT_DATASET_PATH,
    REQUIRED_CATEGORIES,
    category_coverage,
    load_golden,
    missing_categories,
    validate_dataset,
)


def test_dataset_exists():
    assert DEFAULT_DATASET_PATH.is_file(), (
        f"golden dataset bulunamadi: {DEFAULT_DATASET_PATH}"
    )


def test_dataset_has_at_least_50_items():
    records = load_golden()
    assert len(records) >= 50


def test_dataset_schema_valid():
    records = load_golden()
    result = validate_dataset(records)
    assert result["ok"] is True, result["errors"]


def test_dataset_ids_unique():
    records = load_golden()
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), "id'ler tekrar ediyor"


def test_all_91_categories_present():
    records = load_golden()
    missing = missing_categories(records)
    assert not missing, f"eksik kategori(ler): {missing}"


def test_expected_category_coverage_report():
    records = load_golden()
    coverage = category_coverage(records)
    for tag in REQUIRED_CATEGORIES:
        assert coverage[tag] >= 1, f"'{tag}' kategorisi en az 1 soru icermeli"

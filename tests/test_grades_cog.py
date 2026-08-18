"""
tests/test_grades_cog.py

Tests the pure calculation functions from cogs/grades.py --
_weighted_average and _classification -- with no Discord interaction
or database involved. These formalize the exact manual calculation
verified live earlier in the project (72%/5cr + 65%/5cr -> 68.5%, 2.1).
"""

from cogs.grades import _weighted_average, _classification


# ---------- _weighted_average ----------

def test_weighted_average_matches_manually_verified_result():
    # The exact real-world case checked live during manual testing.
    grades = [
        {"grade": 72.0, "credits": 5.0},
        {"grade": 65.0, "credits": 5.0},
    ]
    assert _weighted_average(grades) == 68.5


def test_weighted_average_weights_larger_credits_more_heavily():
    grades = [
        {"grade": 90.0, "credits": 1.0},
        {"grade": 50.0, "credits": 10.0},
    ]
    # A single high-grade, low-credit module shouldn't dominate a much
    # heavier low-grade module.
    result = _weighted_average(grades)
    assert 50.0 < result < 55.0


def test_weighted_average_single_module_equals_its_own_grade():
    assert _weighted_average([{"grade": 77.0, "credits": 5.0}]) == 77.0


def test_weighted_average_empty_list_returns_none():
    assert _weighted_average([]) is None


def test_weighted_average_zero_total_credits_returns_none():
    # Guards against a divide-by-zero if every logged module somehow had 0 credits.
    assert _weighted_average([{"grade": 80.0, "credits": 0.0}]) is None


# ---------- _classification ----------

def test_classification_first_class_honours():
    assert _classification(70.0) == "First Class Honours (1.1)"
    assert _classification(95.0) == "First Class Honours (1.1)"


def test_classification_second_class_grade_1():
    assert _classification(69.99) == "Second Class Honours, Grade 1 (2.1)"
    assert _classification(60.0) == "Second Class Honours, Grade 1 (2.1)"


def test_classification_second_class_grade_2():
    assert _classification(59.99) == "Second Class Honours, Grade 2 (2.2)"
    assert _classification(50.0) == "Second Class Honours, Grade 2 (2.2)"


def test_classification_third_class():
    assert _classification(49.99) == "Third Class Honours"
    assert _classification(40.0) == "Third Class Honours"


def test_classification_below_honours_threshold():
    assert _classification(39.99) == "Below honours threshold"
    assert _classification(0.0) == "Below honours threshold"

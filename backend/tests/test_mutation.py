import ast
from pathlib import Path

import pytest

from repo_reviewer.mutation import (
    ALL_OPERATORS,
    apply_line,
    apply_mutant,
    find_candidates,
    select_mutants,
)


SAMPLE = '''def clamp(value, low, high):
    if value is None:
        return low
    if value <= low and high > 0:
        return low
    total = value + high
    try:
        return int(total)
    except ValueError:
        return 0
'''


def _only(source: str, operator: str):
    found = find_candidates(source, "sample.py", operator)
    assert found, f"{operator} produced no candidate"
    return found


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("off_by_one", "if value < low and high > 0:"),
        ("invert_none_check", "if value is not None:"),
        ("widen_except", "except Exception:"),
    ],
)
def test_operator_produces_expected_line(operator: str, expected: str) -> None:
    mutated_lines = {m.mutated_line.strip() for m in _only(SAMPLE, operator)}
    assert expected in mutated_lines


def test_negate_condition_wraps_test_in_not() -> None:
    mutated = {m.mutated_line.strip() for m in _only(SAMPLE, "negate_condition")}
    assert "if not (value is None):" in mutated
    assert "if not (value <= low and high > 0):" in mutated


def test_swap_operator_covers_arithmetic_and_boolean() -> None:
    mutated = {m.mutated_line.strip() for m in _only(SAMPLE, "swap_operator")}
    assert "total = value - high" in mutated
    assert "if value <= low or high > 0:" in mutated


@pytest.mark.parametrize("operator", ALL_OPERATORS)
def test_every_mutant_still_parses(operator: str) -> None:
    for mutant in find_candidates(SAMPLE, "sample.py", operator):
        ast.parse(apply_line(SAMPLE, mutant.line, mutant.mutated_line))


@pytest.mark.parametrize("operator", ALL_OPERATORS)
def test_mutation_preserves_line_count(operator: str) -> None:
    """Positional scoring is only meaningful if no other line shifts."""
    original_count = len(SAMPLE.splitlines())
    for mutant in find_candidates(SAMPLE, "sample.py", operator):
        mutated = apply_line(SAMPLE, mutant.line, mutant.mutated_line)
        assert len(mutated.splitlines()) == original_count
        assert mutant.original_line == SAMPLE.splitlines()[mutant.line - 1]


def test_operators_ignore_strings_and_comments() -> None:
    source = 'MESSAGE = "use <= here"  # prefer <= over <\nVALUE = 1\n'
    assert find_candidates(source, "s.py", "off_by_one") == []


def test_widen_except_skips_already_broad_handler() -> None:
    source = "try:\n    pass\nexcept Exception:\n    pass\n"
    assert find_candidates(source, "s.py", "widen_except") == []


def test_find_candidates_returns_empty_for_unparseable_source() -> None:
    assert find_candidates("def broken(:\n", "s.py", "off_by_one") == []


def test_apply_line_preserves_trailing_newline_shape() -> None:
    assert apply_line("a\nb\n", 1, "z") == "z\nb\n"
    assert apply_line("a\nb", 1, "z") == "z\nb"


def test_apply_line_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        apply_line("a\n", 5, "z")


def _write_repo(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(SAMPLE, encoding="utf-8")
    (root / "pkg" / "other.py").write_text(SAMPLE, encoding="utf-8")


def test_select_mutants_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    files = ["pkg/core.py", "pkg/other.py"]
    first = select_mutants(tmp_path, files, count=5, seed=7)
    second = select_mutants(tmp_path, files, count=5, seed=7)
    assert [(m.file, m.line, m.operator) for m in first] == [
        (m.file, m.line, m.operator) for m in second
    ]


def test_select_mutants_spreads_across_operators(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    mutants = select_mutants(tmp_path, ["pkg/core.py"], count=5, seed=1)
    assert len({m.operator for m in mutants}) == 5


def test_select_mutants_only_targets_supplied_files(tmp_path: Path) -> None:
    """Files outside `files_to_review` are never shown to the reviewer."""
    _write_repo(tmp_path)
    mutants = select_mutants(tmp_path, ["pkg/core.py"], count=20, seed=1)
    assert mutants
    assert {m.file for m in mutants} == {"pkg/core.py"}


def test_select_mutants_skips_non_python_and_missing_files(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / "README.md").write_text("if a <= b", encoding="utf-8")
    mutants = select_mutants(tmp_path, ["README.md", "pkg/gone.py"], count=5, seed=1)
    assert mutants == []


def test_apply_mutant_writes_file(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    mutant = select_mutants(tmp_path, ["pkg/core.py"], count=1, seed=3)[0]
    apply_mutant(tmp_path, mutant)
    written = (tmp_path / mutant.file).read_text(encoding="utf-8").splitlines()
    assert written[mutant.line - 1] == mutant.mutated_line
    assert len(written) == len(SAMPLE.splitlines())


def test_undecodable_file_is_skipped_rather_than_crashing(tmp_path: Path) -> None:
    """apply_mutant reads strictly, so selection must not offer a lossy candidate."""
    (tmp_path / "legacy.py").write_bytes(
        "x = 1\nif a <= b:  # caf\xe9\n    pass\n".encode("latin-1")
    )
    (tmp_path / "clean.py").write_text(SAMPLE, encoding="utf-8")

    mutants = select_mutants(tmp_path, ["legacy.py", "clean.py"], count=10, seed=1)

    assert mutants, "the decodable file should still yield mutants"
    assert all(m.file == "clean.py" for m in mutants)
    for mutant in mutants:
        apply_mutant(tmp_path, mutant)  # must not raise UnicodeDecodeError

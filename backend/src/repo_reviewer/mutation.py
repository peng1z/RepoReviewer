from __future__ import annotations

import ast
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal


MutationOperator = Literal[
    "off_by_one",
    "negate_condition",
    "swap_operator",
    "invert_none_check",
    "widen_except",
]

ALL_OPERATORS: tuple[MutationOperator, ...] = (
    "off_by_one",
    "negate_condition",
    "swap_operator",
    "invert_none_check",
    "widen_except",
)


@dataclass(frozen=True, slots=True)
class Mutant:
    """A single injected defect with the ground truth needed to score it."""

    operator: MutationOperator
    file: str
    line: int
    original_line: str
    mutated_line: str
    description: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    line: int
    mutated_line: str
    description: str


# Phrases specific enough that their presence implies the finding is about this
# defect class, not merely about the same area of code.
#
# The first benchmark run showed why that bar matters. "except" and "exception"
# each fired on 11 weak hits, and sampling them found several that described an
# unrelated `assert` problem in a file that happened to contain a try/except.
# Across all operators, 8 of 31 weak hits matched only on such ubiquitous words,
# inflating weak_or_better_rate by roughly seven points. Bare "except",
# "exception", "none", "index", "edge case" and "operator" are deliberately
# absent below; they name the neighbourhood, not the defect.
OPERATOR_KEYWORDS: dict[MutationOperator, tuple[str, ...]] = {
    "off_by_one": (
        "off-by-one", "off by one", "boundary", "bounds", "inclusive", "exclusive",
        "fencepost", "comparison operator", "one too", "boundary value",
    ),
    "negate_condition": (
        "negat", "inverted", "inverse", "backwards", "opposite",
        "wrong condition", "condition is reversed", "reversed", "logic is inverted",
    ),
    "swap_operator": (
        "arithmetic", "addition", "subtraction", "subtract",
        "should be `and`", "should be `or`", "should be and", "should be or",
        "boolean logic", "wrong sign", "wrong operator",
    ),
    "invert_none_check": (
        "null check", "none check", "nonetype", "attributeerror", "inverted check",
        "is not none", "is none", "missing check", "null guard", "none guard",
    ),
    "widen_except": (
        "broad except", "bare except", "except exception", "swallow", "silently",
        "catch-all", "too general", "masks the", "overly broad", "catches all",
    ),
}


def _tolerant_parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _replace_once(line: str, pattern: str, replacement: str) -> str | None:
    """Replace the first regex match outside of string literals and comments."""
    stripped = _strip_literals(line)
    match = re.search(pattern, stripped)
    if match is None:
        return None
    return line[: match.start()] + replacement + line[match.end() :]


def _strip_literals(line: str) -> str:
    """Blank out comments and string bodies so operator searches ignore them.

    Positions are preserved (characters are replaced, never removed) so indices
    found in the stripped line apply unchanged to the original line.
    """
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is None:
            if char == "#":
                out.append(" " * (len(line) - index))
                break
            if char in {'"', "'"}:
                quote = char
                out.append(" ")
            else:
                out.append(char)
        else:
            if char == "\\":
                # Consume the escape pair as two blanks -- but a backslash at
                # end of line (a continued string) escapes nothing, so emit one.
                out.append("  " if index + 1 < len(line) else " ")
                index += 2
                continue
            out.append(" ")
            if char == quote:
                quote = None
        index += 1
    return "".join(out)


def _off_by_one(tree: ast.Module, lines: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op in node.ops:
            if not isinstance(op, (ast.LtE, ast.GtE)):
                continue
            line_no = node.lineno
            if line_no in seen or line_no > len(lines):
                continue
            original = lines[line_no - 1]
            token = "<=" if isinstance(op, ast.LtE) else ">="
            mutated = _replace_once(original, re.escape(token), token[0])
            if mutated is None:
                continue
            seen.add(line_no)
            candidates.append(
                _Candidate(
                    line=line_no,
                    mutated_line=mutated,
                    description=(
                        f"Boundary comparison `{token}` was weakened to `{token[0]}`, "
                        "so the boundary value is no longer handled."
                    ),
                )
            )
    return candidates


def _negate_condition(tree: ast.Module, lines: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Only single-line tests, so the edit stays on one line.
        if getattr(test, "end_lineno", None) != test.lineno:
            continue
        line_no = test.lineno
        if line_no > len(lines):
            continue
        original = lines[line_no - 1]
        match = re.match(r"^(\s*)(el)?if (.+?):\s*$", original)
        if match is None:
            continue
        indent, elif_prefix, condition = match.group(1), match.group(2) or "", match.group(3)
        mutated = f"{indent}{elif_prefix}if not ({condition}):"
        candidates.append(
            _Candidate(
                line=line_no,
                mutated_line=mutated,
                description=(
                    "Branch condition was negated, so the body now runs in exactly "
                    "the cases where it previously did not."
                ),
            )
        )
    return candidates


def _swap_operator(tree: ast.Module, lines: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        line_no = getattr(node, "lineno", None)
        if line_no is None or line_no in seen or line_no > len(lines):
            continue
        original = lines[line_no - 1]
        mutated: str | None = None
        description = ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            if isinstance(node.op, ast.Add):
                mutated = _replace_once(original, r"(?<![+=<>!*/-])\+(?!=)", "-")
                description = "Arithmetic `+` was changed to `-`."
            else:
                mutated = _replace_once(original, r"(?<![+=<>!*/-])-(?![=>])", "+")
                description = "Arithmetic `-` was changed to `+`."
        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                mutated = _replace_once(original, r"\band\b", "or")
                description = "Boolean `and` was changed to `or`, widening the condition."
            else:
                mutated = _replace_once(original, r"\bor\b", "and")
                description = "Boolean `or` was changed to `and`, narrowing the condition."
        if mutated is None or mutated == original:
            continue
        seen.add(line_no)
        candidates.append(_Candidate(line=line_no, mutated_line=mutated, description=description))
    return candidates


def _invert_none_check(tree: ast.Module, lines: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        comparator = node.comparators[0]
        if not (isinstance(comparator, ast.Constant) and comparator.value is None):
            continue
        line_no = node.lineno
        if line_no in seen or line_no > len(lines):
            continue
        original = lines[line_no - 1]
        if isinstance(node.ops[0], ast.Is):
            mutated = _replace_once(original, r"\bis\s+None\b", "is not None")
            description = "A `is None` guard was inverted to `is not None`, disabling the null check."
        elif isinstance(node.ops[0], ast.IsNot):
            mutated = _replace_once(original, r"\bis\s+not\s+None\b", "is None")
            description = "A `is not None` guard was inverted to `is None`, disabling the null check."
        else:
            continue
        if mutated is None:
            continue
        seen.add(line_no)
        candidates.append(_Candidate(line=line_no, mutated_line=mutated, description=description))
    return candidates


def _widen_except(tree: ast.Module, lines: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if isinstance(node.type, ast.Name) and node.type.id == "Exception":
            continue
        line_no = node.lineno
        if line_no > len(lines):
            continue
        original = lines[line_no - 1]
        mutated = re.sub(
            r"^(\s*except\s+)(.+?)(\s+as\s+\w+)?\s*:\s*$",
            lambda m: f"{m.group(1)}Exception{m.group(3) or ''}:",
            original,
        )
        if mutated == original:
            continue
        candidates.append(
            _Candidate(
                line=line_no,
                mutated_line=mutated,
                description=(
                    "A specific exception handler was widened to `except Exception`, "
                    "so unrelated failures are now swallowed."
                ),
            )
        )
    return candidates


_FINDERS: dict[MutationOperator, Callable[[ast.Module, list[str]], list[_Candidate]]] = {
    "off_by_one": _off_by_one,
    "negate_condition": _negate_condition,
    "swap_operator": _swap_operator,
    "invert_none_check": _invert_none_check,
    "widen_except": _widen_except,
}


def find_candidates(source: str, relative_path: str, operator: MutationOperator) -> list[Mutant]:
    """Return every valid single-line mutant of `operator` for this source file.

    A candidate is only returned if the mutated source still parses, so callers
    never receive a mutant that breaks the file syntactically.
    """
    tree = _tolerant_parse(source)
    if tree is None:
        return []
    lines = source.splitlines()
    mutants: list[Mutant] = []
    for candidate in _FINDERS[operator](tree, lines):
        mutated_source = apply_line(source, candidate.line, candidate.mutated_line)
        if _tolerant_parse(mutated_source) is None:
            continue
        mutants.append(
            Mutant(
                operator=operator,
                file=relative_path,
                line=candidate.line,
                original_line=lines[candidate.line - 1],
                mutated_line=candidate.mutated_line,
                description=candidate.description,
                keywords=OPERATOR_KEYWORDS[operator],
            )
        )
    return mutants


def apply_line(source: str, line: int, replacement: str) -> str:
    """Replace one 1-indexed line, preserving the file's trailing-newline shape."""
    lines = source.splitlines()
    if not 1 <= line <= len(lines):
        raise ValueError(f"line {line} out of range for {len(lines)}-line source")
    lines[line - 1] = replacement
    text = "\n".join(lines)
    return text + "\n" if source.endswith("\n") else text


def apply_mutant(repo_root: Path, mutant: Mutant) -> None:
    """Write the mutated version of `mutant.file` into the working copy."""
    path = repo_root / mutant.file
    source = path.read_text(encoding="utf-8")
    path.write_text(apply_line(source, mutant.line, mutant.mutated_line), encoding="utf-8")


def select_mutants(
    repo_root: Path,
    candidate_files: list[str],
    *,
    operators: tuple[MutationOperator, ...] = ALL_OPERATORS,
    count: int,
    seed: int,
) -> list[Mutant]:
    """Pick up to `count` mutants, spread across operators, from reviewable files.

    `candidate_files` must be the files the pipeline will actually review -- see
    `benchmark.plan_mutants`. Selection is seeded so a run is reproducible.
    """
    by_operator: dict[MutationOperator, list[Mutant]] = {op: [] for op in operators}
    for relative in candidate_files:
        if not relative.endswith(".py"):
            continue
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # apply_mutant() reads strictly. Selecting from a lossy decode here
            # would either crash on injection or write back a corrupted file.
            continue
        for operator in operators:
            by_operator[operator].extend(find_candidates(source, relative, operator))

    rng = random.Random(seed)
    for pool in by_operator.values():
        rng.shuffle(pool)

    # Round-robin across operators so one defect class cannot dominate the sample.
    selected: list[Mutant] = []
    while len(selected) < count:
        progressed = False
        for operator in operators:
            if len(selected) >= count:
                break
            pool = by_operator[operator]
            if pool:
                selected.append(pool.pop())
                progressed = True
        if not progressed:
            break
    return selected

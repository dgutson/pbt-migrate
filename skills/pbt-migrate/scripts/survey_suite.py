#!/usr/bin/env python3
"""Survey a pytest suite and report, per test, the structural facts that decide
whether it hides a property worth expressing as a Hypothesis test.

Establishes facts only. Every judgement call — is there a real property here,
would parametrising this produce a mirror — is left to the reader.

    python3 survey_suite.py tests/            # human-readable summary
    python3 survey_suite.py tests/ --json     # full detail for programmatic use
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

# Signals, and the section of references/evolving-tests.md that handles each.
SIGNAL_GUIDE = {
    "parametrized": "Loop over hardcoded examples",
    "example_family": "Multiple tests, same assertion shape",
    "exact_transform": "A single test asserting one exact transformation",
    "robustness": "Tests asserting 'no error' on various inputs",
    "seeded_rng": "Tests with manually seeded RNGs",
    "mutation_sequence": "Setup, operations, check final state",
}

# Rough ordering of how often each signal actually yields a property, worst
# first. Used only to rank the report; it is not a verdict.
SIGNAL_WEIGHT = {
    "example_family": 3,
    "parametrized": 3,
    "mutation_sequence": 2,
    "seeded_rng": 2,
    "robustness": 1,
    "exact_transform": 1,
}


class _Blank(ast.NodeTransformer):
    """Replace every literal with `...` so two assertions that differ only in
    their example values collapse to the same string."""

    def visit_Constant(self, node):  # noqa: N802 - ast API
        return ast.Constant(value=Ellipsis)


def shape(node) -> str:
    """Structural fingerprint of an expression, literals erased."""
    return unparse(_Blank().visit(copy.deepcopy(node)))

RNG_SEEDERS = {"random.Random", "random.seed", "np.random.seed", "numpy.random.seed"}


def unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def call_name(node: ast.Call) -> str:
    return unparse(node.func)


def is_pbt_decorator(dec) -> bool:
    text = unparse(dec)
    return text.startswith("given") or text.startswith("hypothesis") or ".given" in text


def parametrize_cases(dec) -> tuple[int, str] | None:
    """Return (case_count, param_names) if this decorator is a parametrize."""
    if not isinstance(dec, ast.Call):
        return None
    if "parametrize" not in unparse(dec.func):
        return None
    names = ""
    count = 0
    if dec.args:
        first = dec.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names = first.value
    if len(dec.args) > 1 and isinstance(dec.args[1], (ast.List, ast.Tuple)):
        count = len(dec.args[1].elts)
    return count, names


def _call_with_literal_args(node) -> bool:
    return isinstance(node, ast.Call) and any(
        isinstance(a, ast.Constant) for a in node.args
    )


def _compares_call_to_literal(node) -> bool:
    """True for `f("x") == "y"` and `self.assertEqual(f("x"), "y")`.

    This is the shape most likely to become a mirror when parametrised: the
    literal on the right is the only thing describing the output, so replacing
    it with a strategy tempts you into retyping the implementation instead.
    """
    if isinstance(node, ast.Compare) and len(node.comparators) == 1:
        left, right = node.left, node.comparators[0]
        return (_call_with_literal_args(left) and isinstance(right, ast.Constant)) or (
            _call_with_literal_args(right) and isinstance(left, ast.Constant)
        )
    if isinstance(node, ast.Call) and call_name(node).startswith("self.assert"):
        if len(node.args) == 2:
            a, b = node.args
            return (_call_with_literal_args(a) and isinstance(b, ast.Constant)) or (
                _call_with_literal_args(b) and isinstance(a, ast.Constant)
            )
    return False


def analyse_test(fn: ast.FunctionDef, class_name: str | None, path: Path) -> dict:
    decorators = [unparse(d) for d in fn.decorator_list]
    already_pbt = any(is_pbt_decorator(d) for d in fn.decorator_list)

    parametrize = None
    for d in fn.decorator_list:
        got = parametrize_cases(d)
        if got:
            parametrize = {"cases": got[0], "params": got[1]}

    calls: list[str] = []
    assert_nodes: list[ast.AST] = []
    seeded: list[str] = []
    raises = False
    mutations: dict[str, int] = defaultdict(int)

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = call_name(node)
            calls.append(name)
            if name in RNG_SEEDERS and any(
                isinstance(a, ast.Constant) for a in node.args
            ):
                seeded.append(name)
            if "raises" in name:
                raises = True
            # unittest-style assertions are method calls, not `assert` statements
            if name.startswith("self.assert"):
                assert_nodes.append(node)
        elif isinstance(node, ast.Assert):
            assert_nodes.append(node.test)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                mutations[f.value.id] += 1

    asserts = [unparse(n) for n in assert_nodes]
    shapes = [shape(n) for n in assert_nodes]

    signals: list[str] = []
    if parametrize and parametrize["cases"] >= 2:
        signals.append("parametrized")
    if seeded:
        signals.append("seeded_rng")
    # `robustness` is deliberately NOT a per-test signal. A single
    # `pytest.raises(SomeError)` test is usually a good unit test that should be
    # left alone; the property ("nothing but the documented error escapes") is
    # only worth writing when several such tests pile up on one target. It is
    # promoted to a signal in survey() once a group of three is found.
    robust_candidate = bool(raises or not asserts)
    if any(v >= 2 for v in mutations.values()) and asserts:
        signals.append("mutation_sequence")
    if len(assert_nodes) == 1 and not parametrize and _compares_call_to_literal(
        assert_nodes[0]
    ):
        signals.append("exact_transform")

    # Targets: called names that are not test scaffolding. Used to group tests
    # that exercise the same function.
    targets = sorted(
        {
            c.split("(")[0]
            for c in calls
            if not c.startswith(("self.assert", "pytest.", "mock.", "patch"))
            and c not in RNG_SEEDERS
        }
    )

    return {
        "file": str(path),
        "line": fn.lineno,
        "name": fn.name,
        "class": class_name,
        "already_pbt": already_pbt,
        "decorators": decorators,
        "parametrize": parametrize,
        "assert_count": len(asserts),
        "asserts": asserts[:4],
        "shapes": shapes,
        "targets": targets,
        "signals": signals,
        "robust_candidate": robust_candidate,
    }


def survey(paths: list[Path]) -> dict:
    tests: list[dict] = []
    unparseable: list[str] = []
    files: list[Path] = []

    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("test_*.py")))
            files.extend(sorted(p.rglob("*_test.py")))
        elif p.is_file():
            files.append(p)

    for f in sorted(set(files)):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            unparseable.append(f"{f}: {e}")
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test"):
                        tests.append(analyse_test(sub, node.name, f))
            elif isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                tests.append(analyse_test(node, None, f))

    by_target: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(tests):
        for tg in t["targets"]:
            by_target[tg].append(i)

    # The "these are examples of one property" case: several tests whose
    # assertions are structurally identical once the example values are erased.
    # Keying on the raw text instead would collapse every `self.assertEqual`
    # in the suite into one bogus family.
    by_shape: dict[str, set[int]] = defaultdict(set)
    for i, t in enumerate(tests):
        for s in t["shapes"]:
            by_shape[s].add(i)

    families = []
    for s, idxs in by_shape.items():
        if len(idxs) >= 3:
            families.append({"shape": s, "tests": sorted(idxs), "count": len(idxs)})
            for i in idxs:
                if "example_family" not in tests[i]["signals"]:
                    tests[i]["signals"].append("example_family")
    families.sort(key=lambda f: -f["count"])

    # Promote `robustness` only where three or more error-shaped tests pile up
    # on the same target.
    robust_by_target: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(tests):
        if t["robust_candidate"] and not t["already_pbt"]:
            for tg in t["targets"]:
                robust_by_target[tg].append(i)
    for tg, idxs in robust_by_target.items():
        if len(idxs) >= 3:
            for i in idxs:
                if "robustness" not in tests[i]["signals"]:
                    tests[i]["signals"].append("robustness")

    return {
        "tests": tests,
        "families": families,
        "unparseable": unparseable,
        "targets": {k: len(v) for k, v in sorted(by_target.items()) if len(v) >= 2},
        "totals": {
            "files": len(set(files)),
            "tests": len(tests),
            "already_pbt": sum(1 for t in tests if t["already_pbt"]),
            "with_signals": sum(1 for t in tests if t["signals"] and not t["already_pbt"]),
            "strong": sum(
                1
                for t in tests
                if not t["already_pbt"]
                and sum(SIGNAL_WEIGHT.get(s, 0) for s in t["signals"]) >= 3
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="emit full JSON")
    args = ap.parse_args()

    result = survey(args.paths)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0

    t = result["totals"]
    print(f"{t['files']} files, {t['tests']} tests, "
          f"{t['already_pbt']} already property-based.\n"
          f"{t['strong']} strong candidates, "
          f"{t['with_signals'] - t['strong']} weak ones.\n")

    if result["unparseable"]:
        print("COULD NOT PARSE (excluded from all counts above):")
        for u in result["unparseable"]:
            print(f"  {u}")
        print()

    if result["families"]:
        print("EXAMPLE FAMILIES - tests whose assertions are structurally identical")
        print("once the example values are erased. Start here: a family is the")
        print("strongest evidence that one property is being spelled out by hand.\n")
        for f in result["families"][:10]:
            print(f"  {f['count']:3d} tests  {f['shape'][:88]}")
            for i in f["tests"][:4]:
                t = result["tests"][i]
                print(f"           {t['file']}:{t['line']}  {t['name']}")
            if len(f["tests"]) > 4:
                print(f"           ... and {len(f['tests']) - 4} more")
        if len(result["families"]) > 10:
            print(f"  ... and {len(result['families']) - 10} more families "
                  "(not shown; rerun with --json for all)")
        print()

    ranked = [x for x in result["tests"] if x["signals"] and not x["already_pbt"]]
    ranked.sort(
        key=lambda x: (
            -sum(SIGNAL_WEIGHT.get(s, 0) for s in x["signals"]),
            x["file"],
            x["line"],
        )
    )

    print("PER-TEST SIGNALS, strongest first\n")
    for x in ranked:
        loc = f"{x['file']}:{x['line']}"
        qual = f"{x['class']}.{x['name']}" if x["class"] else x["name"]
        print(f"{loc}  {qual}")
        for s in x["signals"]:
            print(f"    [{s}] -> {SIGNAL_GUIDE.get(s, '?')}")
        if x["parametrize"]:
            print(f"    parametrize: {x['parametrize']['cases']} cases "
                  f"({x['parametrize']['params']})")
        if x["targets"]:
            print(f"    calls: {', '.join(x['targets'][:6])}")
        print()

    hot = sorted(result["targets"].items(), key=lambda kv: -kv[1])[:15]
    if hot:
        print("Targets exercised by more than one test (grouping candidates):")
        for name, n in hot:
            print(f"  {n:3d}  {name}")

    print(f"\n{len(ranked)} of {t['tests']} tests carry a signal. "
          "A signal is a reason to LOOK, not a verdict — most tests should be "
          "left alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

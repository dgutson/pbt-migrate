---
name: pbt-migrate
description: >
  Triage an existing pytest or unittest suite and migrate the tests that hide a
  real property to Hypothesis, leaving the rest alone. Use when the user points
  at existing tests and asks to convert, migrate, port, or "hypothesis-ify" them,
  asks which of their tests should be property-based, wants to replace a wall of
  parametrize cases or a hand-seeded `random.Random(42)` with generated inputs,
  or asks whether an existing test suite would benefit from PBT. This is for
  tests that already exist. For writing property tests against source code that
  has none, use the `/hypothesis` command instead.
---

# pbt-migrate

Take a suite that already exists, find the tests that are spelling out a
property by hand, and rewrite those as Hypothesis tests. Leave everything else
untouched.

## This is not the greenfield tool

The Hypothesis project ships a `/hypothesis` command that takes **source code**
and finds properties in it. This skill takes **tests** and asks which of them
were already trying to be properties.

If the user has no tests yet, stop and tell them to use that command instead.
Do not reimplement it here. It is an optional companion, not a dependency —
nothing here calls it — so if they don't have it, point them at the source:

    https://github.com/HypothesisWorks/hypothesis/blob/master/.claude/commands/hypothesis.md

Copy it to `~/.claude/commands/hypothesis.md` to install it.

## The rule that matters most

**Most tests should be left alone.** A suite of 200 tests might yield five
properties. If your triage converts a large fraction of what it looks at, the
triage is wrong, not the suite.

Converting a test that has no property produces something strictly worse than
what it replaced: it looks like coverage, it costs generation time on every
run, and it usually cannot fail. Reporting "these 40 tests are fine as they
are" is a real deliverable, not a failure to find work.

## Workflow

### 1. Survey the suite mechanically

Run the bundled script. Never enumerate tests by reading files — you will
miscount parametrize cases and miss files.

```bash
python3 scripts/survey_suite.py <tests-dir-or-file>
python3 scripts/survey_suite.py <tests-dir-or-file> --json   # full detail
```

It reports, per test: whether it is already property-based, its parametrize
case count, its assertion shapes with literals erased, what it calls, and which
signals it carries. It also groups tests into **example families** — sets of
three or more tests whose assertions are structurally identical once the
example values are removed.

A signal means *look here*. It is never a verdict. The script establishes
facts; every judgement below is yours.

### 2. Start with the families, not the per-test list

An example family is the strongest evidence in the report, because it is the
literal shape of a property written out by hand: the same assertion, N times,
with different values. Work families first, then parametrized tests, then the
rest.

### 3. Name the property before touching any code

For each candidate, load `references/evolving-tests.md` and find the section
matching its signal. Write down, in one sentence, the property you believe the
test is an instance of.

If you cannot state it in one sentence without naming a specific input value,
there is no property. Verdict: **leave alone**. Move on.

### 4. Check how the code is actually used

Before writing anything, read the callers of the function under test. A
property that holds only for the inputs the codebase actually passes is worth
more than a general-looking property that is false. This is also where you find
out whether the test asserts on a private attribute when a public accessor
exists — assert through the public surface.

### 5. Both gates must pass

These are blocking. A test that fails either one is not written.

**Gate 1 — is the oracle independent?**

Ask: *could I reconstruct the implementation from my assertion?* If yes, the
assertion is the implementation retyped, and the test cannot fail for any bug
present in both places.

```python
# BAD - the oracle is the function body, copied
@given(st.text(min_size=1))
def test_all_trailing_slashes_stripped(url):
    assert Endpoint(url).url == url.rstrip("/")
```

The fix is almost always to split the equality into a **postcondition** (what
must no longer hold) and a **conservation law** (what must be preserved):

```python
# GOOD - describes the output; construct the interesting input
@given(st.text(), st.integers(min_value=0, max_value=5))
def test_trailing_slashes_stripped(base, n):
    url = base + "/" * n
    result = Endpoint(url).url
    assert not result.endswith("/")                              # postcondition
    assert url == result + "/" * (len(url) - len(result))        # conservation
```

Without the conservation half, a function returning `""` passes.

A legitimate independent oracle is: a simpler reference implementation, the
inverse operation, a different implementation of the same interface, or the
standard library. Retyping the one-liner under test is not one.

For a stateful test, the same rule applies to the model: a model that
reimplements the logic under test will agree with it even when it is wrong.
Keep models to a plain `list` or `dict`.

**Gate 2 — does the generator actually reach the behaviour?**

A test that never generates an interesting input passes forever and proves
nothing. `st.text()` essentially never produces a string ending in `/`, so the
bad test above exercised the no-op branch on nearly every example.

The fix is usually to **construct** the feature rather than to narrow the
alphabet — `base + "/" * n` with `n >= 0` keeps full Unicode and still covers
the no-op case. Narrowing to `st.text(alphabet="ab/")` is a second, targeted
test, not a replacement.

Verify rather than assume:

```python
from hypothesis import event

@given(...)
def test_something(x):
    event(f"trailing slash: {x.endswith('/')}")
```

then run with `pytest --hypothesis-show-statistics` and read the distribution.
If one branch is under a few percent, the test is close to vacuous — fix the
strategy before believing the green tick.

### 6. Decide what happens to the old test

Every converted test gets one of three verdicts, recorded explicitly:

| Verdict | When | What you do |
|---|---|---|
| **Convert** | The PBT covers the whole input space the examples sampled | Replace the old tests |
| **Keep and add** | An example encodes a specific bug or subtle case worth documenting | Add the PBT; keep the example, or fold it in as `@example(...)` |
| **Leave alone** | No property, or the only property would be a mirror | Change nothing |

Replace in place — add Hypothesis tests in the file where the unit tests live.
Do not create a parallel test file.

### 7. Run them

Run the new tests. A failure is more likely to be a wrong test than a real bug:
usually the strategy generates inputs the code was never meant to receive. Go
back to step 4 before concluding anything.

If you do confirm a genuine bug, pin the shrunk input with `@example(...)` on
the property test and report it. Do not narrow the strategy to make it pass —
that converts a finding into a permanent blind spot.

Watch for state leaking across generated examples: `setUp` and
function-scoped fixtures run once per test *function*, not once per generated
example, so mocks accumulate call history across a `@given` run. Reset
explicitly inside the test.

### 8. Report the triage

The deliverable is the triage, not just the diff. Report:

- what you converted, and the property each new test asserts
- what you deliberately left alone, and why — this is the most useful part
- any behaviour a property test surfaced that looks like a bug
- any test you could not judge without more context

## Common mistakes

1. **Converting the whole suite.** If most candidates convert, re-read gate 1.
2. **Parametrize → `@given` with the same oracle.** The literal expected value
   was the only thing describing the output; replacing it with a strategy
   forces you to retype the implementation. This is the single most common way
   a migration produces a worthless test.
3. **Asserting on private attributes.** If the test reaches for `_url`, ask
   what the public surface is and assert through that instead.
4. **Believing a green run.** Check branch density with `event()` before
   trusting a passing property test.
5. **Deleting example tests that document a real bug.** Pin them with
   `@example(...)` instead of dropping them.
6. **Writing plain unit tests when no property is found.** Report it and stop.
   Adding unit tests as a consolation prize hides the finding.

## References

- `references/evolving-tests.md` — the catalogue: seven unit-test shapes, the
  property hiding in each, and what to do with the originals. Load it in step 3.
- Hypothesis API reference: https://hypothesis.readthedocs.io/en/latest/reference/api.html
  (`@given`, `@settings`, `@example`, and the stateful API)
- Strategies reference: https://hypothesis.readthedocs.io/en/latest/reference/strategies.html

# pbt-migrate

A Claude Code skill that triages an existing pytest/unittest suite and migrates
the tests that hide a real property to [Hypothesis](https://hypothesis.readthedocs.io/) —
leaving the rest alone.

## Why this exists

The Hypothesis project ships a `/hypothesis` command in its own repo
([`.claude/commands/hypothesis.md`](https://github.com/HypothesisWorks/hypothesis/tree/master/.claude)).
It is good, and it is the right tool for **greenfield** work: it reads source
code and finds properties in it.

It does not do migration. Its input is implementation, not tests, and it has no
notion of triaging a suite that already exists. Pointing it at a test file is
the worst way to use it — a greenfield property-finder aimed at a parametrized
test will happily replace the literals with a strategy and keep "the same"
oracle, which is exactly how you get a test that cannot fail.

This skill covers that gap, and only that gap.

## The core claim

**Most tests should be left alone.** A suite of 200 tests might yield five
properties. The deliverable is a triage with three verdicts — convert, keep and
add, leave alone — and "leave alone" is the common one.

A migration tool that converts everything it is pointed at is worse than no
tool, because it manufactures tests that look like coverage and cannot fail.

## What's in it

```
skills/pbt-migrate/
├── SKILL.md                        the workflow and the two blocking gates
├── references/evolving-tests.md    seven unit-test shapes → the property in each
└── scripts/survey_suite.py         AST survey of a suite (stdlib only)
```

### The survey script

Establishes facts so the model doesn't have to guess them:

```bash
python3 scripts/survey_suite.py tests/
python3 scripts/survey_suite.py tests/ --json
```

Per test: already property-based?, parametrize case count, assertion shapes
with literals erased, call targets, and which signals it carries. Across tests
it finds **example families** — three or more tests whose assertions are
structurally identical once example values are erased. That is a property
written out by hand, and it is the strongest signal in the report.

Signals are reasons to look, never verdicts.

### The two gates

Both are blocking. A candidate that fails either is not converted.

1. **Is the oracle independent?** If you could reconstruct the implementation
   from the assertion, the test cannot fail for any bug present in both places.
   The fix is postcondition + conservation law.
2. **Does the generator reach the behaviour?** `st.text()` essentially never
   produces a string ending in `/`. Construct the interesting input rather than
   narrowing the alphabet, and verify with `event()` plus
   `--hypothesis-show-statistics` instead of trusting a green run.

## Install

As a plugin:

```bash
claude plugin marketplace add dgutson/pbt-migrate
claude plugin install pbt-migrate
```

Or as a personal skill:

```bash
git clone https://github.com/dgutson/pbt-migrate.git ~/src/pbt-migrate
ln -s ~/src/pbt-migrate/skills/pbt-migrate ~/.claude/skills/pbt-migrate
```

Then invoke with `/pbt-migrate`, or just ask to convert some tests.

No dependencies — the survey script is stdlib-only Python 3.9+. The
`/hypothesis` command is a useful companion for greenfield work but is not
required; nothing here calls it.

## Status

Early. The signal heuristics were calibrated against three suites, including
Hypothesis's own `tests/cover` (1731 tests, 104 files), and land at 28–40% of
non-property tests flagged as strong candidates. Two heuristics were rewritten
during that calibration: assertion families are fingerprinted structurally
rather than by text (or every `self.assertEqual` in a suite collapses into one
bogus family), and `robustness` only fires when three or more error-shaped
tests pile up on one target (a lone `pytest.raises` is a good unit test and
should be left alone).

The verdict taxonomy has not yet been exercised on a full real migration.

## License

MIT

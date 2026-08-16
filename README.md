# pbt-migrate

A Claude Code skill that triages an existing pytest/unittest suite and migrates
the tests that hide a real property to [Hypothesis](https://hypothesis.readthedocs.io/) —
leaving the rest alone.

## Why this exists

The Hypothesis project ships a `/hypothesis` command in its own repo. It is
good, and it is the right tool for **greenfield** work: it reads source code and
finds properties in it.

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

## Install

```bash
claude plugin marketplace add dgutson/pbt-migrate
claude plugin install pbt-migrate
```

Or as a personal skill:

```bash
git clone https://github.com/dgutson/pbt-migrate.git
ln -s "$PWD/pbt-migrate/skills/pbt-migrate" ~/.claude/skills/pbt-migrate
```

No dependencies. The survey script is stdlib-only Python 3.9+.

## Workflow

### Using it on its own

**1. Point it at your tests.**

```
/pbt-migrate tests/
/pbt-migrate tests/test_parser.py
```

Or just ask: *"which of these tests should be property-based?"*

**2. It surveys the suite mechanically first.** Before any judgement, it runs
`survey_suite.py`, which walks the AST and establishes facts — parametrize case
counts, assertion shapes with literals erased, call targets, seeded RNGs, and
mutation sequences. You can run it yourself:

```bash
python3 skills/pbt-migrate/scripts/survey_suite.py tests/
python3 skills/pbt-migrate/scripts/survey_suite.py tests/ --json
```

The most useful thing it reports is **example families**: three or more tests
whose assertions are structurally identical once the example values are erased.
That is a property written out by hand, and it is where the real conversions
are.

**3. It works families first**, then parametrized tests, then everything else.
For each candidate it names the property in one sentence before touching code.
If it cannot state the property without naming a specific input value, the
verdict is *leave alone*.

**4. Two gates must pass before any test is written.**

- *Is the oracle independent?* If you could reconstruct the implementation from
  the assertion, the test cannot fail for any bug present in both places.
- *Does the generator reach the behaviour?* `st.text()` essentially never
  produces a string ending in `/`. A test that never generates an interesting
  input passes forever and proves nothing.

**5. You get a triage, not just a diff.** Every candidate lands on one of three
verdicts, and the report says what was deliberately left alone and why — which
is usually the most useful part.

| Verdict | When | Result |
|---|---|---|
| **Convert** | The PBT covers the space the examples sampled | Old tests replaced |
| **Keep and add** | An example documents a specific bug or subtle case | PBT added; example kept or folded in as `@example(...)` |
| **Leave alone** | No property, or the only property would be a mirror | Nothing changes |

### Using it with `/hypothesis`

The two are complementary and neither depends on the other:

|  | Input | Question it answers |
|---|---|---|
| `pbt-migrate` | your **tests** | which of these were already trying to be properties? |
| `/hypothesis` | your **source** | what properties does this code have that nobody tested? |

Install the companion with:

```bash
claude plugin marketplace add dgutson/hypothesis-command
claude plugin install hypothesis-command
```

(or copy [upstream's file](https://github.com/HypothesisWorks/hypothesis/blob/master/.claude/commands/hypothesis.md)
to `~/.claude/commands/hypothesis.md`).

**Run migration first, greenfield second.** The order matters:

```
1.  /pbt-migrate tests/test_parser.py
       → triage. Say it converts one family of 5 tests into 2 properties,
         and leaves 14 tests alone.

2.  Read the "left alone" list.
       → this tells you which behaviours the team believes in but which have
         no property. It is the best available map of where the gaps are.

3.  /hypothesis src/parser.py
       → now find properties in the implementation that no test covers.

4.  Any bug either one confirms gets pinned with @example(...) on the
    property test, so the shrunk input is retried on every run.
```

Migrating first pays off twice. The existing examples encode what the team
already considers important, so you learn the intended contract before asking a
tool to invent properties from the implementation alone. And it stops
`/hypothesis` from writing a property that duplicates one you were about to
derive from the existing tests.

Going the other way round — greenfield first — you end up reconciling two sets
of overlapping property tests by hand.

## Cost

What your colleagues actually install is small. The Hypothesis *library* repo is
~45 MB, but nobody needs that to use either tool — that is a full library
checkout with source, tests, docs and CI, and it is only relevant if you are
contributing to Hypothesis itself.

| | On disk | In context until invoked |
|---|---|---|
| `pbt-migrate` | ~460 KB (52 KB of content) | ~160 tokens (the skill description) |
| `/hypothesis` | ~308 KB (7.6 KB of content) | ~14 tokens (the command description) |

Bodies are loaded only when the tool is actually used (~2,300 and ~1,900 tokens
respectively), and `references/evolving-tests.md` loads only when a specific
candidate is being triaged.

## What's in it

```
skills/pbt-migrate/
├── SKILL.md                        the workflow and the two blocking gates
├── references/evolving-tests.md    seven unit-test shapes → the property in each
└── scripts/survey_suite.py         AST survey of a suite (stdlib only)
```

## Status

Early. The signal heuristics were calibrated against three suites, including
Hypothesis's own `tests/cover` (1731 tests, 104 files), and land at 28–40% of
non-property tests flagged as strong candidates. Two heuristics were rewritten
during that calibration: assertion families are fingerprinted structurally
rather than by text (or every `self.assertEqual` in a suite collapses into one
bogus family), and `robustness` only fires when three or more error-shaped tests
pile up on one target (a lone `pytest.raises` is a good unit test and should be
left alone).

The verdict taxonomy has not yet been exercised on a full real migration.

## License

MIT

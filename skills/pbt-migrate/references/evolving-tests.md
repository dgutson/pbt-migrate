# Evolving Unit Tests into Property-Based Tests

This guide helps you recognize what property a unit test is hiding and
translate it into a Hypothesis test.

## Recognizing Properties in Unit Tests

### Multiple tests, same assertion shape

```python
# Before: three tests that all do the same thing with different inputs
def test_parse_1():
    assert parse("1") == 1

def test_parse_42():
    assert parse("42") == 42

def test_parse_neg():
    assert parse("-7") == -7
```

These are all instances of a **roundtrip** property: `parse(format(n)) == n`.

```python
# After
from hypothesis import given, strategies as st

@given(st.integers())
def test_parse_roundtrip(n):
    assert parse(format(n)) == n
```

### Loop over hardcoded examples

```python
# Before
@pytest.mark.parametrize("input,expected", [("a", "A"), ("hello", "HELLO"), ("ABC", "ABC")])
def test_to_upper(input, expected):
    assert to_upper(input) == expected
```

The loop body is the property — but using `expected` as the oracle won't
generalize. Look for a structural property instead: the output should equal
the input when compared case-insensitively, and every character in the
output should be uppercase.

```python
# After
from hypothesis import given, strategies as st

@given(st.text())
def test_to_upper_case_insensitive_equal(s):
    assert to_upper(s).lower() == s.lower()
```

### A single test asserting one exact transformation

This is the case most likely to produce a worthless PBT, because
parametrizing it is so easy and so tempting.

```python
# Before — one concrete case, honest and useful
def test_strips_trailing_slash_from_url():
    assert Endpoint("http://x/api/").url == "http://x/api"
```

The obvious move is to replace the literals with a strategy and keep "the
same" oracle:

```python
# WRONG — the oracle is now the implementation, retyped
@given(st.text(min_size=1))
def test_all_trailing_slashes_stripped(url):
    assert Endpoint(url).url == url.rstrip('/')
```

Two things broke at once. The oracle `url.rstrip('/')` is the function body,
so the test cannot fail for any bug present in both places. And `st.text()`
essentially never generates a trailing `/`, so almost every example checks the
no-op path.

The property hiding in the unit test is not "the result equals
`rstrip('/')`" — it's **the result has no trailing slash, and nothing except
trailing slashes was removed**:

```python
# After — construct the interesting case; describe the output
@given(st.text(), st.integers(min_value=0, max_value=5))
def test_trailing_slashes_stripped(base, n):
    url = base + "/" * n
    result = Endpoint(url).url
    assert not result.endswith("/")
    assert url == result + "/" * (len(url) - len(result))
```

The general recipe when a unit test asserts one exact transformation: split
the equality into a **postcondition** (what should no longer hold) and a
**conservation law** (what must be preserved), then make sure the strategy
actually produces inputs where the transformation does something.

### Tests asserting "no error" on various inputs

```python
# Before
def test_parse_empty():
    parse("")  # just shouldn't raise

def test_parse_garbage():
    try:
        parse("xyz")
    except ParseError:
        pass  # either result is fine, just don't crash unexpectedly

def test_parse_unicode():
    try:
        parse("café")
    except ParseError:
        pass
```

This is a **robustness** property: the function should handle any input
without raising anything other than its documented error type.

```python
# After
from hypothesis import given, strategies as st

@given(st.text())
def test_parse_never_crashes_unexpectedly(s):
    try:
        parse(s)
    except ParseError:
        pass  # documented failure mode
```

### Setup, operations, check final state

```python
# Before
def test_stack_operations():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2
    assert s.pop() == 1
    assert s.is_empty()
```

This is a **stateful model test** candidate. The test encodes a specific
operation sequence — generalize it by drawing random operations. See the
Stateful Testing section in `reference.md`.

```python
# After
from hypothesis.stateful import RuleBasedStateMachine, rule, precondition, invariant
from hypothesis import strategies as st

class StackModel(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.subject = Stack()
        self.model = []

    @rule(value=st.integers())
    def push(self, value):
        self.subject.push(value)
        self.model.append(value)

    @precondition(lambda self: self.model)
    @rule()
    def pop(self):
        assert self.subject.pop() == self.model.pop()

    @invariant()
    def empty_agrees(self):
        assert self.subject.is_empty() == (len(self.model) == 0)

TestStack = StackModel.TestCase
```

### Tests with manually seeded RNGs

```python
# Before
def test_sample_distribution():
    rng = random.Random(42)
    result = sample(weights, rng)
    assert result in valid_range
```

Replace the seeded RNG with `st.randoms()`. The fixed seed gives
reproducibility but prevents exploration — Hypothesis gives you both
exploration and a shrinkable counterexample when it fails.

```python
# After
from hypothesis import given, strategies as st

@given(st.randoms(), st.lists(st.floats(min_value=0, exclude_min=True), min_size=1))
def test_sample_in_range(rng, weights):
    result = sample(weights, rng)
    assert 0 <= result < len(weights)
```

### Multiple tests asserting the same invariant

```python
# Before
def test_sort_empty():
    assert my_sort([]) == []

def test_sort_single():
    assert my_sort([5]) == [5]

def test_sort_reversed():
    assert my_sort([3, 2, 1]) == [1, 2, 3]

def test_sort_sorted():
    assert my_sort([1, 2, 3]) == [1, 2, 3]
```

Every test checks that the output is sorted and is a permutation of the
input. Those are the properties.

```python
# After: two separate properties
from hypothesis import given, strategies as st

@given(st.lists(st.integers()))
def test_sort_is_sorted(xs):
    result = my_sort(xs)
    assert all(a <= b for a, b in zip(result, result[1:]))

@given(st.lists(st.integers()))
def test_sort_same_elements(xs):
    assert sorted(my_sort(xs)) == sorted(xs)
```

## What to Do with the Old Tests

**Usually the PBT subsumes them.** If your PBT covers the full input space,
the specific examples in the unit tests are redundant — Hypothesis will
explore those cases and many more.

**Keep edge-case tests that serve as documentation.** If a unit test encodes
a subtle edge case that was discovered through a bug report, it may be worth
keeping as documentation even if the PBT covers it. The unit test communicates
"this specific case matters" in a way a PBT doesn't. Alternatively, pin it
with `@example(...)` on the property test itself (see `reference.md`) so it's
attached to the property rather than living as a separate test.

**Replace inline, don't create a new file.** Add Hypothesis tests in the same
file where the unit tests live. Either replace the unit tests or add the PBTs
alongside them.

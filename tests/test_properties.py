from hypothesis import given, settings
from hypothesis import strategies as st

from jzpack import compress, decompress

json_scalar = st.none() | st.booleans() | st.integers(-10_000, 10_000) | st.text(max_size=20)
json_value = st.recursive(
    json_scalar,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=25,
)
records = st.lists(st.dictionaries(st.text(max_size=8), json_value, max_size=6), max_size=30)


@settings(max_examples=100, deadline=None)
@given(records)
def test_generated_records_round_trip(data):
    assert decompress(compress(data)) == data

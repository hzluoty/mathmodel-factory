import math
import random

from scripts.verify_deliverables import IndexedTruthValues, value_matches


def test_index_matches_legacy_random_and_boundary_oracle():
    rng = random.Random(20260911)
    truth = [rng.uniform(-2000, 2000) for _ in range(300)] + [0, 1e-9, -1e-9, 1e8, -1e8, 25., 25.]
    indexed = IndexedTruthValues(truth)
    queries = [rng.uniform(-2500, 2500) for _ in range(500)]
    queries += [float(round(x, digits)) for x in truth for digits in (0,1,4)]
    queries += [x+sign*abs(x)*.005*factor for x in truth for sign in (-1,1) for factor in (.99999999,1,1.00000001)]
    assert [value_matches(x, indexed) for x in queries] == [value_matches(x, truth) for x in queries]


def test_empty_singleton_and_nonfinite_keep_legacy_result():
    for truth in ([], [0.], [-1.], [1.], [float('inf')], [float('-inf')], [float('nan')], [0.,float('nan'),float('inf')]):
        indexed = IndexedTruthValues(truth)
        for x in (-10., -1., 0., 1e-7, 1., 10., float('inf'), float('-inf'), float('nan')):
            assert value_matches(x, indexed) == value_matches(x, truth)


def test_unmatched_value_is_not_rounded_into_an_arbitrary_source():
    values = IndexedTruthValues([25., 50., 100.])
    assert value_matches(25.12, values)
    assert not value_matches(25.13, values)
    assert not value_matches(-25.12, values)

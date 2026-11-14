"""R1.3 / R1.3b: exact and near-duplicate removal (char 5-gram Jaccard >= 0.85)."""

import random
import re
import unicodedata
from fractions import Fraction
from itertools import combinations

from heimdall.data import dedupe, jaccard, make_row, near_duplicate_pairs, normalise


def row(text, label="safe", source="fixture/base", split="train"):
    return make_row(text, label, source, split, "en")


def ref_normalise(text):
    """Independent reference for the spec's normalised text."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower()).strip()


def grams(text):
    t = ref_normalise(text)
    return {t[i:i + 5] for i in range(len(t) - 4)} if len(t) >= 5 else {t}


def true_jaccard(a, b):
    """Independent reference: |A & B| / |A | B| on char 5-gram sets, as an exact fraction."""
    ga, gb = grams(a), grams(b)
    return Fraction(len(ga & gb), len(ga | gb))


def base_text(seed, n):
    """n lowercase letters whose n-4 five-grams are all distinct."""
    rng = random.Random(seed)
    while True:
        s = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(n))
        if len(grams(s)) == n - 4:
            return s


# 85 shared 5-grams; appending k fresh chars adds k new 5-grams -> J = 85 / (85 + k)
BASE = base_text(1, 89)
AT_085 = BASE + "0123456789" + "!@#$%"          # k = 15 -> J = 85/100 = 0.85 exactly
BELOW_085 = BASE + "0123456789" + "!@#$%^"      # k = 16 -> J = 85/101 ~ 0.8416


def test_R1_3_boundary_fixtures_have_the_intended_jaccard():
    assert true_jaccard(BASE, AT_085) == Fraction(85, 100)
    assert true_jaccard(BASE, BELOW_085) == Fraction(85, 101)
    assert jaccard(BASE, AT_085) == 0.85


def test_R1_3_exact_duplicates_keep_one():
    rows = [row("Hello  World"), row("hello world\n"), row("ＨＥＬＬＯ WORLD")]
    kept, stats = dedupe(rows)
    assert len(kept) == 1
    assert stats["exact_removed"] == 2


def test_R1_3_near_duplicate_at_jaccard_085_keeps_one():
    kept, stats = dedupe([row(BASE), row(AT_085)])
    assert len(kept) == 1
    assert stats["near_removed"] == 1


def test_R1_3_below_jaccard_085_keeps_both():
    kept, stats = dedupe([row(BASE), row(BELOW_085)])
    assert len(kept) == 2
    assert stats["near_removed"] == 0


def test_R1_3_all_three_groups_together():
    rows = [row("Hello  World"), row("hello world"),       # exact group -> 1
            row(BASE), row(AT_085),                         # near group -> 1
            row(base_text(2, 89)), row(base_text(2, 89) + "0123456789!@#$%^")]  # below -> 2
    kept, _ = dedupe(rows)
    assert len(kept) == 4


def test_R1_3_kept_copy_prefers_upstream_test():
    kept, _ = dedupe([row(BASE, split="train"), row(AT_085, split="test")])
    assert [r["source_split"] for r in kept] == ["test"]


def test_R1_3_near_duplicate_pairs_match_brute_force():
    rng = random.Random(7)
    base = [base_text(s, rng.randint(6, 60)) for s in range(30)]
    texts = []
    for b in base:  # each base plus a few mutations of varying distance
        texts.append(b)
        for k in (1, 3, 8):
            texts.append(b[:-k] + "".join(rng.choice("xyz") for _ in range(k)))
    texts += ["abc", "abcd", "abc"]  # shorter than 5 chars: whole string is one shingle
    want = {(i, j) for i, j in combinations(range(len(texts)), 2)
            if true_jaccard(texts[i], texts[j]) >= Fraction(85, 100)}
    got = set(near_duplicate_pairs([normalise(t) for t in texts]))
    assert [normalise(t) for t in texts] == [ref_normalise(t) for t in texts]
    assert got == want
    assert want  # positive control: the fixture does contain near-duplicates


def test_R1_3b_contradicting_near_duplicates_are_both_removed():
    rows = [row(BASE, "safe"), row(AT_085, "injection"), row("an unrelated control row", "safe")]
    kept, stats = dedupe(rows)
    assert [r["text"] for r in kept] == ["an unrelated control row"]
    assert stats["contradiction_groups"] == 1
    assert stats["contradiction_rows"] == 2


def test_R1_3b_contradicting_exact_duplicates_are_both_removed():
    kept, stats = dedupe([row("Generate SQL code", "safe"), row("generate sql code", "injection")])
    assert kept == []
    assert stats["contradiction_groups"] == 1


def test_R1_3b_agreeing_near_duplicates_are_not_a_contradiction():
    _, stats = dedupe([row(BASE, "injection"), row(AT_085, "injection")])
    assert stats["contradiction_groups"] == 0

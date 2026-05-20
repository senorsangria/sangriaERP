"""
Tests for apps/imports/matching.py — smart item-code matching module.
"""
from django.test import TestCase

from apps.catalog.models import Brand, Item
from apps.core.models import Company
from apps.distribution.models import Distributor
from apps.imports.matching import batch_find_best_matches, build_candidate_list
from apps.imports.models import ItemMapping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name='Test Co'):
    return Company.objects.create(name=name)


def _make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def _make_brand(company, name='Brand'):
    return Brand.objects.create(company=company, name=name)


def _make_item(brand, name='Item', item_code='CODE001'):
    return Item.objects.create(brand=brand, name=name, item_code=item_code)


def _make_mapping(company, distributor, raw_code, item, status=ItemMapping.Status.MAPPED):
    return ItemMapping.objects.create(
        company=company,
        distributor=distributor,
        raw_item_name=raw_code,
        mapped_item=item,
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class Priority1ExistingMappingAtOtherDistributorTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist_a = _make_distributor(self.company, 'Dist A')
        self.dist_b = _make_distributor(self.company, 'Dist B')
        self.item = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        _make_mapping(self.company, self.dist_a, 'Red0750', self.item)

    def test_priority_1_returns_high_confidence(self):
        """When a MAPPED ItemMapping exists at another distributor, return it at high confidence."""
        result = batch_find_best_matches(self.company, self.dist_b, ['Red0750'])
        match = result['Red0750']
        self.assertIsNotNone(match)
        self.assertEqual(match['item'], self.item)
        self.assertEqual(match['confidence'], 'high')
        self.assertIn('Dist A', match['reason'])

    def test_priority_1_excludes_current_distributor(self):
        """A mapping at the current distributor is not a Priority 1 hit."""
        # Use a raw code that has no item_code match so Priority 2 won't fire either
        item2 = _make_item(self.brand, 'White 750ml', 'Wht0750')
        _make_mapping(self.company, self.dist_b, 'RAW_ONLY_AT_B', item2)
        result = batch_find_best_matches(self.company, self.dist_b, ['RAW_ONLY_AT_B'])
        # The mapping exists at dist_b itself — not counted as Priority 1 (other-dist)
        # And no exact item_code match for 'RAW_ONLY_AT_B'
        self.assertIsNone(result['RAW_ONLY_AT_B'])

    def test_priority_1_short_circuits_priority_2(self):
        """Priority 1 hit stops evaluation; Priority 2 (exact item code) is not evaluated."""
        # item_code also matches, but Priority 1 should win
        self.item.item_code = 'Red0750'
        self.item.save()
        result = batch_find_best_matches(self.company, self.dist_b, ['Red0750'])
        match = result['Red0750']
        self.assertEqual(match['confidence'], 'high')  # Priority 1, not medium


class Priority2ExactItemCodeTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company, 'Dist A')
        self.item = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')

    def test_priority_2_exact_item_code_returns_medium(self):
        result = batch_find_best_matches(self.company, self.dist, ['Red0750'])
        match = result['Red0750']
        self.assertIsNotNone(match)
        self.assertEqual(match['item'], self.item)
        self.assertEqual(match['confidence'], 'medium')
        self.assertEqual(match['reason'], 'Exact item code match')

    def test_priority_2_case_insensitive(self):
        """'red0750' should match item_code 'Red0750'."""
        result = batch_find_best_matches(self.company, self.dist, ['red0750'])
        match = result['red0750']
        self.assertIsNotNone(match)
        self.assertEqual(match['item'], self.item)
        self.assertEqual(match['confidence'], 'medium')

    def test_priority_2_uppercase_code_matches_lowercase_item(self):
        item2 = _make_item(self.brand, 'White 750ml', 'wht0750')
        result = batch_find_best_matches(self.company, self.dist, ['WHT0750'])
        match = result['WHT0750']
        self.assertIsNotNone(match)
        self.assertEqual(match['item'], item2)

    def test_priority_2_multiple_brands_same_code_returns_one(self):
        """If two brands share an item_code, the first (arbitrary) is returned."""
        brand2 = _make_brand(self.company, 'Brand B')
        _make_item(brand2, 'Another Red', 'Red0750')
        result = batch_find_best_matches(self.company, self.dist, ['Red0750'])
        match = result['Red0750']
        self.assertIsNotNone(match)
        self.assertEqual(match['confidence'], 'medium')


class NoMatchTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company, 'Dist A')

    def test_no_match_returns_none(self):
        result = batch_find_best_matches(self.company, self.dist, ['XYZ999'])
        self.assertIsNone(result['XYZ999'])

    def test_empty_input_returns_empty_dict(self):
        result = batch_find_best_matches(self.company, self.dist, [])
        self.assertEqual(result, {})


class ScopingTest(TestCase):

    def setUp(self):
        self.company_a = _make_company('Company A')
        self.company_b = _make_company('Company B')
        self.brand_a = _make_brand(self.company_a, 'Brand A')
        self.brand_b = _make_brand(self.company_b, 'Brand B')
        self.dist_a = _make_distributor(self.company_a, 'Dist A')
        self.dist_b = _make_distributor(self.company_b, 'Dist B')

    def test_scoped_to_company_item_codes(self):
        """Items from another company must not appear in results."""
        _make_item(self.brand_b, 'Red 750ml', 'Red0750')  # company_b
        result = batch_find_best_matches(self.company_a, self.dist_a, ['Red0750'])
        self.assertIsNone(result['Red0750'])

    def test_scoped_to_company_existing_mappings(self):
        """Priority 1 mappings from another company must not appear."""
        item_b = _make_item(self.brand_b, 'Red 750ml', 'Red0750')
        dist_b2 = _make_distributor(self.company_b, 'Dist B2')
        _make_mapping(self.company_b, self.dist_b, 'Red0750', item_b)
        result = batch_find_best_matches(self.company_a, self.dist_a, ['Red0750'])
        self.assertIsNone(result['Red0750'])


class BatchPerformanceTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company)
        for i in range(5):
            _make_item(self.brand, f'Item {i}', f'CODE{i:03d}')

    def test_batch_uses_two_queries(self):
        """batch_find_best_matches must execute exactly 2 DB queries for any batch size."""
        codes = [f'RAW{i:04d}' for i in range(50)]
        with self.assertNumQueries(2):
            batch_find_best_matches(self.company, self.dist, codes)


class IgnoredMappingTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist_a = _make_distributor(self.company, 'Dist A')
        self.dist_b = _make_distributor(self.company, 'Dist B')
        self.item = _make_item(self.brand, 'Red 750ml', 'Red0750')

    def test_ignored_mapping_at_other_distributor_is_not_suggested(self):
        """An IGNORED mapping at another distributor should not surface as Priority 1."""
        _make_mapping(self.company, self.dist_a, 'Red0750', self.item, status=ItemMapping.Status.IGNORED)
        result = batch_find_best_matches(self.company, self.dist_b, ['Red0750'])
        match = result['Red0750']
        # Should fall through to Priority 2 (exact item code) not Priority 1
        if match:
            self.assertNotEqual(match['confidence'], 'high')


class BuildCandidateListTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company)
        self.item_red_750 = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        self.item_red_1500 = _make_item(self.brand, 'Classic Red 1.5L', 'Red1500')
        self.item_white = _make_item(self.brand, 'Classic White 750ml', 'Wht0750')
        self.all_items = list(Item.objects.filter(brand__company=self.company))

    def test_priority_3_substring_match_in_candidates(self):
        """'Red' is a substring of 'Red0750' and 'Red1500'."""
        candidates = build_candidate_list('Red', self.all_items, {})
        codes = [c['item'].item_code for c in candidates]
        self.assertIn('Red0750', codes)
        self.assertIn('Red1500', codes)
        for c in candidates:
            self.assertEqual(c['confidence'], 'low')

    def test_priority_4_name_word_match_in_candidates(self):
        """'Classic-White' tokenises to ['Classic','White'] and matches the item name."""
        # Use a raw code with a separator so tokenisation produces two tokens
        candidates = build_candidate_list('Classic-White', self.all_items, {})
        codes = [c['item'].item_code for c in candidates]
        self.assertIn('Wht0750', codes)

    def test_exact_match_excluded_from_candidates(self):
        """Exact item_code matches (Priority 2) are not duplicated in candidates."""
        candidates = build_candidate_list('Red0750', self.all_items, {})
        codes = [c['item'].item_code for c in candidates]
        # Red0750 is an exact match (Priority 2) so should NOT be in candidate list
        self.assertNotIn('Red0750', codes)

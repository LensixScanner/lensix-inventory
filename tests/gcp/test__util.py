"""Unit tests for _util.py — small helpers shared by every GCP gather
module. Previously untested (no gather() logic lived here worth its own
file); extract_subnet_region added real branching worth covering."""

import lensix_inventory.gcp._util as m


class TestExtractResourceName:
    def test_full_selflink(self):
        assert m.extract_resource_name('https://compute.../targetHttpsProxies/proxy-1') == 'proxy-1'

    def test_bare_name(self):
        assert m.extract_resource_name('my-ssl-policy') == 'my-ssl-policy'

    def test_none_for_none(self):
        assert m.extract_resource_name(None) is None


class TestExtractNetworkName:
    def test_full_selflink(self):
        assert m.extract_network_name('https://compute.../networks/default') == 'default'

    def test_partial_url(self):
        assert m.extract_network_name('projects/p/global/networks/default') == 'default'

    def test_bare_name(self):
        assert m.extract_network_name('default') == 'default'

    def test_none_for_none(self):
        assert m.extract_network_name(None) is None

    def test_none_for_non_string(self):
        assert m.extract_network_name(123) is None

    def test_none_for_empty_string(self):
        assert m.extract_network_name('') is None


class TestNormalizeLocationToRegion:
    def test_strips_the_zone_letter_suffix(self):
        assert m.normalize_location_to_region('us-central1-a') == 'us-central1'

    def test_leaves_a_bare_region_unchanged(self):
        assert m.normalize_location_to_region('us-central1') == 'us-central1'

    def test_handles_a_multi_word_region_name(self):
        assert m.normalize_location_to_region('asia-southeast1-b') == 'asia-southeast1'

    def test_leaves_global_unchanged(self):
        assert m.normalize_location_to_region('global') == 'global'

    def test_none_passes_through(self):
        assert m.normalize_location_to_region(None) is None

    def test_empty_string_passes_through(self):
        assert m.normalize_location_to_region('') == ''


class TestExtractSubnetRegion:
    def test_full_selflink(self):
        assert m.extract_subnet_region('https://compute.../projects/p/regions/us-central1/subnetworks/sub1') == 'us-central1'

    def test_shortest_documented_partial_form(self):
        assert m.extract_subnet_region('regions/us-central1/subnetworks/sub1') == 'us-central1'

    def test_none_for_a_bare_name_with_no_region_segment(self):
        assert m.extract_subnet_region('sub1') is None

    def test_none_for_none(self):
        assert m.extract_subnet_region(None) is None

    def test_none_for_non_string(self):
        assert m.extract_subnet_region(123) is None

    def test_none_for_empty_string(self):
        assert m.extract_subnet_region('') is None

    def test_none_when_regions_segment_is_the_last_segment(self):
        # Defensive: a malformed/truncated reference with nothing after
        # 'regions' shouldn't raise an IndexError.
        assert m.extract_subnet_region('projects/p/regions') is None

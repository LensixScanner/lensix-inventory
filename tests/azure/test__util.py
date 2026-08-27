"""Unit tests for lensix_inventory.azure._util — shared helpers used by
every Azure gather module."""

import lensix_inventory.azure._util as m


class TestResourceGroup:
    def test_extracts_the_resource_group_from_an_arm_id(self):
        rid = '/subscriptions/sub-1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1'
        assert m.resource_group(rid) == 'my-rg'

    def test_case_insensitive_match(self):
        rid = '/subscriptions/sub-1/RESOURCEGROUPS/my-rg/providers/x'
        assert m.resource_group(rid) == 'my-rg'

    def test_returns_none_for_a_falsy_id(self):
        assert m.resource_group(None) is None
        assert m.resource_group('') is None

    def test_returns_none_when_no_resource_group_segment_is_present(self):
        assert m.resource_group('/subscriptions/sub-1/providers/Microsoft.Compute') is None


class TestAsDict:
    def test_returns_none_unchanged(self):
        assert m.as_dict(None) is None

    def test_returns_a_dict_unchanged(self):
        assert m.as_dict({'id': '1'}) == {'id': '1'}

    def test_calls_as_dict_on_an_sdk_model(self):
        class _Model:
            def as_dict(self):
                return {'id': '1', 'name': 'x'}
        assert m.as_dict(_Model()) == {'id': '1', 'name': 'x'}

    def test_falls_back_to_a_minimal_dict_when_as_dict_fails(self):
        class _Broken:
            id = 'i1'
            name = 'n1'

            def as_dict(self):
                raise RuntimeError('boom')
        assert m.as_dict(_Broken()) == {'id': 'i1', 'name': 'n1'}

    def test_fallback_handles_missing_id_and_name_attributes(self):
        class _Empty:
            def as_dict(self):
                raise RuntimeError('boom')
        assert m.as_dict(_Empty()) == {'id': None, 'name': None}

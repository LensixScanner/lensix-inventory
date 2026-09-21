"""Unit tests for lensix_inventory.aws.route53 — registered domains and public hosted zones."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.route53 as m


def _client(domain_pages=None, detail_by_domain=None, detail_error_domains=None,
            zone_pages=None, txt_by_zone=None, domains_raise=False, zones_raise=False,
            domain_tags=None, zone_tags=None, private_zone_pages=None, vpcs_by_zone=None,
            vpc_error_zones=None):
    client = MagicMock()
    domain_tags = domain_tags or {}
    client.list_tags_for_domain.side_effect = lambda DomainName: {'TagList': domain_tags.get(DomainName, [])}
    zone_tags = zone_tags or {}
    client.list_tags_for_resource.side_effect = lambda ResourceType, ResourceId: {
        'ResourceTagSet': {'ResourceId': ResourceId, 'ResourceType': ResourceType, 'Tags': zone_tags.get(ResourceId, [])}
    }
    if domains_raise:
        client.list_domains.side_effect = RuntimeError('boom')
    else:
        client.list_domains.side_effect = domain_pages or [{'Domains': []}]
    detail_by_domain = detail_by_domain or {}
    detail_error_domains = detail_error_domains or set()

    def _detail(DomainName):
        if DomainName in detail_error_domains:
            raise RuntimeError('boom')
        return detail_by_domain[DomainName]
    client.get_domain_detail.side_effect = _detail

    if zones_raise:
        client.list_hosted_zones.side_effect = RuntimeError('boom')
    else:
        # get_public_zones() and get_private_zones() BOTH call
        # list_hosted_zones() within one gather() -- a plain list-as-
        # side_effect is consumed once across the mock's whole lifetime,
        # so the public pages and private pages have to be concatenated
        # here (public zones are always listed first in gather()) rather
        # than each fetch getting its own independent sequence.
        public_pages = zone_pages or [{'HostedZones': []}]
        private_pages = private_zone_pages or [{'HostedZones': []}]
        client.list_hosted_zones.side_effect = list(public_pages) + list(private_pages)

    txt_by_zone = txt_by_zone or {}

    def _txt(HostedZoneId, StartRecordName, StartRecordType, MaxItems):
        return {'ResourceRecordSets': txt_by_zone.get(HostedZoneId, [])}
    client.list_resource_record_sets.side_effect = _txt

    vpcs_by_zone = vpcs_by_zone or {}
    vpc_error_zones = vpc_error_zones or set()

    def _get_hosted_zone(Id):
        if Id in vpc_error_zones:
            raise RuntimeError('boom')
        return {'VPCs': vpcs_by_zone.get(Id, [])}
    client.get_hosted_zone.side_effect = _get_hosted_zone
    return client


class TestGetDomains:
    def test_paginates_via_marker(self):
        client = _client(domain_pages=[
            {'Domains': [{'DomainName': 'a.com'}], 'NextPageMarker': 'tok'},
            {'Domains': [{'DomainName': 'b.com'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_domains() == ['a.com', 'b.com']


class TestGetPublicZones:
    def test_excludes_private_zones(self):
        client = _client(zone_pages=[{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'public.com.', 'Config': {'PrivateZone': False}},
            {'Id': '/hostedzone/Z2', 'Name': 'private.com.', 'Config': {'PrivateZone': True}},
        ]}])
        with patch.object(m.boto3, 'client', return_value=client):
            zones = m.get_public_zones()
        assert zones == [('/hostedzone/Z1', 'public.com.')]

    def test_paginates_via_marker(self):
        client = _client(zone_pages=[
            {'HostedZones': [{'Id': '/hostedzone/Z1', 'Name': 'a.com.', 'Config': {'PrivateZone': False}}],
             'IsTruncated': True, 'NextMarker': 'tok'},
            {'HostedZones': [{'Id': '/hostedzone/Z2', 'Name': 'b.com.', 'Config': {'PrivateZone': False}}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            zones = m.get_public_zones()
        assert [z[0] for z in zones] == ['/hostedzone/Z1', '/hostedzone/Z2']


class TestGetPrivateZones:
    def test_excludes_public_zones(self):
        client = _client(zone_pages=[{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'public.com.', 'Config': {'PrivateZone': False}},
            {'Id': '/hostedzone/Z2', 'Name': 'private.com.', 'Config': {'PrivateZone': True}},
        ]}])
        with patch.object(m.boto3, 'client', return_value=client):
            zones = m.get_private_zones()
        assert zones == [('/hostedzone/Z2', 'private.com.')]

    def test_paginates_via_marker(self):
        client = _client(zone_pages=[
            {'HostedZones': [{'Id': '/hostedzone/Z1', 'Name': 'a.com.', 'Config': {'PrivateZone': True}}],
             'IsTruncated': True, 'NextMarker': 'tok'},
            {'HostedZones': [{'Id': '/hostedzone/Z2', 'Name': 'b.com.', 'Config': {'PrivateZone': True}}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            zones = m.get_private_zones()
        assert [z[0] for z in zones] == ['/hostedzone/Z1', '/hostedzone/Z2']


class TestGetHostedZoneVpcs:
    def test_returns_the_vpcs_field(self):
        client = MagicMock()
        client.get_hosted_zone.return_value = {'VPCs': [{'VPCId': 'vpc-1', 'VPCRegion': 'us-east-1'}]}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_hosted_zone_vpcs('/hostedzone/Z1') == [{'VPCId': 'vpc-1', 'VPCRegion': 'us-east-1'}]

    def test_returns_empty_list_when_vpcs_is_absent(self):
        client = MagicMock()
        client.get_hosted_zone.return_value = {}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_hosted_zone_vpcs('/hostedzone/Z1') == []


class TestGetApexTxtRecords:
    def test_filters_to_txt_records_at_exactly_the_apex(self):
        client = _client(txt_by_zone={'/hostedzone/Z1': [
            {'Type': 'TXT', 'Name': 'example.com.'},
            {'Type': 'TXT', 'Name': 'sub.example.com.'},
            {'Type': 'MX', 'Name': 'example.com.'},
        ]})
        with patch.object(m.boto3, 'client', return_value=client):
            records = m.get_apex_txt_records('/hostedzone/Z1', 'example.com.')
        assert records == [{'Type': 'TXT', 'Name': 'example.com.'}]

    def test_returns_empty_list_on_failure(self):
        client = MagicMock()
        client.list_resource_record_sets.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_apex_txt_records('/hostedzone/Z1', 'example.com.') == []


class TestGather:
    def test_adds_one_resource_per_domain(self):
        w = MagicMock()
        client = _client(
            domain_pages=[{'Domains': [{'DomainName': 'example.com'}]}],
            detail_by_domain={'example.com': {'DomainName': 'example.com', 'Nameservers': []}},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['route53_domain'].kwargs['resource_id'] == 'example.com'

    def test_domain_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(
            domain_pages=[{'Domains': [{'DomainName': 'example.com'}]}],
            detail_by_domain={'example.com': {'DomainName': 'example.com'}},
            domain_tags={'example.com': tags},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['route53_domain'].kwargs['tags'] == tags

    def test_a_domain_detail_failure_does_not_abort_the_others(self):
        w = MagicMock()
        client = _client(
            domain_pages=[{'Domains': [{'DomainName': 'bad.com'}, {'DomainName': 'good.com'}]}],
            detail_by_domain={'good.com': {}},
            detail_error_domains={'bad.com'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53_domain:bad.com' for c in w.add_error.call_args_list)
        domain_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'route53_domain']
        assert len(domain_calls) == 1

    def test_a_domains_service_failure_does_not_prevent_zones_from_being_gathered(self):
        w = MagicMock()
        client = _client(
            domains_raise=True,
            zone_pages=[{'HostedZones': [{'Id': '/hostedzone/Z1', 'Name': 'example.com.', 'Config': {'PrivateZone': False}}]}],
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53 (domains)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'route53_zone' in calls

    def test_adds_one_resource_per_zone_with_the_stripped_id_and_clean_name(self):
        w = MagicMock()
        client = _client(zone_pages=[{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'example.com.', 'Config': {'PrivateZone': False}},
        ]}], txt_by_zone={'/hostedzone/Z1': [{'Type': 'TXT', 'Name': 'example.com.', 'ResourceRecords': [{'Value': '"v=spf1 -all"'}]}]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        zone_call = calls['route53_zone']
        assert zone_call.kwargs['resource_id'] == 'Z1'
        assert zone_call.kwargs['resource_name'] == 'example.com'
        assert len(zone_call.kwargs['raw']['_ApexTxtRecordSets']) == 1

    def test_zone_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(zone_pages=[{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'example.com.', 'Config': {'PrivateZone': False}},
        ]}], zone_tags={'Z1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['route53_zone'].kwargs['tags'] == tags

    def test_a_zones_service_failure_does_not_abort_the_whole_gather(self):
        w = MagicMock()
        client = _client(zones_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53 (hosted zones)' for c in w.add_error.call_args_list)

    def test_adds_one_resource_per_private_zone_with_vpcs_fused_in(self):
        w = MagicMock()
        client = _client(
            private_zone_pages=[{'HostedZones': [
                {'Id': '/hostedzone/Z2', 'Name': 'internal.example.com.', 'Config': {'PrivateZone': True}},
            ]}],
            vpcs_by_zone={'/hostedzone/Z2': [{'VPCId': 'vpc-1', 'VPCRegion': 'us-east-1'}]},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        pz_call = calls['route53_private_zone']
        assert pz_call.kwargs['resource_id'] == 'Z2'
        assert pz_call.kwargs['resource_name'] == 'internal.example.com'
        assert pz_call.kwargs['raw']['_VPCs'] == [{'VPCId': 'vpc-1', 'VPCRegion': 'us-east-1'}]

    def test_public_and_private_zones_are_gathered_as_distinct_resource_types(self):
        w = MagicMock()
        client = _client(
            zone_pages=[{'HostedZones': [{'Id': '/hostedzone/Z1', 'Name': 'public.com.', 'Config': {'PrivateZone': False}}]}],
            private_zone_pages=[{'HostedZones': [{'Id': '/hostedzone/Z2', 'Name': 'internal.com.', 'Config': {'PrivateZone': True}}]}],
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        types_by_id = {c.kwargs['resource_id']: c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert types_by_id['Z1'] == 'route53_zone'
        assert types_by_id['Z2'] == 'route53_private_zone'

    def test_private_zone_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(
            private_zone_pages=[{'HostedZones': [{'Id': '/hostedzone/Z2', 'Name': 'internal.com.', 'Config': {'PrivateZone': True}}]}],
            zone_tags={'Z2': tags},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['route53_private_zone'].kwargs['tags'] == tags

    def test_a_private_zone_produces_a_vpc_edge_per_associated_vpc(self):
        w = MagicMock()
        client = _client(
            private_zone_pages=[{'HostedZones': [
                {'Id': '/hostedzone/Z2', 'Name': 'internal.example.com.', 'Config': {'PrivateZone': True}},
            ]}],
            vpcs_by_zone={'/hostedzone/Z2': [{'VPCId': 'vpc-1'}, {'VPCId': 'vpc-2'}]},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        edges = [c.kwargs for c in w.add_edge.call_args_list]
        assert {'from_type': 'route53_private_zone', 'from_id': 'Z2', 'to_type': 'vpc', 'to_id': 'vpc-1', 'relationship': 'associated_with_vpc'} in edges
        assert {'from_type': 'route53_private_zone', 'from_id': 'Z2', 'to_type': 'vpc', 'to_id': 'vpc-2', 'relationship': 'associated_with_vpc'} in edges

    def test_a_private_zone_with_no_vpcs_produces_no_edges(self):
        w = MagicMock()
        client = _client(
            private_zone_pages=[{'HostedZones': [
                {'Id': '/hostedzone/Z2', 'Name': 'internal.example.com.', 'Config': {'PrivateZone': True}},
            ]}],
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_private_zone_produces_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        client = _client(
            private_zone_pages=[{'HostedZones': [
                {'Id': '/hostedzone/Z2', 'Name': 'internal.example.com.', 'Config': {'PrivateZone': True}},
            ]}],
            vpcs_by_zone={'/hostedzone/Z2': [{'VPCId': 'vpc-1'}]},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_a_get_vpcs_failure_for_one_private_zone_does_not_abort_the_others(self):
        w = MagicMock()
        client = _client(
            private_zone_pages=[{'HostedZones': [
                {'Id': '/hostedzone/Zbad', 'Name': 'bad.com.', 'Config': {'PrivateZone': True}},
                {'Id': '/hostedzone/Zgood', 'Name': 'good.com.', 'Config': {'PrivateZone': True}},
            ]}],
            vpcs_by_zone={'/hostedzone/Zgood': [{'VPCId': 'vpc-1'}]},
            vpc_error_zones={'/hostedzone/Zbad'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53_private_zone:Zbad' for c in w.add_error.call_args_list)
        pz_calls = {c.kwargs['resource_id']: c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'route53_private_zone'}
        assert pz_calls['Zbad'].kwargs['raw']['_VPCs'] == []
        assert pz_calls['Zgood'].kwargs['raw']['_VPCs'] == [{'VPCId': 'vpc-1'}]

    def test_a_private_zones_service_failure_does_not_abort_public_zones(self):
        w = MagicMock()
        client = _client(zone_pages=[{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'example.com.', 'Config': {'PrivateZone': False}},
        ]}])
        # Exhaust the concatenated side_effect list after the public page so
        # the private-zone call raises StopIteration, exercising gather()'s
        # own isolation around that fetch specifically.
        client.list_hosted_zones.side_effect = [{'HostedZones': [
            {'Id': '/hostedzone/Z1', 'Name': 'example.com.', 'Config': {'PrivateZone': False}},
        ]}]
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53 (private hosted zones)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'route53_zone' in calls

"""Unit tests for pubsub.py — topics and subscriptions.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.pubsub as m


def _topic(*, name='projects/p/topics/t1', labels=None):
    d = {'name': name}
    if labels is not None:
        d['labels'] = labels
    return d


def _sub(*, name='projects/p/subscriptions/s1', labels=None):
    d = {'name': name}
    if labels is not None:
        d['labels'] = labels
    return d


def _pubsub_client(topics=None, subs=None):
    pubsub = MagicMock()
    topics_req = MagicMock()
    topics_req.execute.return_value = {'topics': topics or []}
    pubsub.projects.return_value.topics.return_value.list.return_value = topics_req
    subs_req = MagicMock()
    subs_req.execute.return_value = {'subscriptions': subs or []}
    pubsub.projects.return_value.subscriptions.return_value.list.return_value = subs_req
    return pubsub


class TestGather:
    def test_adds_one_resource_per_topic_and_subscription(self):
        topic = _topic()
        sub = _sub()
        pubsub = _pubsub_client(topics=[topic], subs=[sub])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=pubsub):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 2
        topic_call, sub_call = writer.add_resource.call_args_list
        assert topic_call.kwargs['resource_type'] == 'pubsub_topic'
        assert topic_call.kwargs['tags'] is None
        assert sub_call.kwargs['resource_type'] == 'pubsub_subscription'
        assert sub_call.kwargs['tags'] is None

    def test_tags_are_passed_through_independently(self):
        topic = _topic(labels={'lensix-suppress': 'true'})
        sub = _sub(labels={'lensix-suppress-checks': 'pubsub_nodeadletter'})
        pubsub = _pubsub_client(topics=[topic], subs=[sub])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=pubsub):
            m.gather('p', MagicMock(), writer)
        topic_call, sub_call = writer.add_resource.call_args_list
        assert topic_call.kwargs['tags'] == {'lensix-suppress': 'true'}
        assert sub_call.kwargs['tags'] == {'lensix-suppress-checks': 'pubsub_nodeadletter'}

    def test_a_topics_list_failure_is_isolated_and_subscriptions_still_gathered(self):
        pubsub = MagicMock()
        pubsub.projects.return_value.topics.return_value.list.side_effect = RuntimeError('boom')
        subs_req = MagicMock()
        subs_req.execute.return_value = {'subscriptions': [_sub()]}
        pubsub.projects.return_value.subscriptions.return_value.list.return_value = subs_req
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=pubsub):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert resource_types == ['pubsub_subscription']

    def test_no_topics_or_subs_adds_nothing(self):
        pubsub = _pubsub_client()
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=pubsub):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()

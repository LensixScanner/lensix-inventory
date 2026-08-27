"""Unit tests for lensix_inventory.common.secrets — the local
secret-content scanner used for user-data/environment-variable/metadata
fields across all three providers. Confirms the contract every gather
module relies on: rule NAMES only, never the matched value."""

import pytest

from lensix_inventory.common.secrets import scan_text_for_secrets


class TestScanTextForSecrets:
    def test_empty_and_falsy_input_returns_no_hits(self):
        assert scan_text_for_secrets('') == []
        assert scan_text_for_secrets(None) == []

    def test_clean_text_returns_no_hits(self):
        assert scan_text_for_secrets('export STAGE=production\nexport DEBUG=false') == []

    @pytest.mark.parametrize('label,text', [
        ('AWS Secret Access Key', 'aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'),
        ('AWS Session Token', 'x-amz-security-token: FwoGZXIvYXdzEBAaDA1234567890abcdefEXAMPLE'),
        ('Azure Storage Account Key', 'AccountKey=' + 'A' * 86 + '=='),
        ('Azure SAS Token', 'sv=2023-01-01&ss=b&srt=sco&sp=r&se=2026-01-01&sig=' + 'a' * 24),
        ('Azure Client Secret', 'client_secret=Xy8Q~abcdefghijklmnopqrstuvwxyz123'),
        ('GCP API Key', 'AIza' + 'A' * 35),
        ('GCP Service Account Key', '"private_key_id": "' + 'a' * 40 + '"'),
        ('GitHub Token', 'ghp_' + 'a' * 36),
        ('GitHub Fine-Grained PAT', 'github_pat_' + 'a' * 22),
        ('GitLab Personal Access Token', 'glpat-' + 'a' * 20),
        ('Slack Token', 'xoxb-' + '1' * 10 + '-' + 'a' * 10),
        ('Slack Webhook URL', 'https://hooks.slack.com/services/T00000000/B00000000/' + 'a' * 24),
        ('Stripe Live API Key', 'sk_live_' + 'a' * 24),
        ('Twilio API Key', 'SK' + 'a' * 32),
        ('Private Key', '-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----'),
        ('JSON Web Token', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'),
        ('Database Connection String', 'postgres://user:hunter2@db.example.com:5432/prod'),
        ('Basic Auth Credentials in URL', 'https://alice:hunter2@internal.example.com/api'),
        ('Generic API Key/Secret Assignment', 'api_key="abcdefghijklmnop1234"'),
    ])
    def test_each_rule_fires_for_a_matching_sample(self, label, text):
        assert label in scan_text_for_secrets(text)

    def test_returns_only_the_rule_name_never_the_matched_value(self):
        text = 'aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
        hits = scan_text_for_secrets(text)
        assert hits == ['AWS Secret Access Key']
        assert not any('wJalrXUtnFEMI' in hit for hit in hits)

    def test_multiple_distinct_matches_all_reported(self):
        text = 'aws_secret_access_key=abc123\nsk_live_' + 'a' * 24
        hits = scan_text_for_secrets(text)
        assert set(hits) >= {'AWS Secret Access Key', 'Stripe Live API Key'}

    def test_duplicate_matches_of_the_same_rule_are_not_repeated(self):
        text = 'sk_live_' + 'a' * 24 + '\nsk_live_' + 'b' * 24
        hits = scan_text_for_secrets(text)
        assert hits.count('Stripe Live API Key') == 1

    def test_cloudformation_waitcondition_url_is_stripped_before_scanning(self):
        # These embed an X-Amz-Security-Token query param that would
        # otherwise match the AWS Session Token rule, but aren't a
        # meaningful secret — they're stripped before the scan runs.
        url = 'https://cloudformation-waitcondition-us-east-1.s3.amazonaws.com/arn%3Aaws%3Acloudformation/stack/abc?X-Amz-Security-Token=FwoGZXIvYXdzEBAaDA1234567890abcdefEXAMPLE'
        assert scan_text_for_secrets(url) == []

    def test_waitcondition_stripping_does_not_hide_a_real_secret_elsewhere_in_the_text(self):
        url = 'https://cloudformation-waitcondition-us-east-1.s3.amazonaws.com/arn?X-Amz-Security-Token=FwoGZXIvYXdzEBAaDA1234567890abcdefEXAMPLE'
        text = url + '\naws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
        assert scan_text_for_secrets(text) == ['AWS Secret Access Key']

    def test_generic_rule_does_not_fire_on_a_short_value(self):
        # The generic assignment rule requires at least 16 characters of
        # value to avoid flagging things like `token=abc`.
        assert scan_text_for_secrets('token=short') == []

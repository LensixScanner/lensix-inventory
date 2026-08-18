"""Azure Security Center (Microsoft Defender for Cloud) gathering —
subscription-level security contacts and auto-provisioning settings.

Only the data-fetching calls are included here (security_contacts.list,
auto_provisioning_settings.list) — missing-contact, missing-email/phone,
disabled-alert-notification, and auto-provisioning-disabled evaluation is
left server-side.

Requires: azure-mgmt-security.
"""


def get_security_contacts(credential, subscription_id):
    from azure.mgmt.security import SecurityCenter
    security_client = SecurityCenter(credential, subscription_id)
    return list(security_client.security_contacts.list())


def get_auto_provisioning_settings(credential, subscription_id):
    from azure.mgmt.security import SecurityCenter
    security_client = SecurityCenter(credential, subscription_id)
    return list(security_client.auto_provisioning_settings.list())


def gather(credential, subscription_id, writer):
    try:
        contacts = get_security_contacts(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='securitycenter:security_contacts', message=e)
    else:
        for contact in contacts:
            writer.add_resource(
                resource_type='security_contact',
                region='global',
                resource_id=contact.id,
                resource_name=contact.name,
                raw=contact.as_dict(),
            )

    try:
        settings = get_auto_provisioning_settings(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='securitycenter:auto_provisioning_settings', message=e)
    else:
        for setting in settings:
            writer.add_resource(
                resource_type='security_setting',
                region='global',
                resource_id=setting.id,
                resource_name=setting.name,
                raw=setting.as_dict(),
            )

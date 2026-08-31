"""Security group gathering.

Determining "is this security group referenced anywhere" (for an unused-SG
finding) means cross-referencing against ~21 other AWS services (ENIs,
Lambda, ECS, RDS, EKS, CloudFormation, ...) — most of them with no natural
resource type of their own in this tool, and several (Glue connections,
SageMaker, Step Functions, Service Catalog, Elastic Beanstalk, Directory
Service, App Runner, Batch) not gathered by any other module at all. Rather
than require every consumer of this data to separately gather all ~21 of
those services just to extract SG references from them, get_attached_sg_ids()
below does that fan-out itself and gather() stores the result as a single
synthetic `security_group_usage` resource (same convention as
account.py's `iam_password_policy`/`catalog_encryption` singletons) — the
checks layer then just reads that resource's raw field like any other
already-gathered data, no live AWS calls of its own.
"""

import re

import boto3

_SG_PATTERN = re.compile(r'sg-[0-9a-f]{8,17}')


def get_security_groups(region):
    ec2 = boto3.client('ec2', region_name=region)
    sgs = []
    for page in ec2.get_paginator('describe_security_groups').paginate():
        sgs.extend(page['SecurityGroups'])
    return sgs


def get_security_group_rules(region):
    ec2 = boto3.client('ec2', region_name=region)
    rules = []
    for page in ec2.get_paginator('describe_security_group_rules').paginate():
        rules.extend(page['SecurityGroupRules'])
    return rules


def get_attached_sg_ids(region, writer=None):
    """Collect all SG IDs referenced by ENIs and service configurations.

    Fans out across ~21 AWS services. Many of those services store SG
    references in their config without ever creating a persistent ENI —
    deleting a SG that one of these services references causes silent
    runtime failures, so config-level references are treated the same as
    ENI attachments. Every fetch here fails silently except Glue
    connections (recorded via `writer.add_error`, matching the historical
    behavior of the live check this replaced) — a permission gap or
    outage in any one of these ~21 unrelated services shouldn't abort the
    other 20."""
    used = set()

    # 1. Network interfaces (EC2, RDS, ELB, etc.)
    try:
        ec2 = boto3.client('ec2', region_name=region)
        for page in ec2.get_paginator('describe_network_interfaces').paginate():
            for eni in page['NetworkInterfaces']:
                for g in eni.get('Groups', []):
                    used.add(g['GroupId'])
    except Exception:
        pass

    # 2. EC2 Launch Templates — SGs referenced at launch time, no ENI until instance starts
    try:
        ec2 = boto3.client('ec2', region_name=region)
        for page in ec2.get_paginator('describe_launch_templates').paginate():
            for lt in page['LaunchTemplates']:
                try:
                    ver = ec2.describe_launch_template_versions(
                        LaunchTemplateId=lt['LaunchTemplateId'], Versions=['$Default']
                    )['LaunchTemplateVersions']
                    for v in ver:
                        for ni in v.get('LaunchTemplateData', {}).get('NetworkInterfaces', []):
                            for sg in ni.get('Groups', []):
                                used.add(sg)
                        for sg in v.get('LaunchTemplateData', {}).get('SecurityGroupIds', []):
                            used.add(sg)
                        for sg in v.get('LaunchTemplateData', {}).get('SecurityGroups', []):
                            used.add(sg)
                except Exception:
                    pass
    except Exception:
        pass

    # 2b. EC2 Launch Configurations (legacy Auto Scaling — predates Launch Templates)
    try:
        asg = boto3.client('autoscaling', region_name=region)
        for page in asg.get_paginator('describe_launch_configurations').paginate():
            for lc in page['LaunchConfigurations']:
                for sg in lc.get('SecurityGroups', []):
                    used.add(sg)
    except Exception:
        pass

    # 3. Lambda VPC configurations
    try:
        lam = boto3.client('lambda', region_name=region)
        for page in lam.get_paginator('list_functions').paginate():
            for fn in page['Functions']:
                for sg in fn.get('VpcConfig', {}).get('SecurityGroupIds', []):
                    used.add(sg)
    except Exception:
        pass

    # 4. ECS Services (awsvpc mode) — SGs referenced via service network config
    try:
        ecs = boto3.client('ecs', region_name=region)
        for page in ecs.get_paginator('list_clusters').paginate():
            for arn in page.get('clusterArns', []):
                try:
                    for svc_page in ecs.get_paginator('list_services').paginate(cluster=arn):
                        if svc_page.get('serviceArns'):
                            svcs = ecs.describe_services(cluster=arn, services=svc_page['serviceArns'])
                            for svc in svcs.get('services', []):
                                nc = svc.get('networkConfiguration', {}).get('awsvpcConfiguration', {})
                                for sg in nc.get('securityGroups', []):
                                    used.add(sg)
                except Exception:
                    pass
    except Exception:
        pass

    # 5. CodeBuild projects with VPC config
    try:
        cb = boto3.client('codebuild', region_name=region)
        projects = []
        for page in cb.get_paginator('list_projects').paginate():
            projects.extend(page.get('projects', []))
        if projects:
            for i in range(0, len(projects), 100):
                batch = cb.batch_get_projects(names=projects[i:i+100])
                for p in batch.get('projects', []):
                    for sg in p.get('vpcConfig', {}).get('securityGroupIds', []):
                        used.add(sg)
    except Exception:
        pass

    # 6. RDS / Aurora — SGs in VpcSecurityGroups
    try:
        rds = boto3.client('rds', region_name=region)
        for page in rds.get_paginator('describe_db_instances').paginate():
            for db in page['DBInstances']:
                for sg in db.get('VpcSecurityGroups', []):
                    if sg.get('VpcSecurityGroupId'):
                        used.add(sg['VpcSecurityGroupId'])
        for page in rds.get_paginator('describe_db_clusters').paginate():
            for cl in page['DBClusters']:
                for sg in cl.get('VpcSecurityGroups', []):
                    if sg.get('VpcSecurityGroupId'):
                        used.add(sg['VpcSecurityGroupId'])
    except Exception:
        pass

    # 7. ElastiCache clusters and serverless caches
    try:
        ec = boto3.client('elasticache', region_name=region)
        for page in ec.get_paginator('describe_cache_clusters').paginate():
            for cl in page['CacheClusters']:
                for sg in cl.get('SecurityGroups', []):
                    if sg.get('SecurityGroupId'):
                        used.add(sg['SecurityGroupId'])
        try:
            resp = ec.describe_serverless_caches()
            for sc in resp.get('ServerlessCaches', []):
                for sg in sc.get('SecurityGroupIds', []):
                    used.add(sg)
        except Exception:
            pass
    except Exception:
        pass

    # 8. Glue connections — SGs stored in config, ENIs only created when jobs run
    try:
        glue = boto3.client('glue', region_name=region)
        kwargs = {}
        while True:
            resp = glue.get_connections(**kwargs)
            for conn in resp.get('ConnectionList', []):
                pcr = conn.get('PhysicalConnectionRequirements') or {}
                for sg in pcr.get('SecurityGroupIdList', []):
                    used.add(sg)
            next_token = resp.get('NextToken')
            if not next_token:
                break
            kwargs['NextToken'] = next_token
    except Exception as e:
        if writer is not None:
            writer.add_error(region=region, source='sg (glue connections)', message=e)

    # 9. SageMaker domains and notebook instances
    try:
        sm = boto3.client('sagemaker', region_name=region)
        try:
            for domain in sm.list_domains().get('Domains', []):
                d = sm.describe_domain(DomainId=domain['DomainId'])
                for sg in d.get('DefaultUserSettings', {}).get('SecurityGroups', []):
                    used.add(sg)
                vpc_sg = d.get('DefaultSpaceSettings', {}).get('SecurityGroups', [])
                for sg in vpc_sg:
                    used.add(sg)
        except Exception:
            pass
        try:
            for page in sm.get_paginator('list_notebook_instances').paginate():
                for nb in page['NotebookInstances']:
                    detail = sm.describe_notebook_instance(NotebookInstanceName=nb['NotebookInstanceName'])
                    for sg in detail.get('SecurityGroups', []):
                        used.add(sg)
        except Exception:
            pass
    except Exception:
        pass

    # 10. AWS Batch compute environments
    try:
        batch = boto3.client('batch', region_name=region)
        for page in batch.get_paginator('describe_compute_environments').paginate():
            for ce in page['computeEnvironments']:
                for sg in ce.get('computeResources', {}).get('securityGroupIds', []):
                    used.add(sg)
    except Exception:
        pass

    # 11. MSK clusters
    try:
        kafka = boto3.client('kafka', region_name=region)
        for page in kafka.get_paginator('list_clusters_v2').paginate():
            for cl in page.get('ClusterInfoList', []):
                prov = cl.get('Provisioned', {})
                for sg in prov.get('BrokerNodeGroupInfo', {}).get('SecurityGroups', []):
                    used.add(sg)
                svl = cl.get('Serverless', {})
                for vpc_cfg in svl.get('VpcConfigs', []):
                    for sg in vpc_cfg.get('SecurityGroupIds', []):
                        used.add(sg)
    except Exception:
        pass

    # 12. App Runner VPC connectors
    try:
        ar = boto3.client('apprunner', region_name=region)
        resp = ar.list_vpc_connectors()
        for vc in resp.get('VpcConnectors', []):
            for sg in vc.get('SecurityGroups', []):
                used.add(sg)
    except Exception:
        pass

    # 13. Directory Service (Managed AD)
    try:
        ds = boto3.client('ds', region_name=region)
        for d in ds.describe_directories().get('DirectoryDescriptions', []):
            sg = d.get('VpcSettings', {}).get('SecurityGroupId')
            if sg:
                used.add(sg)
    except Exception:
        pass

    # 14. EKS clusters
    try:
        eks = boto3.client('eks', region_name=region)
        clusters = eks.list_clusters().get('clusters', [])
        for name in clusters:
            cl = eks.describe_cluster(name=name).get('cluster', {})
            vpc_cfg = cl.get('resourcesVpcConfig', {})
            for sg in vpc_cfg.get('securityGroupIds', []):
                used.add(sg)
            cluster_sg = vpc_cfg.get('clusterSecurityGroupId')
            if cluster_sg:
                used.add(cluster_sg)
    except Exception:
        pass

    # 15. Redshift clusters
    try:
        rs = boto3.client('redshift', region_name=region)
        for page in rs.get_paginator('describe_clusters').paginate():
            for cl in page['Clusters']:
                for sg in cl.get('VpcSecurityGroups', []):
                    if sg.get('VpcSecurityGroupId'):
                        used.add(sg['VpcSecurityGroupId'])
    except Exception:
        pass

    # 16. Step Functions — SG IDs embedded in state machine definition JSON
    try:
        sfn = boto3.client('stepfunctions', region_name=region)
        for page in sfn.get_paginator('list_state_machines').paginate():
            for sm in page['stateMachines']:
                try:
                    defn = sfn.describe_state_machine(stateMachineArn=sm['stateMachineArn'])
                    for sg in _SG_PATTERN.findall(defn.get('definition', '')):
                        used.add(sg)
                except Exception:
                    pass
    except Exception:
        pass

    # 17. CloudFormation stacks — SG IDs in parameter values and outputs
    try:
        cfn = boto3.client('cloudformation', region_name=region)
        for page in cfn.get_paginator('describe_stacks').paginate():
            for stack in page['Stacks']:
                for p in stack.get('Parameters', []):
                    for sg in _SG_PATTERN.findall(p.get('ParameterValue', '')):
                        used.add(sg)
                for o in stack.get('Outputs', []):
                    for sg in _SG_PATTERN.findall(o.get('OutputValue', '')):
                        used.add(sg)
    except Exception:
        pass

    # 18. SSM Parameter Store — SG IDs stored as parameter values
    try:
        ssm = boto3.client('ssm', region_name=region)
        for page in ssm.get_paginator('describe_parameters').paginate():
            for param in page['Parameters']:
                if param.get('Type') == 'SecureString':
                    continue
                try:
                    val = ssm.get_parameter(Name=param['Name']).get('Parameter', {}).get('Value', '')
                    for sg in _SG_PATTERN.findall(val):
                        used.add(sg)
                except Exception:
                    pass
    except Exception:
        pass

    # 19. Service Catalog provisioned products — SG IDs in launch parameters/outputs
    try:
        sc = boto3.client('servicecatalog', region_name=region)
        page_token = None
        while True:
            kwargs = {'AccessLevelFilter': {'Key': 'Account', 'Value': 'self'}}
            if page_token:
                kwargs['PageToken'] = page_token
            resp = sc.search_provisioned_products(**kwargs)
            for pp in resp.get('ProvisionedProducts', []):
                try:
                    detail = sc.describe_provisioned_product(Id=pp['Id']).get('ProvisionedProductDetail', {})
                    rec_id = detail.get('LastRecordId')
                    if rec_id:
                        rec = sc.describe_record(Id=rec_id)
                        for p in rec.get('RecordOutputs', []):
                            for sg in _SG_PATTERN.findall(p.get('OutputValue', '')):
                                used.add(sg)
                except Exception:
                    pass
            page_token = resp.get('NextPageToken')
            if not page_token:
                break
    except Exception:
        pass

    # 20. Elastic Beanstalk — SGs stored in environment config, persist at zero instances
    try:
        eb = boto3.client('elasticbeanstalk', region_name=region)
        for env in eb.describe_environments(IncludeDeleted=False).get('Environments', []):
            try:
                settings = eb.describe_configuration_settings(
                    ApplicationName=env['ApplicationName'],
                    EnvironmentName=env['EnvironmentName']
                )['ConfigurationSettings'][0]
                for opt in settings.get('OptionSettings', []):
                    if 'SecurityGroups' in opt.get('OptionName', ''):
                        for sg in _SG_PATTERN.findall(opt.get('Value', '')):
                            used.add(sg)
            except Exception:
                pass
    except Exception:
        pass

    # 21. VPC Endpoints
    try:
        ec2 = boto3.client('ec2', region_name=region)
        for page in ec2.get_paginator('describe_vpc_endpoints').paginate():
            for ep in page['VpcEndpoints']:
                for sg in ep.get('Groups', []):
                    used.add(sg.get('GroupId', ''))
    except Exception:
        pass

    return used


def gather(region, writer):
    # Isolated from the groups fetch below: a permission gap on
    # DescribeSecurityGroupRules specifically (rarer, but distinct from
    # DescribeSecurityGroups) shouldn't cost every security group in the
    # region its resource record too — record the error and continue with
    # whatever groups can still be read, same as every other per-service
    # failure this tool tolerates (see module docstring in __init__.py).
    rules_by_group = {}
    try:
        for rule in get_security_group_rules(region):
            rules_by_group.setdefault(rule['GroupId'], []).append(rule)
    except Exception as e:
        writer.add_error(region=region, source='sg (rules)', message=e)

    for sg in get_security_groups(region):
        group_id = sg['GroupId']
        raw = dict(sg)
        raw['_Rules'] = rules_by_group.get(group_id, [])
        writer.add_resource(
            resource_type='security_group',
            region=region,
            resource_id=group_id,
            resource_name=sg.get('GroupName', group_id),
            scope_id=sg.get('VpcId'),
            raw=raw,
            tags=sg.get('Tags'),
        )

    # Synthetic, region-scoped singleton (same convention as account.py's
    # iam_password_policy/catalog_encryption) — added unconditionally, even
    # when the set is empty, so it's always present downstream for
    # sg_unused's correlation. See get_attached_sg_ids()'s own docstring.
    attached_ids = get_attached_sg_ids(region, writer=writer)
    writer.add_resource(
        resource_type='security_group_usage',
        region=region,
        resource_id='attached_ids',
        resource_name='attached_ids',
        raw={'AttachedSecurityGroupIds': sorted(attached_ids)},
    )

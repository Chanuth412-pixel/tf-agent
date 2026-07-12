import os
import boto3
import json
import logging
from moto import mock_aws

logger = logging.getLogger(__name__)

def fetch_live_infrastructure(region_name=None):
    """
    Connects to AWS to read a complex, real-world infrastructure state.
    Fetches Network, Security, Compute, and Data resources to populate the Agentic Engine.
    """
    if not region_name:
        # Dynamically detect region from environment variables or active AWS CLI configuration
        session = boto3.Session()
        region_name = session.region_name or os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

    logger.info(f"Scanning AWS account in region {region_name}...")
    
    # 1. Initialize all necessary boto3 clients
    ec2 = boto3.client('ec2', region_name=region_name)
    s3 = boto3.client('s3', region_name=region_name)
    iam = boto3.client('iam')  # IAM is a global service
    autoscaling = boto3.client('autoscaling', region_name=region_name)
    dynamodb = boto3.client('dynamodb', region_name=region_name)
    rds = boto3.client('rds', region_name=region_name)
    sqs = boto3.client('sqs', region_name=region_name)
    lambda_client = boto3.client('lambda', region_name=region_name)
    
    resources = []
    vpc_id = None

    def get_tags_dict(aws_tags):
        if not aws_tags:
            return {}
        return {t['Key']: t['Value'] for t in aws_tags if 'Key' in t}

    def parse_ip_permissions(permissions):
        rules = []
        for perm in permissions:
            from_port = perm.get('FromPort', -1)
            to_port = perm.get('ToPort', -1)
            protocol = perm.get('IpProtocol', '-1')
            
            cidr_blocks = [ip_range['CidrIp'] for ip_range in perm.get('IpRanges', [])]
            ipv6_cidr_blocks = [ip_range['CidrIpv6'] for ip_range in perm.get('Ipv6Ranges', [])]
            security_groups = [group['GroupId'] for group in perm.get('UserIdGroupPairs', [])]
            
            rules.append({
                "from_port": from_port,
                "to_port": to_port,
                "protocol": protocol,
                "cidr_blocks": cidr_blocks,
                "ipv6_cidr_blocks": ipv6_cidr_blocks,
                "security_groups": security_groups
            })
        return rules

    try:
        # --- NETWORK RESOURCES ---
        vpcs_response = ec2.describe_vpcs()
        vpcs = vpcs_response.get('Vpcs', [])
        
        default_vpc_id = None
        # Find default VPC ID to skip its subnets
        for v in vpcs:
            if v.get('IsDefault', False) or v.get('CidrBlock') == '172.31.0.0/16':
                default_vpc_id = v['VpcId']
                break
                
        # Select the active VPC (prioritizing non-default custom VPC)
        selected_vpc = None
        if vpcs:
            non_default_vpcs = [v for v in vpcs if v['VpcId'] != default_vpc_id]
            selected_vpc = non_default_vpcs[0] if non_default_vpcs else vpcs[0]
            vpc_id = selected_vpc['VpcId']
            
            # Only append to resources if it's not the default ghost network
            if vpc_id != default_vpc_id:
                resources.append({
                    "type": "aws_vpc",
                    "id": vpc_id,
                    "cidr_block": selected_vpc['CidrBlock'],
                    "tags": get_tags_dict(selected_vpc.get('Tags', []))
                })
        else:
            vpc_id = None
            
        subnets_resp = ec2.describe_subnets()
        for sn in subnets_resp.get('Subnets', []):
            # Strictly skip subnets attached to the default VPC
            if sn['VpcId'] == default_vpc_id:
                continue
            # If we have a selected VPC and this subnet belongs to a different non-default VPC, skip it too
            if vpc_id and sn['VpcId'] != vpc_id:
                continue
            resources.append({
                "type": "aws_subnet",
                "id": sn['SubnetId'],
                "cidr_block": sn['CidrBlock'],
                "az": sn['AvailabilityZone'],
                "vpc_id": sn['VpcId'],
                "tags": get_tags_dict(sn.get('Tags', []))
            })

        # Discover Internet Gateways attached to this VPC
        if vpc_id:
            try:
                igw_resp = ec2.describe_internet_gateways()
                for igw in igw_resp.get('InternetGateways', []):
                    for attachment in igw.get('Attachments', []):
                        if attachment.get('VpcId') == vpc_id:
                            resources.append({
                                "type": "aws_internet_gateway",
                                "id": igw['InternetGatewayId'],
                                "tags": get_tags_dict(igw.get('Tags', []))
                            })
            except Exception as igw_err:
                logger.warning(f"Failed to fetch Internet Gateways: {str(igw_err)}")

        # --- SECURITY RESOURCES ---
        sg_filter = [{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else []
        sg_resp = ec2.describe_security_groups(Filters=sg_filter)
        for sg in sg_resp.get('SecurityGroups', []):
            if sg['GroupName'] != 'default': # Skip the default SG
                ingress_rules = []
                egress_rules = []
                try:
                    rules_resp = ec2.describe_security_group_rules(Filters=[{'Name': 'group-id', 'Values': [sg['GroupId']]}])
                    for rule in rules_resp.get('SecurityGroupRules', []):
                        rule_data = {
                            "from_port": rule.get('FromPort', -1),
                            "to_port": rule.get('ToPort', -1),
                            "protocol": rule.get('IpProtocol', '-1'),
                        }
                        if rule.get('CidrIpv4'):
                            rule_data['cidr_blocks'] = [rule['CidrIpv4']]
                        if rule.get('CidrIpv6'):
                            rule_data['ipv6_cidr_blocks'] = [rule['CidrIpv6']]
                        if rule.get('ReferencedGroupInfo', {}).get('GroupId'):
                            rule_data['security_groups'] = [rule['ReferencedGroupInfo']['GroupId']]
                        
                        if rule.get('IsEgress', False):
                            egress_rules.append(rule_data)
                        else:
                            ingress_rules.append(rule_data)
                except Exception as rules_err:
                    logger.warning(f"Failed to fetch detailed rules for SG {sg['GroupId']}: {str(rules_err)}")
                    ingress_rules = parse_ip_permissions(sg.get('IpPermissions', []))
                    egress_rules = parse_ip_permissions(sg.get('IpPermissionsEgress', []))

                resources.append({
                    "type": "aws_security_group",
                    "id": sg['GroupId'],
                    "name": sg['GroupName'],
                    "vpc_id": sg['VpcId'],
                    "description": sg.get('Description', ''),
                    "ingress": ingress_rules,
                    "egress": egress_rules,
                    "tags": get_tags_dict(sg.get('Tags', []))
                })
        
        try:
            roles_resp = iam.list_roles(MaxItems=50)
            for role in roles_resp.get('Roles', []):
                role_name = role['RoleName']
                # Fetching custom roles (excluding standard AWS service roles)
                if not role_name.startswith("AWSServiceRoleFor"):
                    resources.append({
                        "type": "aws_iam_role",
                        "id": role_name,
                        "name": role_name,
                        "arn": role['Arn'],
                        "description": role.get('Description', '')
                    })
        except Exception as iam_err:
            logger.warning(f"Failed to fetch IAM roles: {str(iam_err)}")

        # --- COMPUTE RESOURCES ---
        instances_filter = [{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else []
        instances_resp = ec2.describe_instances(Filters=instances_filter)
        for reservation in instances_resp.get('Reservations', []):
            for inst in reservation.get('Instances', []):
                # Only grab instances that are actually running or stopped (not terminated)
                if inst['State']['Name'] != 'terminated':
                    resources.append({
                        "type": "aws_instance",
                        "id": inst['InstanceId'],
                        "image_id": inst.get('ImageId'),  # Grab the exact ImageId of the running instance
                        "instance_type": inst['InstanceType'],
                        "subnet_id": inst.get('SubnetId', 'Unknown'),
                        "tags": get_tags_dict(inst.get('Tags', []))
                    })
                
        try:
            lts_resp = ec2.describe_launch_templates()
            for lt in lts_resp.get('LaunchTemplates', []):
                lt_entry = {
                    "type": "aws_launch_template",
                    "id": lt['LaunchTemplateId'],
                    "name": lt['LaunchTemplateName'],
                    "tags": get_tags_dict(lt.get('Tags', []))
                }
                try:
                    versions_resp = ec2.describe_launch_template_versions(
                        LaunchTemplateId=lt['LaunchTemplateId'],
                        Versions=['$Default']
                    )
                    versions = versions_resp.get('LaunchTemplateVersions', [])
                    if versions:
                        lt_data = versions[0].get('LaunchTemplateData', {})
                        if lt_data.get('ImageId'):
                            lt_entry['image_id'] = lt_data['ImageId']
                        if lt_data.get('InstanceType'):
                            lt_entry['instance_type'] = lt_data['InstanceType']
                        if lt_data.get('UserData'):
                            lt_entry['user_data'] = lt_data['UserData']
                        if lt_data.get('IamInstanceProfile'):
                            profile = lt_data['IamInstanceProfile']
                            lt_entry['iam_instance_profile'] = profile.get('Arn') or profile.get('Name')
                        if lt_data.get('BlockDeviceMappings'):
                            lt_entry['block_device_mappings'] = [
                                {
                                    "device_name": bdm.get('DeviceName'),
                                    "ebs": {
                                        "volume_size": bdm.get('Ebs', {}).get('VolumeSize'),
                                        "volume_type": bdm.get('Ebs', {}).get('VolumeType'),
                                        "encrypted": bdm.get('Ebs', {}).get('Encrypted')
                                    }
                                }
                                for bdm in lt_data['BlockDeviceMappings']
                                if bdm.get('Ebs')
                            ]
                except Exception as lt_ver_err:
                    logger.warning(f"Failed to fetch Launch Template version details: {str(lt_ver_err)}")
                resources.append(lt_entry)
        except Exception as lt_err:
            logger.warning(f"Failed to fetch Launch Templates: {str(lt_err)}")
        
        try:
            asgs_resp = autoscaling.describe_auto_scaling_groups()
            for asg in asgs_resp.get('AutoScalingGroups', []):
                asg_entry = {
                    "type": "aws_autoscaling_group",
                    "id": asg['AutoScalingGroupName'],
                    "name": asg['AutoScalingGroupName'],
                    "min_size": asg['MinSize'],
                    "max_size": asg['MaxSize'],
                    "desired_capacity": asg['DesiredCapacity'],
                    "tags": get_tags_dict(asg.get('Tags', []))
                }
                if asg.get('LaunchTemplate'):
                    asg_entry['launch_template_name'] = asg['LaunchTemplate'].get('LaunchTemplateName')
                    asg_entry['launch_template_id'] = asg['LaunchTemplate'].get('LaunchTemplateId')
                elif asg.get('LaunchConfigurationName'):
                    asg_entry['launch_configuration'] = asg['LaunchConfigurationName']
                resources.append(asg_entry)
        except Exception as asg_err:
            logger.warning(f"Failed to fetch Auto Scaling Groups: {str(asg_err)}")

        # --- DATA RESOURCES ---
        buckets_response = s3.list_buckets()
        for bucket in buckets_response.get('Buckets', []):
            try:
                tagging = s3.get_bucket_tagging(Bucket=bucket['Name'])
                bucket_tags = get_tags_dict(tagging.get('TagSet', []))
            except Exception:
                bucket_tags = {}

            try:
                ver = s3.get_bucket_versioning(Bucket=bucket['Name'])
                bucket_versioning = {"status": ver.get('Status', 'Disabled')}
            except Exception:
                bucket_versioning = {}

            try:
                enc = s3.get_bucket_encryption(Bucket=bucket['Name'])
                rules = enc.get('ServerSideEncryptionConfiguration', {}).get('Rules', [])
                if rules:
                    encryption_algorithm = rules[0].get('ApplyServerSideEncryptionByDefault', {}).get('SSEAlgorithm')
                    bucket_encryption = {"sse_algorithm": encryption_algorithm}
                else:
                    bucket_encryption = {}
            except Exception:
                bucket_encryption = {}

            resources.append({
                "type": "aws_s3_bucket",
                "id": bucket['Name'],
                "bucket": bucket['Name'],
                "tags": bucket_tags,
                "server_side_encryption": bucket_encryption,
                "versioning": bucket_versioning
            })
        
        try:
            tables_resp = dynamodb.list_tables()
            for table_name in tables_resp.get('TableNames', []):
                try:
                    desc_resp = dynamodb.describe_table(TableName=table_name)
                    table_desc = desc_resp.get('Table', {})
                    hash_key = ""
                    range_key = ""
                    for key in table_desc.get('KeySchema', []):
                        if key['KeyType'] == 'HASH':
                            hash_key = key['AttributeName']
                        elif key['KeyType'] == 'RANGE':
                            range_key = key['AttributeName']
                            
                    attr_defs = [
                        {
                            "name": attr['AttributeName'],
                            "type": attr['AttributeType']
                        }
                        for attr in table_desc.get('AttributeDefinitions', [])
                    ]
                    
                    try:
                        table_tags_resp = dynamodb.list_tags_of_resource(ResourceArn=table_desc['TableArn'])
                        table_tags = get_tags_dict(table_tags_resp.get('Tags', []))
                    except Exception:
                        table_tags = {}

                    resources.append({
                        "type": "aws_dynamodb_table",
                        "id": table_name,
                        "name": table_name,
                        "hash_key": hash_key,
                        "range_key": range_key,
                        "attribute_definitions": attr_defs,
                        "tags": table_tags
                    })
                except Exception as tbl_err:
                    logger.warning(f"Failed to describe DynamoDB table {table_name}: {str(tbl_err)}")
                    resources.append({
                        "type": "aws_dynamodb_table",
                        "id": table_name,
                        "name": table_name
                    })
        except Exception as db_err:
            logger.warning(f"Failed to fetch DynamoDB tables: {str(db_err)}")
        
        try:
            # Discover and fetch DB Subnet Groups
            db_subnet_groups_resp = rds.describe_db_subnet_groups()
            for sng in db_subnet_groups_resp.get('DBSubnetGroups', []):
                # Filter to only fetch subnet groups residing in the primary VPC
                if vpc_id and sng.get('VpcId') != vpc_id:
                    continue
                sng_subnets = [sub['SubnetIdentifier'] for sub in sng.get('Subnets', [])]
                resources.append({
                    "type": "aws_db_subnet_group",
                    "id": sng['DBSubnetGroupName'],
                    "name": sng['DBSubnetGroupName'],
                    "subnet_ids": sng_subnets,
                    "description": sng.get('DBSubnetGroupDescription', ''),
                    "tags": get_tags_dict(sng.get('TagList', []))
                })
        except Exception as sng_err:
            logger.warning(f"Failed to fetch DB Subnet Groups: {str(sng_err)}")

        try:
            db_instances_resp = rds.describe_db_instances()
            for db in db_instances_resp.get('DBInstances', []):
                # Filter to only fetch DB instances residing in the primary VPC
                db_subnet_group = db.get('DBSubnetGroup', {})
                db_vpc_id = db_subnet_group.get('VpcId')
                if vpc_id and db_vpc_id != vpc_id:
                    continue
                
                db_subnet_group_name = db_subnet_group.get('DBSubnetGroupName')
                subnet_ids = [sub['SubnetIdentifier'] for sub in db_subnet_group.get('Subnets', [])]
                
                resources.append({
                    "type": "aws_db_instance",
                    "id": db['DBInstanceIdentifier'],
                    "engine": db['Engine'],
                    "instance_class": db['DBInstanceClass'],
                    "storage_encrypted": db.get('StorageEncrypted', False),
                    "db_subnet_group_name": db_subnet_group_name,
                    "subnet_ids": subnet_ids,
                    "tags": get_tags_dict(db.get('TagList', []))
                })
        except Exception as rds_err:
            logger.warning(f"Failed to fetch RDS DB instances: {str(rds_err)}")

        # --- SQS RESOURCES ---
        try:
            queues_resp = sqs.list_queues()
            for queue_url in queues_resp.get('QueueUrls', []):
                queue_name = queue_url.split('/')[-1]
                attrs_resp = sqs.get_queue_attributes(
                    QueueUrl=queue_url,
                    AttributeNames=['All']
                )
                q_attrs = attrs_resp.get('Attributes', {})
                resources.append({
                    "type": "aws_sqs_queue",
                    "id": queue_name,
                    "name": queue_name,
                    "redrive_policy": q_attrs.get('RedrivePolicy')
                })
        except Exception as sqs_scan_err:
            logger.warning(f"Failed to fetch SQS queues: {str(sqs_scan_err)}")

        # --- LAMBDA RESOURCES ---
        try:
            funcs_resp = lambda_client.list_functions()
            for func in funcs_resp.get('Functions', []):
                func_name = func['FunctionName']
                resources.append({
                    "type": "aws_lambda_function",
                    "id": func_name,
                    "function_name": func_name,
                    "role": func['Role'],
                    "handler": func['Handler'],
                    "runtime": func['Runtime']
                })
                
                try:
                    mappings_resp = lambda_client.list_event_source_mappings(FunctionName=func_name)
                    for mapping in mappings_resp.get('EventSourceMappings', []):
                        resources.append({
                            "type": "aws_lambda_event_source_mapping",
                            "id": mapping['UUID'],
                            "event_source_arn": mapping['EventSourceArn'],
                            "function_name": func_name
                        })
                except Exception as es_scan_err:
                    logger.warning(f"Failed to fetch Event Source Mappings for {func_name}: {str(es_scan_err)}")
        except Exception as lam_scan_err:
            logger.warning(f"Failed to fetch Lambda functions: {str(lam_scan_err)}")

        logger.info("Successfully fetched complete live infrastructure.")
        
    except Exception as e:
        logger.error(f"Error fetching infrastructure from AWS: {str(e)}")
        print(f"AWS API Error: Make sure your credentials are set and you have permissions! ({str(e)})")
        
    return {
        "vpc_id": vpc_id,
        "region": region_name,
        "resources": resources
    }


def compile_infrastructure_graph(raw_data, mode):
    """
    Compiles discovered infrastructure resources into a structured graph with nodes and edges.
    Supports 'import' mode (from boto3 telemetry JSON) and 'clone' mode (from static HCL string parsing).
    """
    import re

    nodes = {}
    edges = []

    if mode == "import":
        # Raw data is expected to be a list of resource dicts or a dictionary containing 'resources'
        resources = []
        if isinstance(raw_data, list):
            resources = raw_data
        elif isinstance(raw_data, dict):
            # Try to grab the first VPC if present, to represent it as a node
            vpc_id = raw_data.get("vpc_id")
            if vpc_id:
                nodes[f"aws_vpc.{vpc_id}"] = {
                    "type": "aws_vpc",
                    "name": vpc_id,
                    "display_name": vpc_id
                }
            resources = raw_data.get("resources", [])

        for resource in resources:
            res_type = resource.get("type")
            res_id = resource.get("id")
            if not res_type or not res_id:
                continue

            node_id = f"{res_type}.{res_id}"
            tags = resource.get("tags") or {}
            display_name = tags.get("Name") or resource.get("name") or resource.get("bucket") or res_id
            
            nodes[node_id] = {
                "type": res_type,
                "name": display_name,
                "display_name": display_name
            }

            # Extract Edges based on explicit structural attachment keys
            
            # VPC container relationship
            vpc_ref = (
                resource.get("vpc_id") or 
                resource.get("VpcId") or 
                resource.get("attributes", {}).get("vpc_id") or 
                resource.get("attributes", {}).get("VpcId")
            )
            if vpc_ref:
                edges.append({
                    "source": node_id,
                    "target": f"aws_vpc.{vpc_ref}",
                    "relation": "isolated_by" if res_type == "aws_security_group" else "contained_in"
                })

            # Subnet deployed relationship
            subnet_ref = (
                resource.get("subnet_id") or 
                resource.get("SubnetId") or 
                resource.get("attributes", {}).get("subnet_id") or 
                resource.get("attributes", {}).get("SubnetId")
            )
            if subnet_ref:
                edges.append({
                    "source": node_id,
                    "target": f"aws_subnet.{subnet_ref}",
                    "relation": "deployed_in" if res_type == "aws_instance" else "associated_with"
                })

            # Security group relationship (e.g. EC2 instance SecurityGroups)
            sg_list = (
                resource.get("SecurityGroups") or 
                resource.get("security_groups") or 
                resource.get("vpc_security_group_ids") or 
                resource.get("attributes", {}).get("vpc_security_group_ids") or 
                resource.get("attributes", {}).get("security_groups") or 
                []
            )
            if isinstance(sg_list, str):
                sg_list = [sg_list]
            for sg in sg_list:
                sg_id = None
                if isinstance(sg, dict):
                    sg_id = sg.get("GroupId") or sg.get("group_id")
                elif isinstance(sg, str):
                    sg_id = sg
                if sg_id:
                    relation = "protected_by" if res_type in ["aws_instance", "aws_eks_cluster", "aws_eks_node_group", "aws_autoscaling_group", "aws_launch_template"] else "uses"
                    edges.append({
                        "source": node_id,
                        "target": f"aws_security_group.{sg_id}",
                        "relation": relation
                    })

            # Launch template relationship
            lt_ref = resource.get("launch_template_id") or resource.get("LaunchTemplateId")
            if lt_ref:
                edges.append({
                    "source": node_id,
                    "target": f"aws_launch_template.{lt_ref}",
                    "relation": "uses_template"
                })

            # DB Subnet Group and RDS relationships
            if res_type == "aws_db_subnet_group":
                subnet_ids = resource.get("subnet_ids") or []
                for sub_id in subnet_ids:
                    edges.append({
                        "source": node_id,
                        "target": f"aws_subnet.{sub_id}",
                        "relation": "groups_subnet"
                    })

            if res_type == "aws_db_instance":
                db_sng = resource.get("db_subnet_group_name") or "default"
                sng_node_id = f"aws_db_subnet_group.{db_sng}"
                
                # Dynamic injection of DB subnet group node if not already present
                if sng_node_id not in nodes:
                    nodes[sng_node_id] = {
                        "type": "aws_db_subnet_group",
                        "name": db_sng,
                        "display_name": db_sng
                    }
                
                edges.append({
                    "source": node_id,
                    "target": sng_node_id,
                    "relation": "uses_subnet_group"
                })

                # If we dynamically created the sng node or it has subnet IDs, let's link it to subnets
                snet_ids = resource.get("subnet_ids") or []
                if not snet_ids:
                    # Look up subnets in the list of resources
                    for r in resources:
                        if r.get("type") == "aws_subnet":
                            r_tags = r.get("tags") or {}
                            r_name = r_tags.get("Name", "").lower()
                            r_id = r.get("id", "").lower()
                            # Prefer private subnets
                            if "private" in r_name or "private" in r_id:
                                snet_ids.append(r.get("id"))
                    # Fallback to all subnets if no private found
                    if not snet_ids:
                        for r in resources:
                            if r.get("type") == "aws_subnet":
                                snet_ids.append(r.get("id"))
                
                # Check existing edges to avoid duplicate groups_subnet edges
                existing_sng_targets = {e["target"] for e in edges if e["source"] == sng_node_id and e["relation"] == "groups_subnet"}
                for sub_id in snet_ids:
                    target_sub = f"aws_subnet.{sub_id}"
                    if target_sub not in existing_sng_targets:
                        edges.append({
                            "source": sng_node_id,
                            "target": target_sub,
                            "relation": "groups_subnet"
                        })

    elif mode == "clone":
        # Raw data is expected to be HCL configuration string
        hcl_content = ""
        if isinstance(raw_data, str):
            hcl_content = raw_data
        elif isinstance(raw_data, dict):
            # If a dict of files, join them
            hcl_content = "\n".join(raw_data.values())

        # Extract Nodes using r'resource\s+"([^"]+)"\s+"([^"]+)"'
        # To identify positions, we can use re.finditer to extract both resource info and block ranges
        resource_pattern = r'resource\s+"([^"]+)"\s+"([^"]+)"'
        matches = list(re.finditer(resource_pattern, hcl_content))
        
        for match in matches:
            res_type = match.group(1)
            res_name = match.group(2)
            node_id = f"{res_type}.{res_name}"
            
            nodes[node_id] = {
                "type": res_type,
                "name": res_name,
                "display_name": res_name
            }

        # Extract Edges by scanning each block for references to other resources
        for i, match in enumerate(matches):
            current_id = f"{match.group(1)}.{match.group(2)}"
            start_pos = match.start()
            # The block extends to the start of the next resource or end of content
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(hcl_content)
            block_text = hcl_content[start_pos:end_pos]

            for other_id in nodes:
                if other_id == current_id:
                    continue
                # Search for reference with word boundaries to ensure it's not a substring of a larger identifier
                ref_pattern = r'\b' + re.escape(other_id) + r'\b'
                if re.search(ref_pattern, block_text):
                    res_type_curr = current_id.split('.')[0]
                    res_type_other = other_id.split('.')[0]
                    relation = "depends_on"
                    if res_type_curr in ["aws_instance", "aws_eks_cluster", "aws_eks_node_group", "aws_autoscaling_group", "aws_launch_template"] and res_type_other == "aws_security_group":
                        relation = "protected_by"
                    edges.append({
                        "source": current_id,
                        "target": other_id,
                        "relation": relation
                    })

    return {
        "nodes": nodes,
        "edges": edges
    }


# --- UPGRADED LOCAL TESTING HARNESS ---
@mock_aws
def test_fetcher_locally():
    print("[Local Test] Simulating a complex AWS environment from mock_infra.json...")
    
    import json
    import os
    
    file_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(file_dir, "..", "scanner", "mock_infra.json")
    
    try:
        with open(json_path, 'r') as f:
            mock_data = json.load(f)
    except Exception as e:
        print(f"[Local Test] Error loading mock_infra.json: {str(e)}")
        mock_data = {}

    region = mock_data.get("region", "us-east-1")
    ec2 = boto3.client('ec2', region_name=region)
    s3 = boto3.client('s3', region_name=region)
    rds = boto3.client('rds', region_name=region)
    dynamodb = boto3.client('dynamodb', region_name=region)
    sqs = boto3.client('sqs', region_name=region)
    iam = boto3.client('iam', region_name=region)
    lambda_client = boto3.client('lambda', region_name=region)
    
    # 1. Pass One: Create independent resources and VPCs
    vpc_map = {}
    subnet_map = {}
    sg_map = {}
    iam_role_map = {}
    
    # We always ensure a fallback VPC is created if no VPC is defined in mock_infra.json
    vpc_defined = any(r.get("type") == "aws_vpc" for r in mock_data.get("resources", []))
    if not vpc_defined:
        fallback_vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        fallback_vpc_id = fallback_vpc["Vpc"]["VpcId"]
        vpc_map["default_vpc"] = fallback_vpc_id
        ec2.create_tags(Resources=[fallback_vpc_id], Tags=[{'Key': 'Name', 'Value': 'mock-vpc'}])
        
        # Fallback subnets
        subnet_a = ec2.create_subnet(VpcId=fallback_vpc_id, CidrBlock='10.0.1.0/24', AvailabilityZone=f'{region}a')
        subnet_b = ec2.create_subnet(VpcId=fallback_vpc_id, CidrBlock='10.0.2.0/24', AvailabilityZone=f'{region}b')
        subnet_map["default_subnet_a"] = subnet_a['Subnet']['SubnetId']
        subnet_map["default_subnet_b"] = subnet_b['Subnet']['SubnetId']

    for r in mock_data.get("resources", []):
        r_type = r.get("type")
        r_id = r.get("id")
        r_name = r.get("name")
        attrs = r.get("attributes", {})
        
        if r_type == "aws_vpc":
            cidr = r.get("cidr_block") or attrs.get("cidr_block") or "10.0.0.0/16"
            resp = ec2.create_vpc(CidrBlock=cidr)
            v_id = resp["Vpc"]["VpcId"]
            vpc_map[r_id] = v_id
            if r_name:
                vpc_map[r_name] = v_id
            ec2.create_tags(Resources=[v_id], Tags=[{'Key': 'Name', 'Value': r_name or r_id}])
            
        elif r_type == "aws_s3_bucket":
            bucket_name = r_id or r_name
            try:
                if region == "us-east-1":
                    s3.create_bucket(Bucket=bucket_name)
                else:
                    s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )
                versioning = attrs.get("versioning")
                if versioning == "Enabled" or (isinstance(versioning, dict) and versioning.get("status") == "Enabled"):
                    s3.put_bucket_versioning(
                        Bucket=bucket_name,
                        VersioningConfiguration={'Status': 'Enabled'}
                    )
            except Exception as s3_err:
                print(f"[Local Test] Error creating S3 bucket {bucket_name}: {str(s3_err)}")
                
        elif r_type == "aws_iam_role":
            role_name = r_name or r_id
            assume_policy = r.get("assume_role_policy") or attrs.get("assume_role_policy", "lambda.amazonaws.com")
            policy_doc = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Service": assume_policy
                        },
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            try:
                resp = iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(policy_doc)
                )
                role_arn = resp["Role"]["Arn"]
                iam_role_map[r_id] = role_arn
                if r_name:
                    iam_role_map[r_name] = role_arn
            except Exception as iam_err:
                print(f"[Local Test] Error creating IAM role {role_name}: {str(iam_err)}")

    # 2. Pass Two: Create Subnets, Security Groups, SQS, DynamoDB (Requires VPC mapping)
    for r in mock_data.get("resources", []):
        r_type = r.get("type")
        r_id = r.get("id")
        r_name = r.get("name")
        attrs = r.get("attributes", {})
        
        if r_type == "aws_subnet":
            vpc_ref = r.get("vpc_id") or attrs.get("vpc_id")
            real_vpc_id = vpc_map.get(vpc_ref) or list(vpc_map.values())[0]
            cidr = r.get("cidr_block") or attrs.get("cidr_block") or "10.0.1.0/24"
            az = r.get("availability_zone") or attrs.get("availability_zone") or f"{region}a"
            
            resp = ec2.create_subnet(VpcId=real_vpc_id, CidrBlock=cidr, AvailabilityZone=az)
            s_id = resp["Subnet"]["SubnetId"]
            subnet_map[r_id] = s_id
            if r_name:
                subnet_map[r_name] = s_id
            ec2.create_tags(Resources=[s_id], Tags=[{'Key': 'Name', 'Value': r_name or r_id}])
            
        elif r_type == "aws_security_group":
            vpc_ref = r.get("vpc_id") or attrs.get("vpc_id")
            real_vpc_id = vpc_map.get(vpc_ref) or list(vpc_map.values())[0]
            sg_name = r_name or r_id or "mock-sg"
            
            resp = ec2.create_security_group(GroupName=sg_name, Description=attrs.get("description", "Mock SG"), VpcId=real_vpc_id)
            g_id = resp["GroupId"]
            sg_map[r_id] = g_id
            if r_name:
                sg_map[r_name] = g_id
                
        elif r_type == "aws_dynamodb_table":
            table_name = r_name or r_id
            hash_key = r.get("hash_key") or attrs.get("hash_key", "id")
            range_key = r.get("range_key") or attrs.get("range_key")
            
            key_schema = [{'AttributeName': hash_key, 'KeyType': 'HASH'}]
            attr_defs = [{'AttributeName': hash_key, 'AttributeType': 'S'}]
            
            if range_key:
                key_schema.append({'AttributeName': range_key, 'KeyType': 'RANGE'})
                attr_defs.append({'AttributeName': range_key, 'AttributeType': 'S'})
                
            try:
                dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=key_schema,
                    AttributeDefinitions=attr_defs,
                    BillingMode=r.get("billing_mode") or attrs.get("billing_mode", "PAY_PER_REQUEST")
                )
            except Exception as dy_err:
                print(f"[Local Test] Error creating DynamoDB table {table_name}: {str(dy_err)}")

        elif r_type == "aws_sqs_queue":
            queue_name = r_name or r_id
            queue_attrs = {}
            redrive_policy = r.get("redrive_policy") or attrs.get("redrive_policy")
            if redrive_policy:
                queue_attrs['RedrivePolicy'] = redrive_policy
            try:
                sqs.create_queue(
                    QueueName=queue_name,
                    Attributes=queue_attrs
                )
            except Exception as sqs_err:
                print(f"[Local Test] Error creating SQS queue {queue_name}: {str(sqs_err)}")

    # 3. Pass Three: Create Compute (EC2, Lambda, RDS) (Requires Subnet, SG, IAM Role maps)
    for r in mock_data.get("resources", []):
        r_type = r.get("type")
        r_id = r.get("id")
        r_name = r.get("name")
        attrs = r.get("attributes", {})
        
        if r_type == "aws_instance":
            ami = attrs.get("ami") or attrs.get("image_id") or "ami-12c6146b"
            instance_type = attrs.get("instance_type") or "t3.micro"
            
            subnet_ref = r.get("subnet_id") or attrs.get("subnet_id")
            real_subnet_id = subnet_map.get(subnet_ref) or list(subnet_map.values())[0]
            
            sg_refs = r.get("vpc_security_group_ids") or attrs.get("security_groups") or []
            real_sg_ids = [sg_map.get(sg) for sg in sg_refs if sg in sg_map]
            
            run_resp = ec2.run_instances(
                ImageId=ami,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                SubnetId=real_subnet_id,
                SecurityGroupIds=real_sg_ids
            )
            inst_id = run_resp['Instances'][0]['InstanceId']
            ec2.create_tags(Resources=[inst_id], Tags=[{'Key': 'Name', 'Value': r_name or r_id}])
            
        elif r_type == "aws_lambda_function":
            func_name = r.get("function_name") or attrs.get("function_name") or r_id
            role_ref = r.get("role") or attrs.get("role")
            role_arn = iam_role_map.get(role_ref) or role_ref or f"arn:aws:iam::{mock_data.get('account_id', '123456789012')}:role/default"
            handler = r.get("handler") or attrs.get("handler", "index.handler")
            runtime = r.get("runtime") or attrs.get("runtime", "nodejs20.x")
            
            import zipfile
            import io
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED, False) as zip_file:
                zip_file.writestr('index.js', 'exports.handler = async (event) => { return {}; };')
            zip_buffer.seek(0)
            zip_bytes = zip_buffer.read()
            
            try:
                lambda_client.create_function(
                    FunctionName=func_name,
                    Runtime=runtime,
                    Role=role_arn,
                    Handler=handler,
                    Code={'ZipFile': zip_bytes}
                )
            except Exception as lam_err:
                print(f"[Local Test] Error creating Lambda function {func_name}: {str(lam_err)}")

        elif r_type == "aws_lambda_event_source_mapping":
            event_source = r.get("event_source_arn") or attrs.get("event_source_arn")
            func_name = r.get("function_name") or attrs.get("function_name")
            try:
                lambda_client.create_event_source_mapping(
                    EventSourceArn=event_source,
                    FunctionName=func_name
                )
            except Exception as es_err:
                print(f"[Local Test] Error creating Event Source Mapping: {str(es_err)}")

        elif r_type == "aws_db_instance":
            db_id = r_id or r_name
            engine = attrs.get("engine", "postgres")
            instance_class = attrs.get("instance_class", "db.t3.micro")
            allocated_storage = attrs.get("allocated_storage", 20)
            
            sng_subnets = list(subnet_map.values())[:2]
            try:
                rds.create_db_subnet_group(
                    DBSubnetGroupName='default',
                    DBSubnetGroupDescription='Default DB Subnet Group',
                    SubnetIds=sng_subnets
                )
            except rds.exceptions.DBSubnetGroupAlreadyExistsFault:
                pass
                
            try:
                rds.create_db_instance(
                    DBInstanceIdentifier=db_id,
                    DBInstanceClass=instance_class,
                    Engine=engine,
                    AllocatedStorage=allocated_storage,
                    MasterUsername='admin',
                    MasterUserPassword='TempPassword123!',
                    DBSubnetGroupName='default'
                )
            except Exception as rds_err:
                print(f"[Local Test] Error creating DB instance {db_id}: {str(rds_err)}")

    print("[Local Test] Fetching data using your upgraded function...")
    data = fetch_live_infrastructure(region_name=region)
    print(json.dumps(data, indent=4))
    return data

if __name__ == "__main__":
    # If you run this file directly (python3 src/aws_client.py), it will test your connection
    # and print out everything it finds before running the main engine.
    logging.basicConfig(level=logging.INFO)
    data = fetch_live_infrastructure()
    print("\n--- DISCOVERED AWS RESOURCES ---")
    print(json.dumps(data, indent=4))
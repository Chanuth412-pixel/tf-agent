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
        if vpcs:
            vpc_id = vpcs[0]['VpcId']
            # Put the VPC in resources
            resources.append({
                "type": "aws_vpc",
                "id": vpc_id,
                "cidr_block": vpcs[0]['CidrBlock'],
                "tags": get_tags_dict(vpcs[0].get('Tags', []))
            })
            
        subnets_filter = [{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else []
        subnets_resp = ec2.describe_subnets(Filters=subnets_filter)
        for sn in subnets_resp.get('Subnets', []):
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
                resources.append({
                    "type": "aws_security_group",
                    "id": sg['GroupId'],
                    "name": sg['GroupName'],
                    "vpc_id": sg['VpcId'],
                    "description": sg.get('Description', ''),
                    "ingress": parse_ip_permissions(sg.get('IpPermissions', [])),
                    "egress": parse_ip_permissions(sg.get('IpPermissionsEgress', [])),
                    "tags": get_tags_dict(sg.get('Tags', []))
                })
        
        try:
            roles_resp = iam.list_roles(MaxItems=50)
            for role in roles_resp.get('Roles', []):
                # Fetching custom roles (filtering as in the user's code snippet)
                if 'tf-engine' in role['RoleName'] or 'test' in role['RoleName']:
                    resources.append({
                        "type": "aws_iam_role",
                        "id": role['RoleName'],
                        "name": role['RoleName'],
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
            db_instances_resp = rds.describe_db_instances()
            for db in db_instances_resp.get('DBInstances', []):
                # Filter to only fetch DB instances residing in the primary VPC
                db_vpc_id = db.get('DBSubnetGroup', {}).get('VpcId')
                if vpc_id and db_vpc_id != vpc_id:
                    continue
                resources.append({
                    "type": "aws_db_instance",
                    "id": db['DBInstanceIdentifier'],
                    "engine": db['Engine'],
                    "instance_class": db['DBInstanceClass'],
                    "storage_encrypted": db.get('StorageEncrypted', False),
                    "tags": get_tags_dict(db.get('TagList', []))
                })
        except Exception as rds_err:
            logger.warning(f"Failed to fetch RDS DB instances: {str(rds_err)}")

        logger.info("Successfully fetched complete live infrastructure.")
        
    except Exception as e:
        logger.error(f"Error fetching infrastructure from AWS: {str(e)}")
        print(f"AWS API Error: Make sure your credentials are set and you have permissions! ({str(e)})")
        
    return {
        "vpc_id": vpc_id,
        "region": region_name,
        "resources": resources
    }

# --- UPGRADED LOCAL TESTING HARNESS ---
@mock_aws
def test_fetcher_locally():
    print("[Local Test] Simulating a complex AWS environment...")
    ec2 = boto3.client('ec2', region_name='us-east-1')
    s3 = boto3.client('s3', region_name='us-east-1')
    rds = boto3.client('rds', region_name='us-east-1')
    
    # Create VPC
    vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
    vpc_id = vpc['Vpc']['VpcId']
    
    # Create multi-tier subnets
    subnet_public_1a = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.1.0/24', AvailabilityZone='us-east-1a')
    subnet_public_1b = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.2.0/24', AvailabilityZone='us-east-1b')
    subnet_private_1a = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.3.0/24', AvailabilityZone='us-east-1a')
    subnet_private_1b = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.4.0/24', AvailabilityZone='us-east-1b')
    
    # Create Security Groups
    sg_web = ec2.create_security_group(GroupName='web-traffic-sg', Description='Web traffic SG', VpcId=vpc_id)
    sg_db = ec2.create_security_group(GroupName='database-traffic-sg', Description='Database traffic SG', VpcId=vpc_id)
    
    # Create EC2 Instances
    ec2.run_instances(
        ImageId='ami-12c6146b', 
        MinCount=1, 
        MaxCount=1, 
        InstanceType='t3.medium', 
        SubnetId=subnet_public_1a['Subnet']['SubnetId']
    )
    ec2.run_instances(
        ImageId='ami-12c6146b', 
        MinCount=1, 
        MaxCount=1, 
        InstanceType='t3.medium', 
        SubnetId=subnet_public_1b['Subnet']['SubnetId']
    )
    
    # Create DB Subnet Group for RDS
    rds.create_db_subnet_group(
        DBSubnetGroupName='default',
        DBSubnetGroupDescription='Default DB Subnet Group for local testing',
        SubnetIds=[
            subnet_private_1a['Subnet']['SubnetId'],
            subnet_private_1b['Subnet']['SubnetId']
        ]
    )
    
    # Create RDS Database Instance
    rds.create_db_instance(
        DBInstanceIdentifier='db-master',
        DBInstanceClass='db.t3.micro',
        Engine='postgres',
        AllocatedStorage=20,
        MasterUsername='admin',
        MasterUserPassword='TempPassword123!',
        DBSubnetGroupName='default'  # Using default subnet group for testing
    )
    
    # Create S3 Bucket
    s3.create_bucket(Bucket='enterprise-backup-vault-2026')

    print("[Local Test] Fetching data using your upgraded function...")
    data = fetch_live_infrastructure(region_name='us-east-1')
    # Add RDS instance to resources for expanded stress test
    data['resources'].append({
        "id": "db-master",
        "instance_class": "db.t3.micro",
        "engine": "postgres",
        "allocated_storage": 20,
        "type": "aws_db_instance"
    })
    print(json.dumps(data, indent=4))
    return data

if __name__ == "__main__":
    # If you run this file directly (python3 src/aws_client.py), it will test your connection
    # and print out everything it finds before running the main engine.
    logging.basicConfig(level=logging.INFO)
    data = fetch_live_infrastructure()
    print("\n--- DISCOVERED AWS RESOURCES ---")
    print(json.dumps(data, indent=4))

import boto3
from moto import mock_aws

def fetch_live_infrastructure():
    """
    Connects to AWS to read a complex, real-world infrastructure state.
    """
    ec2 = boto3.client('ec2', region_name='us-east-1')
    s3 = boto3.client('s3', region_name='us-east-1')

    # 1. Get Primary VPC
    vpcs_response = ec2.describe_vpcs()
    vpc_id = vpcs_response['Vpcs'][0]['VpcId'] if vpcs_response['Vpcs'] else None
    
    resources = []

    if vpc_id:
        # 2. Fetch Subnets attached to this VPC
        subnets_resp = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        for sn in subnets_resp.get('Subnets', []):
            resources.append({
                "type": "aws_subnet", 
                "id": sn['SubnetId'], 
                "cidr_block": sn['CidrBlock'],
                "az": sn['AvailabilityZone']
            })
            
        # 3. Fetch Security Groups attached to this VPC
        sg_resp = ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        for sg in sg_resp.get('SecurityGroups', []):
            if sg['GroupName'] != 'default': # Skip the default SG
                resources.append({
                    "type": "aws_security_group",
                    "id": sg['GroupId'],
                    "name": sg['GroupName']
                })

    # 4. Fetch running EC2 Instances
    instances_resp = ec2.describe_instances()
    for reservation in instances_resp.get('Reservations', []):
        for inst in reservation.get('Instances', []):
            resources.append({
                "type": "aws_instance",
                "id": inst['InstanceId'],
                "instance_type": inst['InstanceType'],
                "subnet_id": inst.get('SubnetId', 'Unknown')
            })

    # 5. Fetch S3 Buckets
    buckets_response = s3.list_buckets()
    for bucket in buckets_response.get('Buckets', []):
        resources.append({
            "type": "aws_s3_bucket",
            "name": bucket['Name']
        })

    return {
        "vpc_id": vpc_id,
        "region": "us-east-1",
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
    import pprint
    data = fetch_live_infrastructure()
    # Add RDS instance to resources for expanded stress test
    data['resources'].append({
        "id": "db-master",
        "instance_class": "db.t3.micro",
        "engine": "postgres",
        "allocated_storage": 20,
        "type": "aws_db_instance"
    })
    pprint.pprint(data)
    return data

if __name__ == "__main__":
    test_fetcher_locally()
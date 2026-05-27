
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
    
    # Create VPC
    vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
    vpc_id = vpc['Vpc']['VpcId']
    
    # Create Subnets
    ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.1.0/24', AvailabilityZone='us-east-1a')
    subnet2 = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.2.0/24', AvailabilityZone='us-east-1b')
    
    # Create Security Group
    ec2.create_security_group(GroupName='web-tier-sg', Description='Web SG', VpcId=vpc_id)
    
    # Create EC2 Instance
    ec2.run_instances(
        ImageId='ami-12c6146b', 
        MinCount=1, 
        MaxCount=1, 
        InstanceType='t3.micro', 
        SubnetId=subnet2['Subnet']['SubnetId']
    )
    
    # Create S3 Bucket
    s3.create_bucket(Bucket='production-assets-2026')

    print("[Local Test] Fetching data using your upgraded function...")
    import pprint
    data = fetch_live_infrastructure()
    # Ensure the LLM has a deterministic Security Group ID to reference
    # Add the exact mock SG dictionary requested by the user so import-mode prompts can use it
    mock_sg = {'id': 'sg-0123456789abcdef0', 'type': 'aws_security_group', 'name': 'eks-cluster-sg'}
    data.setdefault('resources', []).append(mock_sg)
    pprint.pprint(data)
    return data

if __name__ == "__main__":
    test_fetcher_locally()
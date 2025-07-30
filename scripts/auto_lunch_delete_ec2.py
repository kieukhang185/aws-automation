import os
import json
import time
import boto3
from botocore.exceptions import ClientError
from typing import Optional, List

ec2 = boto3.client("ec2")
az= "us-east-1a"
TAG_NAME = "Name"                       # default to "Name"
TAG_NAME_VALUE = "Lamda-default"        # -> vpc: khangkieu-vpc
TAG_PROJECT = "Project"                 # default to "Project"
TAG_PROJECT_VALUE = "Lamda-default"     # e.g devops-khangkieu


def find_rtbs_from_vpc(vpc_id: str) -> List[str]:
    """Return a list of Route Table IDs associated with the VPC."""
    filters = [{"Name": "vpc-id", "Values": [vpc_id]}]
    rtbs = ec2.describe_route_tables(Filters=filters)["RouteTables"]
    # grab just the main table (Associations[].Main == True)
    main_rtb_id = next(
        rt['RouteTableId'] for rt in rtbs
        if any(a.get('Main') for a in rt['Associations'])
    )
    return main_rtb_id  

def wait_until_state(describe_call, id_key, target_state, sleep=3, timeout=120):
    """Generic helper to wait for a resource to reach a desired state."""
    elapsed = 0
    while elapsed < timeout:
        resp = describe_call()
        state = resp[id_key][0]["State"]
        if state.lower() == target_state.lower():
            return
        time.sleep(sleep)
        elapsed += sleep
    raise TimeoutError(f"{id_key} did not reach state {target_state} in {timeout}s")


def wait_for_instance_state(instance_id: str, target: str, timeout: int = 300, delay: int = 10):
    """Poll until the instance reaches the desired state."""
    elapsed = 0
    while elapsed < timeout:
        res = ec2.describe_instances(InstanceIds=[instance_id])
        state = res["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state.lower() == target.lower():
            return
        time.sleep(delay)
        elapsed += delay
    raise TimeoutError(f"Instance {instance_id} never reached '{target}' in {timeout}s")


def find_id_by_tag(resource: str, tag_value: str) -> Optional[str]:
    """Return the first resource ID whose Name tag matches tag_value."""
    filters = [{"Name": "tag:Name", "Values": [tag_value]}]

    if resource == "vpc":
        vpcs = ec2.describe_vpcs(Filters=filters)["Vpcs"]
        return vpcs[0]["VpcId"] if vpcs else None

    if resource == "subnet":
        subnets = ec2.describe_subnets(Filters=filters)["Subnets"]
        return subnets[0]["SubnetId"] if subnets else None

    if resource == "instance":
        resv = ec2.describe_instances(Filters=filters)["Reservations"]
        return resv[0]["Instances"][0]["InstanceId"] if resv else None

    if resource == "sg":
        return ec2.describe_security_groups(Filters=filters) \
                  ["SecurityGroups"][0]["GroupId"]
    return None


################## MAIN ##################
def lambda_handler(event, context):
    """
    Debian12:ami-0779caf41f9ba54f0
    Ubuntu24.04: ami-020cba7c55df1f615
    Input (all optional – defaults provided):
    {
        "state": "start",
        "vpc_cidr": "10.0.10.1/16",
        "subnet_cidr": "10.0.1.0/24",
        "instance_type": "t2.micro",
        "ami_id": "ami-0779caf41f9ba54f0",
        "key_pair": "khang-kieu-demo",
        "ingress_rules": [
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}]
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [ {"CidrIp": "0.0.0.0/0"}]
            }
        ],
        "tag_key": "Name",
        "tag_name_value": "khangkieu",
        "tag_project": "Project",
        "tag_project_value": "vtd-devops-khangkieu"
    }
    """

    # Parameter setup
    vpc_cidr      = event.get("vpc_cidr", "10.0.0.0/16")
    subnet_cidr   = event.get("subnet_cidr", "10.0.1.0/24")
    instance_type = event.get("instance_type", "t2.micro")
    ami_id        = event.get("ami_id", "ami-020cba7c55df1f615")
    key_pair      = event.get("key_pair", "khang-kieu-demo")
    ingress_rules = event.get("ingress_rules", [
        {  # SSH
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
        },
        {  # HTTP
            "IpProtocol": "tcp",
            "FromPort": 80,
            "ToPort": 80,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
        }
    ])
    state         = event.get("state", "stop")                              # optional
    tag_name = event.get("tag_name", TAG_NAME)                              # default to "Name"
    tag_name_value = event.get("tag_name_value", TAG_NAME_VALUE)            # -> vpc: khangkieu-vpc
    tag_project = event.get("tag_project", TAG_PROJECT)                     # default to "Project"
    tag_project_value = event.get("tag_project_value", TAG_PROJECT_VALUE)   # e.g devops-khangkieu

    if state == "start":
        try:
            # Create VPC
            vpc_resp = ec2.create_vpc(CidrBlock=vpc_cidr)
            vpc_id   = vpc_resp["Vpc"]["VpcId"]
            ec2.create_tags(Resources=[vpc_id],
                            Tags=[{"Key": f"{tag_name}", "Value": f"{tag_name_value}-vpc"}, {"Key": f"{tag_project}", "Value": f"{tag_project_value}"}])

            wait_until_state(
                lambda: ec2.describe_vpcs(VpcIds=[vpc_id]),
                "Vpcs",
                "available"
            )
            rtb_id = find_rtbs_from_vpc(vpc_id)
            # Create tag for rtb
            ec2.create_tags(Resources=[rtb_id],
                            Tags=[{"Key": f"{tag_name}", "Value": f"{tag_name_value}-rtb"}, {"Key": f"{tag_project}", "Value": f"{tag_project_value}"}])

            # Create subnet
            subnet_resp = ec2.create_subnet(VpcId=vpc_id, CidrBlock=subnet_cidr, AvailabilityZone=az)
            subnet_id   = subnet_resp["Subnet"]["SubnetId"]
            ec2.create_tags(Resources=[subnet_id],
                            Tags=[{"Key": f"{tag_name}", "Value": f"{tag_name_value}-sub"}, {"Key": f"{tag_project}", "Value": f"{tag_project_value}"}])

            wait_until_state(
                lambda: ec2.describe_subnets(SubnetIds=[subnet_id]),
                "Subnets",
                "available"
            )

            # flip the subnet-wide "auto-assign public IPv4" switch
            ec2.modify_subnet_attribute(
                SubnetId=subnet_id,
                MapPublicIpOnLaunch={"Value": True}
            )

            # Create and attach the IGW ----------
            igw_resp = ec2.create_internet_gateway()
            igw_id   = igw_resp["InternetGateway"]["InternetGatewayId"]     # igw-...

            # Create tag for internet gateway
            ec2.create_tags(Resources=[igw_id],
                            Tags=[{"Key": f"{tag_name}", "Value": f"{tag_name_value}-igw"}, {"Key": f"{tag_project}", "Value": f"{tag_project_value}"}])

            ec2.attach_internet_gateway(             # hooks IGW to the VPC
                InternetGatewayId=igw_id,
                VpcId=vpc_id
            )

            # Associate the route table with a subnet ----------
            ec2.associate_route_table(
                RouteTableId=rtb_id,
                SubnetId=subnet_id
            )
            ec2.create_route(
                RouteTableId=rtb_id,
                DestinationCidrBlock="0.0.0.0/0",
                GatewayId=igw_id
            )

            # Turn on DNS support  (must be on *before* hostnames)
            ec2.modify_vpc_attribute(
                VpcId=vpc_id,
                EnableDnsSupport={"Value": True}
            )

            # Turn on DNS hostnames (public DNS names for instances) only work if EnableDnsSupport: True
            ec2.modify_vpc_attribute(
                VpcId=vpc_id,
                EnableDnsHostnames={"Value": True}
            )


            # CREATE SECURITY GROUP ────────────────────────────────────
            sg_resp = ec2.create_security_group(
                GroupName=f"{tag_name_value}-sg",
                Description="sg from Lambda-created instance",
                VpcId=vpc_id,
            )
            sg_id = sg_resp["GroupId"]

            # Tag so the teardown Lambda can find it
            ec2.create_tags(Resources=[sg_id],
                            Tags=[{"Key": f"{tag_name}", "Value": f"{tag_name_value}-sg"}, {"Key": f"{tag_project}", "Value": f"{tag_project_value}"}])

            if ingress_rules and isinstance(ingress_rules[0], list):
                ingress_rules = ingress_rules[0]

            # Inbound rules
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=ingress_rules,
            )

            # # Optional: permit all outbound traffic (default anyway, but explicit here)
            # ec2.authorize_security_group_egress(
            #     GroupId=sg_id,
            #     IpPermissions=[
            #         {"IpProtocol": "-1", 
            #         "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            #     ],
            # )

            # Launch EC2 instance
            run_resp = ec2.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                NetworkInterfaces=[{
                    "DeviceIndex": 0,
                    "SubnetId": subnet_id,
                    "AssociatePublicIpAddress": True,     # per-instance request
                    "Groups": [sg_resp["GroupId"]]
                }],
                MaxCount=1,
                MinCount=1,
                Placement={
                    "AvailabilityZone": az
                },
                KeyName=key_pair if key_pair else None,
                TagSpecifications=[{
                    "ResourceType": "instance",
                    "Tags": [{"Key": f"{tag_name}", "Value": f"{tag_name_value}-ec2"}, {"Key": f"{tag_project}", "Value": f"{TAG_PROJECT_VALUE}"}]
                }]
            )
            instance_id = run_resp["Instances"][0]["InstanceId"]

            return {
                "status": "SUCCESS",
                "vpc_id": vpc_id,
                "subnet_id": subnet_id,
                "instance_id": instance_id
            }
        except ClientError as e:
            # In real life you’d add cleanup steps here
            print("AWS error:", e)
            return {"status": "ERROR", "message": str(e)}
        except Exception as e:
            print("General error:", e)
            return {"status": "ERROR", "message": str(e)}

    elif state == "stop":
        instance_id = find_id_by_tag("instance", f"{tag_name_value}-ec2")
        subnet_id   = find_id_by_tag("subnet",   f"{tag_name_value}-sub")
        vpc_id      = find_id_by_tag("vpc",      f"{tag_name_value}-vpc")
        sg_id       = find_id_by_tag("sg",       f"{tag_name_value}-sg")

        try:
            # Terminate instance
            if instance_id:
                print(f"Terminating instance {instance_id}")
                ec2.terminate_instances(InstanceIds=[instance_id])
                wait_for_instance_state(instance_id, "terminated")

            # Delete subnet
            if subnet_id:
                print(f"Deleting subnet {subnet_id}")
                ec2.delete_subnet(SubnetId=subnet_id)

            if sg_id:
                print(f"Deleting security group {sg_id}")
            try:
                ec2.delete_security_group(GroupId=sg_id)
            except ClientError as e:
                # Common edge case: SG is still attached somewhere.
                # Log & re-raise so Step Functions catch can retry or compensate.
                print("Could not delete SG:", e)
                raise

            # Detach + delete any IGWs, then delete VPC
            if vpc_id:
                print(f"Cleaning up VPC {vpc_id}")
                igws = ec2.describe_internet_gateways(
                    Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
                )["InternetGateways"]

                for igw in igws:
                    igw_id = igw["InternetGatewayId"]
                    print(f" Detaching and deleting IGW {igw_id}")
                    ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                    ec2.delete_internet_gateway(InternetGatewayId=igw_id)

                ec2.delete_vpc(VpcId=vpc_id)

            return {
                "status": "SUCCESS",
                "instance_id": instance_id,
                "subnet_id": subnet_id,
                "vpc_id": vpc_id
            }
        except ClientError as e:
            print("AWS error:", e)
            return {"status": "ERROR", "message": str(e)}
        except Exception as e:
            print("General error:", e)
            return {"status": "ERROR", "message": str(e)}

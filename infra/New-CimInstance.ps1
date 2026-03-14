<#
.SYNOPSIS
    Creates a new EC2 instance for a CIM block agent.

.DESCRIPTION
    Launches an EC2 instance in the same VPC, subnet, and security group as the
    existing CIM instances. Uses the same AMI, key pair, and instance type.

.PARAMETER BlockName
    The CIM block to assign to this instance (bitcell, adc, pwm-driver, array, integration).

.PARAMETER InstanceType
    EC2 instance type. Default: c6a.4xlarge (16 vCPU, 32GB RAM).

.EXAMPLE
    .\New-CimInstance.ps1 -BlockName bitcell
    .\New-CimInstance.ps1 -BlockName adc -InstanceType c6a.2xlarge
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("bitcell", "adc", "pwm-driver", "array", "integration")]
    [string]$BlockName,

    [string]$InstanceType = "c6a.4xlarge"
)

# --- Config (from existing infrastructure) ---
$AMI           = "ami-04680790a315cd58d"       # Ubuntu 22.04 x86_64
$KeyName       = "schemato-key"
$SecurityGroup = "sg-07e91a82a4f7678aa"        # sky130-cim-sg
$SubnetId      = "subnet-0ae32ef43f5322974"
$Region        = "us-east-1"
$VolumeSize    = 50                             # GB

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Creating CIM Instance: $BlockName" -ForegroundColor Cyan
Write-Host "  Type: $InstanceType" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Launch instance ---
Write-Host "Launching EC2 instance..." -ForegroundColor Yellow

$InstanceId = aws ec2 run-instances `
    --region $Region `
    --image-id $AMI `
    --instance-type $InstanceType `
    --key-name $KeyName `
    --security-group-ids $SecurityGroup `
    --subnet-id $SubnetId `
    --associate-public-ip-address `
    --block-device-mappings "[{`"DeviceName`":`"/dev/sda1`",`"Ebs`":{`"VolumeSize`":$VolumeSize,`"VolumeType`":`"gp3`",`"Iops`":3000,`"Throughput`":250}}]" `
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=sky130-cim-$BlockName},{Key=Project,Value=sky130-cim},{Key=Block,Value=$BlockName}]" `
    --query 'Instances[0].InstanceId' `
    --output text

if (-not $InstanceId) {
    Write-Host "ERROR: Failed to launch instance!" -ForegroundColor Red
    exit 1
}

Write-Host "Instance ID: $InstanceId" -ForegroundColor Green

# --- Wait for running ---
Write-Host "Waiting for instance to be running..." -ForegroundColor Yellow
aws ec2 wait instance-running --region $Region --instance-ids $InstanceId

# --- Get public IP ---
$PublicIp = aws ec2 describe-instances `
    --region $Region `
    --instance-ids $InstanceId `
    --query 'Reservations[0].Instances[0].PublicIpAddress' `
    --output text

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  INSTANCE READY" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Block:       $BlockName"
Write-Host "  Instance ID: $InstanceId"
Write-Host "  Public IP:   $PublicIp"
Write-Host "  Type:        $InstanceType"
Write-Host ""
Write-Host "  SSH:" -ForegroundColor Cyan
Write-Host "    ssh -i ~/.ssh/schemato-key.pem ubuntu@$PublicIp"
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. Wait ~30 seconds for SSH to be ready"
Write-Host "    2. Run: .\Setup-CimInstance.ps1 -Ip $PublicIp -BlockName $BlockName"
Write-Host "    3. Run: .\Start-CimAgent.ps1 -Ip $PublicIp -BlockName $BlockName"
Write-Host ""

# --- Save instance info ---
$InfoFile = "instance-$BlockName.json"
@{
    BlockName    = $BlockName
    InstanceId   = $InstanceId
    PublicIp     = $PublicIp
    InstanceType = $InstanceType
    CreatedAt    = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
} | ConvertTo-Json | Out-File -FilePath $InfoFile -Encoding UTF8

Write-Host "  Instance info saved to: $InfoFile" -ForegroundColor Gray
Write-Host ""

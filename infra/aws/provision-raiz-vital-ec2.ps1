param(
    [string]$Profile = "raiz-vital",
    [string]$Region = "sa-east-1",
    [string]$Project = "raiz-vital-zwaf",
    [string]$InstanceType = "t3.medium",
    [int]$VolumeSizeGb = 40,
    [string]$KeyDir = "$env:USERPROFILE\.ssh",
    [switch]$ConfirmProvision
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmProvision) {
    throw "Refusing to create billable AWS resources without -ConfirmProvision."
}

$awsCommand = Get-Command aws -ErrorAction SilentlyContinue
$Aws = if ($awsCommand) { $awsCommand.Source } else { "" }
if (-not $Aws) {
    $defaultAws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
    if (Test-Path $defaultAws) {
        $Aws = $defaultAws
    }
}
if (-not $Aws) {
    throw "AWS CLI not found. Install AWS CLI v2 and configure profile '$Profile'."
}

function Invoke-Aws {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & $Aws @Args --profile $Profile --region $Region
}

function Invoke-AwsJson {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $raw = Invoke-Aws @Args --output json
    if (-not $raw) { return $null }
    return $raw | ConvertFrom-Json
}

Write-Host "Validating AWS identity for profile '$Profile' in '$Region'..."
$identity = Invoke-AwsJson sts get-caller-identity
Write-Host "Account: $($identity.Account)"
Write-Host "Arn: $($identity.Arn)"

$keyName = "$Project-key"
$keyPath = Join-Path $KeyDir "$keyName.pem"
$sgName = "$Project-sg"
$instanceName = "$Project-app"

New-Item -ItemType Directory -Force -Path $KeyDir | Out-Null

Write-Host "Resolving default VPC..."
$vpcId = Invoke-Aws ec2 describe-vpcs `
    --filters "Name=isDefault,Values=true" `
    --query "Vpcs[0].VpcId" `
    --output text
if (-not $vpcId -or $vpcId -eq "None") {
    throw "No default VPC found in $Region. Create/select a VPC before provisioning."
}

$subnetId = Invoke-Aws ec2 describe-subnets `
    --filters "Name=vpc-id,Values=$vpcId" "Name=default-for-az,Values=true" `
    --query "Subnets[0].SubnetId" `
    --output text
if (-not $subnetId -or $subnetId -eq "None") {
    throw "No default subnet found in VPC $vpcId."
}

Write-Host "VPC: $vpcId"
Write-Host "Subnet: $subnetId"

Write-Host "Resolving current public IP for SSH ingress..."
$myIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com").Trim()
$sshCidr = "$myIp/32"
Write-Host "SSH CIDR: $sshCidr"

Write-Host "Ensuring EC2 key pair '$keyName'..."
$existingKey = Invoke-Aws ec2 describe-key-pairs `
    --filters "Name=key-name,Values=$keyName" `
    --query "KeyPairs[0].KeyName" `
    --output text
if (-not $existingKey -or $existingKey -eq "None") {
    $keyMaterial = Invoke-Aws ec2 create-key-pair `
        --key-name $keyName `
        --key-type rsa `
        --query "KeyMaterial" `
        --output text
    [System.IO.File]::WriteAllText(
        $keyPath,
        (($keyMaterial -join "`n") + "`n"),
        [System.Text.Encoding]::ASCII
    )
    icacls $keyPath /inheritance:r | Out-Null
    icacls $keyPath /grant:r "$env:USERNAME`:R" | Out-Null
    Write-Host "Created key pair and saved private key to $keyPath"
} else {
    if (-not (Test-Path $keyPath)) {
        throw "Key pair '$keyName' exists in AWS, but local private key is missing at $keyPath."
    }
    Write-Host "Key pair already exists and local key file is present."
}

Write-Host "Ensuring security group '$sgName'..."
$sgId = Invoke-Aws ec2 describe-security-groups `
    --filters "Name=group-name,Values=$sgName" "Name=vpc-id,Values=$vpcId" `
    --query "SecurityGroups[0].GroupId" `
    --output text 2>$null
if ($LASTEXITCODE -ne 0 -or -not $sgId -or $sgId -eq "None") {
    $sgId = Invoke-Aws ec2 create-security-group `
        --group-name $sgName `
        --description "Raiz Vital ZWAF public ingress for SSH/HTTP/HTTPS" `
        --vpc-id $vpcId `
        --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$sgName},{Key=Project,Value=$Project}]" `
        --query "GroupId" `
        --output text
    Write-Host "Created security group: $sgId"
} else {
    Write-Host "Security group already exists: $sgId"
}

$rules = @(
    @{ Port = 22; Cidr = $sshCidr; Description = "SSH from admin IP" },
    @{ Port = 80; Cidr = "0.0.0.0/0"; Description = "HTTP public" },
    @{ Port = 443; Cidr = "0.0.0.0/0"; Description = "HTTPS public" }
)

foreach ($rule in $rules) {
    $sg = Invoke-AwsJson ec2 describe-security-groups --group-ids $sgId
    $existingRule = $sg.SecurityGroups[0].IpPermissions | Where-Object {
        $_.IpProtocol -eq "tcp" -and
        $_.FromPort -eq $rule.Port -and
        $_.ToPort -eq $rule.Port -and
        ($_.IpRanges | Where-Object { $_.CidrIp -eq $rule.Cidr })
    }

    if ($existingRule) {
        Write-Host "Ingress already exists for tcp/$($rule.Port) from $($rule.Cidr)."
    } else {
        Write-Host "Authorizing ingress tcp/$($rule.Port) from $($rule.Cidr)..."
        Invoke-Aws ec2 authorize-security-group-ingress `
            --group-id $sgId `
            --ip-permissions "IpProtocol=tcp,FromPort=$($rule.Port),ToPort=$($rule.Port),IpRanges=[{CidrIp=$($rule.Cidr),Description='$($rule.Description)'}]" `
            --output text | Out-Null
    }
}

Write-Host "Resolving latest Ubuntu 24.04 LTS AMI..."
$amiId = Invoke-Aws ec2 describe-images `
    --owners "099720109477" `
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" "Name=architecture,Values=x86_64" "Name=state,Values=available" `
    --query "sort_by(Images, &CreationDate)[-1].ImageId" `
    --output text
if (-not $amiId -or $amiId -eq "None") {
    throw "Could not resolve Ubuntu 24.04 AMI through EC2 describe-images."
}
Write-Host "AMI: $amiId"

Write-Host "Checking for existing running instance named '$instanceName'..."
$existingInstanceId = Invoke-Aws ec2 describe-instances `
    --filters "Name=tag:Name,Values=$instanceName" "Name=instance-state-name,Values=pending,running,stopping,stopped" `
    --query "Reservations[0].Instances[0].InstanceId" `
    --output text 2>$null
if ($LASTEXITCODE -eq 0 -and $existingInstanceId -and $existingInstanceId -ne "None") {
    $instanceId = $existingInstanceId
    Write-Host "Reusing existing instance: $instanceId"
} else {
    Write-Host "Launching EC2 instance '$instanceName'..."
    $instanceId = Invoke-Aws ec2 run-instances `
        --image-id $amiId `
        --instance-type $InstanceType `
        --key-name $keyName `
        --security-group-ids $sgId `
        --subnet-id $subnetId `
        --associate-public-ip-address `
        --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$VolumeSizeGb,VolumeType=gp3,Encrypted=true,DeleteOnTermination=false}" `
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$instanceName},{Key=Project,Value=$Project}]" "ResourceType=volume,Tags=[{Key=Name,Value=$instanceName-root},{Key=Project,Value=$Project}]" `
        --query "Instances[0].InstanceId" `
        --output text
}

Write-Host "Waiting for instance to run: $instanceId"
Invoke-Aws ec2 wait instance-running --instance-ids $instanceId

Write-Host "Ensuring Elastic IP..."
$allocationId = Invoke-Aws ec2 describe-addresses `
    --filters "Name=tag:Name,Values=$Project-eip" `
    --query "Addresses[0].AllocationId" `
    --output text 2>$null
if ($LASTEXITCODE -ne 0 -or -not $allocationId -or $allocationId -eq "None") {
    $allocation = Invoke-AwsJson ec2 allocate-address `
        --domain vpc `
        --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$Project-eip},{Key=Project,Value=$Project}]"
    $allocationId = $allocation.AllocationId
    Write-Host "Allocated Elastic IP: $($allocation.PublicIp)"
}

$publicIp = Invoke-Aws ec2 describe-addresses `
    --allocation-ids $allocationId `
    --query "Addresses[0].PublicIp" `
    --output text

$associatedInstance = Invoke-Aws ec2 describe-addresses `
    --allocation-ids $allocationId `
    --query "Addresses[0].InstanceId" `
    --output text
if ($associatedInstance -ne $instanceId) {
    Write-Host "Associating Elastic IP $publicIp to $instanceId..."
    Invoke-Aws ec2 associate-address --allocation-id $allocationId --instance-id $instanceId --output text | Out-Null
}

$summary = Invoke-AwsJson ec2 describe-instances --instance-ids $instanceId
$instance = $summary.Reservations[0].Instances[0]

Write-Host ""
Write-Host "Provisioning complete."
Write-Host "InstanceId: $instanceId"
Write-Host "InstanceType: $($instance.InstanceType)"
Write-Host "Region: $Region"
Write-Host "SecurityGroup: $sgId"
Write-Host "ElasticIP: $publicIp"
Write-Host "KeyPath: $keyPath"
Write-Host ""
Write-Host "SSH:"
Write-Host "ssh -i `"$keyPath`" ubuntu@$publicIp"

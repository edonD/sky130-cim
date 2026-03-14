<#
.SYNOPSIS
    Installs all necessary packages on a CIM EC2 instance.

.DESCRIPTION
    SSHes into the instance and installs: ngspice-44 (from source), Python + numpy/scipy/matplotlib,
    Node.js + Claude Code CLI, tmux, git, SKY130 PDK models, and clones the CIM repo.

.PARAMETER Ip
    Public IP of the EC2 instance.

.PARAMETER BlockName
    The CIM block this instance will work on.

.EXAMPLE
    .\Setup-CimInstance.ps1 -Ip 34.204.193.234 -BlockName bitcell
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$Ip,

    [Parameter(Mandatory=$true)]
    [ValidateSet("bitcell", "adc", "pwm-driver", "array", "integration")]
    [string]$BlockName
)

$SshKey = "$env:USERPROFILE\.ssh\schemato-key.pem"
$SshCmd = "ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setting up: $BlockName @ $Ip" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Wait for SSH to be ready ---
Write-Host "Waiting for SSH..." -ForegroundColor Yellow
$retries = 0
while ($retries -lt 30) {
    $result = & ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i $SshKey ubuntu@$Ip "echo OK" 2>&1
    if ($result -match "OK") { break }
    Start-Sleep -Seconds 5
    $retries++
    Write-Host "  Retry $retries..." -ForegroundColor Gray
}
if ($retries -ge 30) {
    Write-Host "ERROR: Cannot reach $Ip after 30 retries" -ForegroundColor Red
    exit 1
}
Write-Host "SSH connected!" -ForegroundColor Green

# --- Upload and run setup script ---
Write-Host "Running full setup (ngspice-44, python, claude, PDK)..." -ForegroundColor Yellow
Write-Host "This takes ~5 minutes. Be patient." -ForegroundColor Gray

$SetupScript = @'
#!/bin/bash
set -ex
export DEBIAN_FRONTEND=noninteractive

# ---- System packages ----
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential libreadline-dev libxaw7-dev libfftw3-dev libedit-dev \
    bison flex autoconf automake libtool \
    python3-pip python3-venv git curl jq tmux htop tree unzip

# ---- Python packages ----
pip3 install numpy pandas matplotlib scipy scikit-optimize 2>/dev/null || \
pip3 install --break-system-packages numpy pandas matplotlib scipy scikit-optimize

# ---- Node.js + Claude Code ----
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
if ! command -v claude &>/dev/null; then
    sudo npm install -g @anthropic-ai/claude-code
fi

# ---- ngspice-44 from source ----
if ! /usr/local/bin/ngspice --version 2>/dev/null | grep -q "ngspice-44"; then
    cd /tmp
    rm -rf ngspice-44-src
    git clone --depth 1 --branch ngspice-44 https://git.code.sf.net/p/ngspice/ngspice ngspice-44-src
    cd ngspice-44-src
    ./autogen.sh 2>&1 | tail -3
    mkdir -p release && cd release
    ../configure --with-x --with-readline=yes --with-fftw3=yes --enable-xspice --enable-cider --enable-openmp --prefix=/usr/local 2>&1 | tail -3
    make -j$(nproc) 2>&1 | tail -3
    sudo make install 2>&1 | tail -3
    rm -rf /tmp/ngspice-44-src
fi

# ---- .spiceinit ----
cat > ~/.spiceinit << 'SINIT'
set ngbehavior=hsa
set skywaterpdk
option noinit
SINIT

# ---- Clone CIM repo ----
mkdir -p ~/workspace
cd ~/workspace
if [ ! -d "sky130-cim" ]; then
    git clone https://github.com/edonD/sky130-cim.git
else
    cd sky130-cim && git fetch origin && git reset --hard origin/master && cd ..
fi

# ---- SKY130 PDK models ----
if [ ! -d "sky130_models" ]; then
    git clone https://github.com/mkghub/skywater130_fd_pr_models.git sky130_models
    cd sky130_models
    mkdir -p sky130_fd_pr_models
    for dir in cells corners parameters parasitics capacitors r+c file_tree; do
        [ -d "$dir" ] && [ ! -d "sky130_fd_pr_models/$dir" ] && cp -r "$dir" sky130_fd_pr_models/
    done
    cp -f *.spice sky130_fd_pr_models/ 2>/dev/null || true
    sed -i 's|^\.include "sky130_fd_pr_models/sonos_|* .include "sky130_fd_pr_models/sonos_|' sky130_fd_pr_models/all.spice 2>/dev/null || true
    cd ..
fi

# ---- Symlink PDK into blocks ----
for bdir in ~/workspace/sky130-cim/blocks/*/; do
    ln -sf ~/workspace/sky130_models "$bdir/sky130_models"
    [ -d ~/workspace/sky130_models/sky130_fd_pr_models ] && ln -sf ~/workspace/sky130_models/sky130_fd_pr_models "$bdir/sky130_fd_pr_models"
    cp -f ~/.spiceinit "$bdir/.spiceinit" 2>/dev/null || true
done

# ---- Git config ----
git config --global user.email "cim-agent@sky130.dev"
git config --global user.name "CIM Design Agent"

# ---- Verify ----
echo ""
echo "======== VERIFICATION ========"
echo "ngspice:    $(/usr/local/bin/ngspice --version 2>&1 | head -1)"
echo "python:     $(python3 --version)"
echo "numpy:      $(python3 -c 'import numpy; print(numpy.__version__)')"
echo "matplotlib: $(python3 -c 'import matplotlib; print(matplotlib.__version__)')"
echo "node:       $(node --version)"
echo "claude:     $(which claude) $(claude --version 2>&1 | head -1)"
echo "tmux:       $(which tmux)"
echo ""

# Quick ngspice + PDK test
cd ~/workspace/sky130-cim/blocks/BLOCK_PLACEHOLDER
echo '* test
.lib "sky130_models/sky130.lib.spice" tt
V1 vdd 0 1.8
XM1 vdd vdd 0 0 sky130_fd_pr__nfet_01v8 W=1u L=0.15u
.control
op
echo "NGSPICE_PDK_OK"
.endc
.end' > /tmp/test.cir
RESULT=$(/usr/local/bin/ngspice -b /tmp/test.cir 2>&1 | grep NGSPICE_PDK_OK)
if [ -n "$RESULT" ]; then
    echo "ngspice + SKY130 PDK: OK"
else
    echo "ngspice + SKY130 PDK: FAILED"
fi
echo ""
echo "======== SETUP COMPLETE ========"
'@

# Replace block placeholder
$SetupScript = $SetupScript -replace 'BLOCK_PLACEHOLDER', $BlockName

# Write to temp file, convert line endings, upload, run
$TempFile = [System.IO.Path]::GetTempFileName()
$SetupScript | Out-File -FilePath $TempFile -Encoding ASCII -NoNewline
# Fix line endings
(Get-Content $TempFile -Raw) -replace "`r`n", "`n" | Set-Content -Path $TempFile -NoNewline

& scp -o StrictHostKeyChecking=no -i $SshKey $TempFile "ubuntu@${Ip}:/home/ubuntu/setup.sh" 2>&1 | Out-Null
Remove-Item $TempFile

# Run setup
& ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip "bash /home/ubuntu/setup.sh"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  SETUP COMPLETE: $BlockName @ $Ip" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next: .\Start-CimAgent.ps1 -Ip $Ip -BlockName $BlockName" -ForegroundColor Yellow
Write-Host ""

#!/bin/bash
# =============================================================================
# CIM Block Agent — EC2 User Data Script
# Installs ngspice, Python, SKY130 PDK, and Claude Code for autonomous design
# =============================================================================
set -ex

export DEBIAN_FRONTEND=noninteractive

echo "=== CIM AGENT SETUP START ===" >> /var/log/userdata.log
date >> /var/log/userdata.log

# --- System dependencies ---
apt-get update -y
apt-get install -y \
    build-essential \
    libreadline-dev \
    ngspice \
    python3-pip \
    python3-venv \
    git \
    curl \
    jq \
    tmux \
    htop

# --- Python packages ---
pip3 install --break-system-packages \
    numpy \
    pandas \
    matplotlib \
    scipy \
    scikit-optimize \
    requests \
    fastapi \
    uvicorn

# --- Node.js (for Claude Code) ---
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# --- Claude Code CLI ---
npm install -g @anthropic-ai/claude-code

# --- Create workspace ---
mkdir -p /home/ubuntu/workspace
chown ubuntu:ubuntu /home/ubuntu/workspace

# --- Clone the CIM repo ---
# NOTE: Replace with your actual repo URL
# git clone https://github.com/YOUR_USER/sky130-cim.git /home/ubuntu/workspace/sky130-cim

# --- Setup ngspice config ---
cat > /home/ubuntu/.spiceinit << 'SPICEINIT'
set ngbehavior=hsa
set skywaterpdk
option noinit
SPICEINIT
chown ubuntu:ubuntu /home/ubuntu/.spiceinit

# --- Clone SKY130 PDK models (shared across blocks) ---
cd /home/ubuntu/workspace
if [ ! -d "sky130_models" ]; then
    git clone https://github.com/mkghub/skywater130_fd_pr_models.git sky130_models
    cd sky130_models
    mkdir -p sky130_fd_pr_models
    for dir in cells corners parameters parasitics capacitors r+c file_tree; do
        if [ -d "$dir" ] && [ ! -d "sky130_fd_pr_models/$dir" ]; then
            cp -r "$dir" sky130_fd_pr_models/
        fi
    done
    cp -f *.spice sky130_fd_pr_models/ 2>/dev/null || true
    # Comment out flash models not in this lightweight repo
    sed -i 's|^\.include "sky130_fd_pr_models/sonos_|* .include "sky130_fd_pr_models/sonos_|' sky130_fd_pr_models/all.spice
    cd ..
fi

# --- Create launch script for each block ---
cat > /home/ubuntu/launch_agent.sh << 'LAUNCH'
#!/bin/bash
# Usage: ./launch_agent.sh <block_name>
# Example: ./launch_agent.sh bitcell
# Example: ./launch_agent.sh adc

BLOCK=$1
if [ -z "$BLOCK" ]; then
    echo "Usage: $0 <block_name>"
    echo "Blocks: bitcell, adc, pwm-driver, array, integration"
    exit 1
fi

WORKSPACE="/home/ubuntu/workspace"
BLOCK_DIR="$WORKSPACE/sky130-cim/blocks/$BLOCK"

if [ ! -d "$BLOCK_DIR" ]; then
    echo "Block directory not found: $BLOCK_DIR"
    echo "Make sure sky130-cim repo is cloned first."
    exit 1
fi

# Symlink shared SKY130 models into the block
ln -sf "$WORKSPACE/sky130_models" "$BLOCK_DIR/sky130_models"
if [ -d "$WORKSPACE/sky130_models/sky130_fd_pr_models" ]; then
    ln -sf "$WORKSPACE/sky130_models/sky130_fd_pr_models" "$BLOCK_DIR/sky130_fd_pr_models"
fi

cd "$BLOCK_DIR"

echo "=== Launching Claude Code agent for block: $BLOCK ==="
echo "Working directory: $(pwd)"
echo "Files:"
ls -la

# Launch Claude Code in headless mode with the block's prompt
claude --dangerously-skip-permissions \
    -p "You are an autonomous analog circuit designer. Read program.md for your full instructions. Read specs.json for targets. Read ../../interfaces.md for interface contracts. Read ../../verification.md for mandatory testbenches and plots. Design the circuit, optimize it, validate it, generate all required plots, update README.md, then commit and push. Do not stop until all specs are met and all testbenches pass."

LAUNCH
chmod +x /home/ubuntu/launch_agent.sh
chown ubuntu:ubuntu /home/ubuntu/launch_agent.sh

# --- Verify installation ---
echo "=== Verification ===" >> /var/log/userdata.log
which ngspice >> /var/log/userdata.log 2>&1
ngspice --version >> /var/log/userdata.log 2>&1
python3 -c "import numpy; print(f'numpy {numpy.__version__}')" >> /var/log/userdata.log 2>&1
which claude >> /var/log/userdata.log 2>&1
node --version >> /var/log/userdata.log 2>&1

# --- Signal ready ---
touch /home/ubuntu/.setup_complete
echo "=== CIM AGENT SETUP COMPLETE ===" >> /var/log/userdata.log
date >> /var/log/userdata.log

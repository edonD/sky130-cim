#!/bin/bash
# =============================================================================
# full_setup.sh — Complete instance setup for CIM agent
# Run this on each EC2 instance to install everything needed
# Usage: bash full_setup.sh <block_name>
#   e.g.: bash full_setup.sh bitcell
# =============================================================================
set -ex

BLOCK=${1:-""}
REPO_URL="https://github.com/edonD/sky130-cim.git"

echo "=========================================="
echo "  CIM AGENT FULL SETUP"
echo "  Block: ${BLOCK:-'(not specified)'}"
echo "  $(date)"
echo "=========================================="

# --- Wait for any existing apt locks to clear ---
while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
    echo "Waiting for apt lock..."
    sleep 5
done

# --- System packages ---
echo ">>> Installing system packages..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    libreadline-dev \
    ngspice \
    python3-pip \
    python3-venv \
    git \
    curl \
    jq \
    tmux \
    htop \
    tree \
    unzip

# --- Python packages ---
echo ">>> Installing Python packages..."
pip3 install \
    numpy \
    pandas \
    matplotlib \
    scipy \
    scikit-optimize \
  || pip3 install --break-system-packages \
    numpy \
    pandas \
    matplotlib \
    scipy \
    scikit-optimize

# --- Node.js 20 ---
echo ">>> Installing Node.js..."
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
echo "Node: $(node --version)"
echo "npm: $(npm --version)"

# --- Claude Code CLI ---
echo ">>> Installing Claude Code..."
if ! command -v claude &>/dev/null; then
    sudo npm install -g @anthropic-ai/claude-code
fi
echo "Claude: $(which claude)"

# --- ngspice config ---
echo ">>> Configuring ngspice..."
cat > ~/.spiceinit << 'EOF'
set ngbehavior=hsa
set skywaterpdk
option noinit
EOF

# --- Clone the CIM repo ---
echo ">>> Cloning CIM repo..."
mkdir -p ~/workspace
cd ~/workspace
if [ ! -d "sky130-cim" ]; then
    git clone "$REPO_URL" sky130-cim
else
    cd sky130-cim && git pull && cd ..
fi

# --- Clone SKY130 PDK models (shared) ---
echo ">>> Setting up SKY130 PDK models..."
cd ~/workspace
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
    sed -i 's|^\.include "sky130_fd_pr_models/sonos_|* .include "sky130_fd_pr_models/sonos_|' sky130_fd_pr_models/all.spice 2>/dev/null || true
    cd ..
fi

# --- Symlink PDK into all blocks ---
echo ">>> Symlinking PDK into blocks..."
for block_dir in ~/workspace/sky130-cim/blocks/*/; do
    block_name=$(basename "$block_dir")
    ln -sf ~/workspace/sky130_models "$block_dir/sky130_models"
    if [ -d ~/workspace/sky130_models/sky130_fd_pr_models ]; then
        ln -sf ~/workspace/sky130_models/sky130_fd_pr_models "$block_dir/sky130_fd_pr_models"
    fi
    # Also copy .spiceinit into each block
    cp -f ~/.spiceinit "$block_dir/.spiceinit" 2>/dev/null || true
    echo "  Linked PDK into $block_name"
done

# --- Git config for agent commits ---
echo ">>> Configuring git..."
git config --global user.email "cim-agent@sky130.dev"
git config --global user.name "CIM Design Agent"

# --- Create the launch script ---
cat > ~/launch_agent.sh << 'LAUNCH'
#!/bin/bash
BLOCK=$1
if [ -z "$BLOCK" ]; then
    echo "Usage: ./launch_agent.sh <block_name>"
    echo "Blocks: bitcell, adc, pwm-driver, array, integration"
    exit 1
fi

BLOCK_DIR="$HOME/workspace/sky130-cim/blocks/$BLOCK"
if [ ! -d "$BLOCK_DIR" ]; then
    echo "Block not found: $BLOCK_DIR"
    exit 1
fi

cd "$BLOCK_DIR"
echo "=== Launching Claude Code for: $BLOCK ==="
echo "Directory: $(pwd)"
echo "Files:"
ls -la
echo ""

claude --dangerously-skip-permissions \
    -p "You are an autonomous analog circuit designer. Read program.md for your full instructions. Read specs.json for targets. Read ../../interfaces.md for interface contracts. Read ../../verification.md for mandatory testbenches and plots. Design the circuit, optimize it, validate it, generate all required plots, update README.md, then commit and push. Do not stop until all specs are met and all testbenches pass."
LAUNCH
chmod +x ~/launch_agent.sh

# --- Verify everything ---
echo ""
echo "=========================================="
echo "  VERIFICATION"
echo "=========================================="
echo "ngspice:   $(which ngspice) — $(ngspice --version 2>&1 | head -1)"
echo "python3:   $(python3 --version)"
echo "numpy:     $(python3 -c 'import numpy; print(numpy.__version__)')"
echo "matplotlib:$(python3 -c 'import matplotlib; print(matplotlib.__version__)')"
echo "node:      $(node --version)"
echo "claude:    $(which claude)"
echo "git:       $(git --version)"
echo "tmux:      $(which tmux)"
echo ""
echo "Repo:      ~/workspace/sky130-cim"
echo "PDK:       ~/workspace/sky130_models"
echo ""

# Test ngspice with a trivial circuit
echo ">>> Testing ngspice..."
cat > /tmp/test_ngspice.cir << 'SPICE'
* Quick test
.lib "sky130_models/sky130.lib.spice" tt
V1 vdd 0 1.8
R1 vdd out 1k
XM1 out out 0 0 sky130_fd_pr__nfet_01v8 W=1u L=0.15u
.control
op
echo "NGSPICE_OK"
.endc
.end
SPICE

cd ~/workspace
ngspice_out=$(ngspice -b /tmp/test_ngspice.cir 2>&1)
if echo "$ngspice_out" | grep -q "NGSPICE_OK"; then
    echo "ngspice + SKY130: WORKING"
else
    echo "ngspice test FAILED:"
    echo "$ngspice_out" | tail -10
fi

echo ""
echo "=========================================="
echo "  SETUP COMPLETE"
if [ -n "$BLOCK" ]; then
    echo "  Ready to run: ./launch_agent.sh $BLOCK"
fi
echo "  $(date)"
echo "=========================================="

#!/bin/bash
# ============================================================
# start.sh — Launch the autonomous CIM design agent
# Run this from inside the block directory:
#   cd /home/ubuntu/workspace/sky130-cim/blocks/<block>
#   bash start.sh
#
# The agent runs in a tmux session that persists after SSH disconnect.
# To check:  tmux attach -t <block>
# To detach: Ctrl+B, D
# To stop:   tmux kill-session -t <block>
# ============================================================

BLOCK=$(basename "$(pwd)")
BLOCK_DIR="$(pwd)"

echo ""
echo "================================================"
echo "  CIM Agent: $BLOCK"
echo "  Directory: $BLOCK_DIR"
echo "  $(date)"
echo "================================================"
echo ""

# Verify we're in the right place
if [ ! -f program.md ] || [ ! -f specs.json ]; then
    echo "ERROR: program.md or specs.json not found."
    echo "Make sure you're in a block directory."
    exit 1
fi

# Verify tools
echo "Checking tools..."
for tool in ngspice python3 claude tmux git; do
    if command -v $tool &>/dev/null; then
        echo "  $tool: OK"
    else
        echo "  $tool: MISSING!"
        exit 1
    fi
done

# Verify PDK
if [ -f sky130_models/sky130.lib.spice ]; then
    echo "  SKY130 PDK: OK"
else
    echo "  SKY130 PDK: MISSING! Run setup.sh first."
    exit 1
fi
echo ""

# Kill existing session if any
tmux kill-session -t "$BLOCK" 2>/dev/null

# Create the agent runner script
cat > /tmp/run_${BLOCK}.sh << 'AGENT'
#!/bin/bash
cd BLOCK_DIR_PLACEHOLDER

claude --dangerously-skip-permissions \
    -p "You are an autonomous analog circuit designer. You will work indefinitely until manually stopped.

SETUP:
1. Read program.md — it contains your full instructions, the experiment loop, and design freedom.
2. Read specs.json — these are your pass/fail targets. You cannot edit this file.
3. Read ../../interfaces.md — signal contracts with other blocks.
4. Read ../../verification.md — mandatory testbenches and plots you must produce.
5. Check design.cir, parameters.csv, evaluate.py for current state.

THEN: Begin the autonomous experiment loop as described in program.md.
- Phase A: Meet all specs (score = 1.0).
- Phase B: Deep verification, waveform analysis, margin improvement, all plots.
- NEVER STOP. Loop forever. Do not ask for permission. The human is away.
- Search the web for papers, techniques, SKY130 examples. pip install anything you need.
- README.md is your progress dashboard — update it after every improvement with plots and analysis.
- Commit and push after every keeper so progress is saved."
AGENT

sed -i "s|BLOCK_DIR_PLACEHOLDER|$BLOCK_DIR|g" /tmp/run_${BLOCK}.sh
chmod +x /tmp/run_${BLOCK}.sh

# Start in detached tmux
tmux new-session -d -s "$BLOCK" "bash /tmp/run_${BLOCK}.sh"

sleep 1
echo "================================================"
echo "  AGENT STARTED: $BLOCK"
echo "================================================"
echo ""
tmux ls
echo ""
echo "  To watch:   tmux attach -t $BLOCK"
echo "  To detach:  Ctrl+B, D"
echo "  To stop:    tmux kill-session -t $BLOCK"
echo "  To check:   cat README.md"
echo ""

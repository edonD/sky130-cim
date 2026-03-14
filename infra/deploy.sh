#!/bin/bash
# =============================================================================
# deploy.sh — Launch CIM design instances
# =============================================================================
# Usage:
#   ./deploy.sh              # Deploy Phase 1 (3 instances: bitcell, adc, pwm)
#   ./deploy.sh phase2       # Deploy Phase 2 (1 instance: array)
#   ./deploy.sh phase3       # Deploy Phase 3 (1 instance: integration)
#   ./deploy.sh destroy      # Tear down all instances
#   ./deploy.sh status       # Show instance IPs and SSH commands
# =============================================================================

set -e
cd "$(dirname "$0")"

PHASE=${1:-phase1}

case $PHASE in
    phase1)
        echo "=== Deploying Phase 1: bitcell + adc + pwm-driver (3 parallel instances) ==="
        cat > terraform.tfvars << 'EOF'
key_name       = "schemato-key"
instance_type  = "c6a.4xlarge"
instance_count = 3
block_names    = ["bitcell", "adc", "pwm-driver"]
EOF
        terraform init
        terraform apply -auto-approve
        echo ""
        echo "=== INSTANCES READY ==="
        terraform output -json | python3 -c "
import json, sys
data = json.load(sys.stdin)
print()
print('SSH into each instance and run:')
print()
for block, cmd in data.get('ssh_commands', {}).get('value', {}).items():
    print(f'  [{block}]')
    print(f'    {cmd}')
    print(f'    # Then: ./launch_agent.sh {block}')
    print()
"
        ;;

    phase2)
        echo "=== Deploying Phase 2: array (1 instance) ==="
        cat > terraform.tfvars << 'EOF'
key_name       = "schemato-key"
instance_type  = "c6a.4xlarge"
instance_count = 1
block_names    = ["array"]
EOF
        terraform apply -auto-approve
        terraform output ssh_commands
        ;;

    phase3)
        echo "=== Deploying Phase 3: integration (1 instance) ==="
        cat > terraform.tfvars << 'EOF'
key_name       = "schemato-key"
instance_type  = "c6a.4xlarge"
instance_count = 1
block_names    = ["integration"]
EOF
        terraform apply -auto-approve
        terraform output ssh_commands
        ;;

    destroy)
        echo "=== Destroying all instances ==="
        terraform destroy -auto-approve
        ;;

    status)
        echo "=== Current instances ==="
        terraform output -json 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
ips = data.get('instance_ips', {}).get('value', {})
cmds = data.get('ssh_commands', {}).get('value', {})
ids = data.get('instance_ids', {}).get('value', {})
if not ips:
    print('  No instances running.')
else:
    for block in ips:
        print(f'  {block:15s}  IP: {ips[block]:20s}  ID: {ids.get(block, \"\")}')
        print(f'                   {cmds.get(block, \"\")}')
        print()
" 2>/dev/null || echo "  No state found. Run ./deploy.sh first."
        ;;

    *)
        echo "Usage: $0 [phase1|phase2|phase3|destroy|status]"
        exit 1
        ;;
esac

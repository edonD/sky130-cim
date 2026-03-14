<#
.SYNOPSIS
    Starts the autonomous CIM design agent in a detached tmux session.

.DESCRIPTION
    SSHes into the instance, creates a tmux session named after the block,
    and launches Claude Code with --dangerously-skip-permissions in autonomous mode.
    The agent reads program.md and loops forever until manually stopped.

    After running this, the agent works in the background even if you close SSH.
    To check progress: ssh in and run `tmux attach -t <BlockName>`
    To detach without stopping: press Ctrl+B, then D

.PARAMETER Ip
    Public IP of the EC2 instance.

.PARAMETER BlockName
    The CIM block to run the agent on.

.PARAMETER Model
    Claude model to use. Default: sonnet (fastest). Use "opus" for harder blocks.

.EXAMPLE
    .\Start-CimAgent.ps1 -Ip 34.204.193.234 -BlockName bitcell
    .\Start-CimAgent.ps1 -Ip 100.56.9.255 -BlockName adc -Model opus
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$Ip,

    [Parameter(Mandatory=$true)]
    [ValidateSet("bitcell", "adc", "pwm-driver", "array", "integration")]
    [string]$BlockName,

    [string]$Model = ""
)

$SshKey = "$env:USERPROFILE\.ssh\schemato-key.pem"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting Agent: $BlockName @ $Ip" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Build the model flag
$ModelFlag = ""
if ($Model -ne "") {
    $ModelFlag = "--model $Model"
}

# Build the prompt
$Prompt = @"
You are an autonomous analog circuit designer. You will work indefinitely until manually stopped.

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
- Commit and push after every keeper so progress is saved.
"@

# Create the launch script on the remote instance
$LaunchScript = @"
#!/bin/bash
cd /home/ubuntu/workspace/sky130-cim/blocks/$BlockName
echo '================================================'
echo '  CIM AGENT: $BlockName'
echo '  Started: '`date`
echo '  Model: ${Model:-default}'
echo '================================================'
echo ''
claude --dangerously-skip-permissions $ModelFlag -p '$($Prompt -replace "'", "'\''")'
"@

# Write launch script, fix line endings, upload
$TempFile = [System.IO.Path]::GetTempFileName()
$LaunchScript | Out-File -FilePath $TempFile -Encoding ASCII -NoNewline
(Get-Content $TempFile -Raw) -replace "`r`n", "`n" | Set-Content -Path $TempFile -NoNewline

& scp -o StrictHostKeyChecking=no -i $SshKey $TempFile "ubuntu@${Ip}:/home/ubuntu/run_${BlockName}.sh" 2>&1 | Out-Null
& ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip "chmod +x /home/ubuntu/run_${BlockName}.sh" 2>&1 | Out-Null
Remove-Item $TempFile

# Kill existing tmux session if any
& ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip "tmux kill-session -t $BlockName 2>/dev/null; echo ok" 2>&1 | Out-Null

# Start detached tmux session
& ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip "tmux new-session -d -s $BlockName 'bash /home/ubuntu/run_${BlockName}.sh'" 2>&1

# Verify it's running
Start-Sleep -Seconds 2
$TmuxCheck = & ssh -o StrictHostKeyChecking=no -i $SshKey ubuntu@$Ip "tmux ls 2>&1"

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  AGENT LAUNCHED: $BlockName" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  tmux sessions:" -ForegroundColor Gray
Write-Host "  $TmuxCheck" -ForegroundColor Gray
Write-Host ""
Write-Host "  The agent is now running autonomously." -ForegroundColor Yellow
Write-Host "  It will loop forever until you stop it." -ForegroundColor Yellow
Write-Host ""
Write-Host "  To check progress:" -ForegroundColor Cyan
Write-Host "    ssh -i ~/.ssh/schemato-key.pem ubuntu@$Ip"
Write-Host "    tmux attach -t $BlockName"
Write-Host "    # Ctrl+B, D to detach without stopping"
Write-Host ""
Write-Host "  To check README (quick progress view):" -ForegroundColor Cyan
Write-Host "    ssh -i ~/.ssh/schemato-key.pem ubuntu@$Ip 'cat ~/workspace/sky130-cim/blocks/$BlockName/README.md'"
Write-Host ""
Write-Host "  To stop the agent:" -ForegroundColor Cyan
Write-Host "    ssh -i ~/.ssh/schemato-key.pem ubuntu@$Ip 'tmux kill-session -t $BlockName'"
Write-Host ""

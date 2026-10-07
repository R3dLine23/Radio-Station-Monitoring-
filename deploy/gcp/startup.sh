#!/bin/bash
# Compute Engine startup script: runs as root on every boot, so the VM
# installs the monitor on first boot and pulls the latest code on each reboot.
curl -fsSL https://raw.githubusercontent.com/R3dLine23/Radio-Station-Monitoring-/main/deploy/gcp/install.sh | bash

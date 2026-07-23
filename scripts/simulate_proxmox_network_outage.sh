#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: simulate_proxmox_network_outage.sh VMID [NIC] [DURATION]

Temporarily disconnect a running Proxmox VM's virtual NIC through the QEMU
monitor, then reconnect it. Run this on the Proxmox node hosting the VM.

Arguments:
  VMID      Numeric Proxmox VM ID
  NIC       QEMU NIC ID (default: net0)
  DURATION  Outage duration in seconds, strictly between 5 and 10 (default: 7)

Environment variables:
  PVE_NODE  Proxmox node name (default: output of hostname -s)

Examples:
  ./simulate_proxmox_network_outage.sh 123
  ./simulate_proxmox_network_outage.sh 123 net1 7.5
EOF
}

if (( $# < 1 || $# > 3 )) || [[ ${1:-} == '-h' || ${1:-} == '--help' ]]; then
  usage
  if [[ ${1:-} == '-h' || ${1:-} == '--help' ]]; then
    exit 0
  fi
  exit 2
fi

vmid=$1
nic=${2:-net0}
duration=${3:-7}
node=${PVE_NODE:-$(hostname -s)}
reconnect_needed=0

if [[ ! $vmid =~ ^[0-9]+$ ]]; then
  echo "ERROR: VMID must be numeric: $vmid" >&2
  exit 2
fi

if [[ ! $nic =~ ^net[0-9]+$ ]]; then
  echo "ERROR: NIC must look like net0, net1, etc.: $nic" >&2
  exit 2
fi

if [[ ! $duration =~ ^[0-9]+([.][0-9]+)?$ ]] ||
  ! awk -v duration="$duration" \
    'BEGIN { exit !(duration > 5 && duration < 10) }'; then
  echo "ERROR: DURATION must be greater than 5 and less than 10: $duration" >&2
  exit 2
fi

for command in awk grep hostname pvesh qm sleep; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $command" >&2
    exit 2
  fi
done

if [[ $(qm status "$vmid") != 'status: running' ]]; then
  echo "ERROR: VM $vmid is not running on node $node" >&2
  exit 2
fi

vm_config=$(qm config "$vmid")
if ! grep -q "^${nic}:" <<<"$vm_config"; then
  echo "ERROR: VM $vmid does not have a configured $nic interface" >&2
  exit 2
fi

monitor() {
  local command=$1

  pvesh create "/nodes/$node/qemu/$vmid/monitor" \
    --command "$command" >/dev/null
}

reconnect() {
  if (( reconnect_needed == 0 )); then
    return
  fi

  echo "Reconnecting VM $vmid $nic..."
  if monitor "set_link $nic on"; then
    reconnect_needed=0
    echo "VM $vmid $nic reconnected."
  else
    echo "ERROR: failed to reconnect VM $vmid $nic" >&2
  fi
}

trap reconnect EXIT
trap 'exit 130' INT TERM

echo "Disconnecting VM $vmid $nic for $duration seconds..."
reconnect_needed=1
monitor "set_link $nic off"
sleep "$duration"
reconnect

if (( reconnect_needed != 0 )); then
  exit 1
fi

trap - EXIT INT TERM

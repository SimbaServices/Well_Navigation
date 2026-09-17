#!/usr/bin/env bash
# Apply Remote-SSH + Dev Container host settings on a box already
# provisioned from an older cloud-init.yaml. Run as wellnav:
#   sudo bash deploy/enable-remote-dev.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "re-run as root: sudo bash $0" >&2
  exit 1
fi

install -d -m 0755 -o wellnav -g wellnav /home/wellnav/Well_Navigation

cat >/etc/ssh/sshd_config.d/ssh-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
Port 2222
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
MaxAuthTries 3
AllowTcpForwarding yes
AllowAgentForwarding yes
X11Forwarding no
ClientAliveInterval 60
ClientAliveCountMax 3
AuthorizedKeysFile .ssh/authorized_keys
AllowUsers wellnav
EOF

if [[ ! -f /home/wellnav/.gitconfig ]]; then
  cat >/home/wellnav/.gitconfig <<'EOF'
[user]
	name = Sam Parker
	email = samwayneparker@gmail.com
EOF
  chown wellnav:wellnav /home/wellnav/.gitconfig
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl wget ca-certificates rsync unzip docker.io docker-compose-v2

systemctl enable --now docker
usermod -aG docker wellnav || true

if systemctl reload ssh; then
  :
elif systemctl reload sshd; then
  :
else
  echo "sshd reload failed" >&2
  exit 1
fi

echo "Remote-SSH forwarding is on. Connect as wellnav on port 2222, open ~/Well_Navigation, then Reopen in Container."

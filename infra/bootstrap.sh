#!/usr/bin/env bash
# Toolchain setup for an existing Ubuntu 24.04 server (step 4).
# Run as your regular sudo user, not root:   bash bootstrap.sh
# Safe to re-run. Mirrors the install part of cloud-init.yaml.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your regular user (it uses sudo where needed), not as root." >&2
  exit 1
fi

echo "==> Base packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git tmux curl wget ca-certificates fail2ban unattended-upgrades just
sudo systemctl enable --now fail2ban

echo "==> Apt repos for Docker and GitHub CLI"
sudo install -m 0755 -d /etc/apt/keyrings
arch="$(dpkg --print-architecture)"
codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"

sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo wget -nv -O /etc/apt/keyrings/githubcli-archive-keyring.gpg https://cli.github.com/packages/githubcli-archive-keyring.gpg
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null

echo "==> Docker, Compose plugin, GitHub CLI"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin gh
sudo usermod -aG docker "$USER"

echo "==> uv"
command -v uv > /dev/null || [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

echo "==> Temporal CLI"
[ -x "$HOME/.temporalio/bin/temporal" ] || curl -sSf https://temporal.download/cli.sh | sh
grep -q '.temporalio/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.temporalio/bin:$PATH"' >> "$HOME/.bashrc"

echo "==> Claude Code"
command -v claude > /dev/null || [ -x "$HOME/.local/bin/claude" ] || curl -fsSL https://claude.ai/install.sh | bash

cat <<'EOF'

==> Done. Next:
  1. Log out and back in (for the docker group and PATH).
  2. Check:  docker run --rm hello-world && docker compose version && uv --version && temporal --version && gh --version && just --version && claude --version
  3. Log in: gh auth login && gh auth setup-git
  4. Set:    git config --global user.name "Your Name"; git config --global user.email "you@example.com"
  5. Run:    claude   (log in once; open the link it shows on your Mac)
EOF

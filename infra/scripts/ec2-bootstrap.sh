#!/usr/bin/env bash
# One-time (idempotent) server prep for Amazon Linux 2023 or Ubuntu 22.04+/24.04 on x86_64.
# Run as root:  sudo bash ec2-bootstrap.sh
# Installs Docker + compose plugin, creates /opt/oryn, adds 1 GB swap for the 2 GB instance.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-ec2-user}}"
COMPOSE_VERSION="v2.29.7"
APP_DIR=/opt/oryn

. /etc/os-release

install_docker_amzn() {
  dnf install -y docker
  install_compose_plugin
}

# Amazon Linux has no compose package; fetch the pinned release binary and verify its checksum.
install_compose_plugin() {
  local dir=/usr/local/lib/docker/cli-plugins arch url
  arch="$(uname -m)"
  url="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${arch}"
  mkdir -p "$dir"
  curl -fsSL "$url" -o "$dir/docker-compose"
  echo "$(curl -fsSL "$url.sha256" | cut -d' ' -f1)  $dir/docker-compose" | sha256sum -c -
  chmod +x "$dir/docker-compose"
}

install_docker_ubuntu() {
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
}

if ! docker compose version >/dev/null 2>&1; then
  case "$ID" in
    amzn) install_docker_amzn ;;
    ubuntu) install_docker_ubuntu ;;
    *) echo "unsupported OS: $ID" >&2; exit 1 ;;
  esac
fi
systemctl enable --now docker
usermod -aG docker "$DEPLOY_USER"

mkdir -p "$APP_DIR"
chown "$DEPLOY_USER": "$APP_DIR"

if [[ -z "$(swapon --show --noheadings)" ]]; then
  [[ -f /swapfile ]] || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

cat <<MSG
Bootstrap done. Next, as $DEPLOY_USER (log out/in first so the docker group applies):
  1. echo "ORYN_DOMAIN=<your-domain-or-ip.sslip.io>" > $APP_DIR/.env
  2. create $APP_DIR/backend.env (chmod 600), see docs/context/deployment.md
  3. docker login ghcr.io   (only if the image package is private)
MSG

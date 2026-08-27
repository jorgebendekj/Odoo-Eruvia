#!/bin/bash
set -e

# ==============================================================================
# Script de Inicialización de Droplet (Ubuntu 22.04 / 24.04 LTS) para Odoo Eruvia
# ==============================================================================

echo ">>> [1/5] Actualizando paquetes del sistema..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl wget git ufw htop unzip software-properties-common ca-certificates gnupg

echo ">>> [2/5] Configurando SWAP de 2GB (para estabilidad de memoria)..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap configurado correctamente."
else
    echo "Swap ya existe."
fi

echo ">>> [3/5] Instalando Docker Engine y Docker Compose Plugin..."
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo systemctl enable docker
sudo systemctl start docker

echo ">>> [4/5] Configurando Firewall UFW (Seguridad)..."
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'HTTP Caddy'
sudo ufw allow 443/tcp comment 'HTTPS Caddy'
sudo ufw --force enable

echo ">>> [5/5] Creando directorio de despliegue en /opt/eruvia-odoo..."
sudo mkdir -p /opt/eruvia-odoo
sudo chown -R $USER:$USER /opt/eruvia-odoo

echo "========================================================================"
echo " ¡Servidor preparado exitosamente!"
echo " Próximo paso: Clona tu repositorio en /opt/eruvia-odoo y ejecuta:"
echo "   cd /opt/eruvia-odoo"
echo "   cp .env.example .env && nano .env"
echo "   docker compose up -d"
echo "========================================================================"

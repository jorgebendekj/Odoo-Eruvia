# Eruvia European Business School - Odoo 18 & Integración Claude MCP

Infraestructura de producción para desplegar **Odoo 18 Community** en **DigitalOcean** (vía Docker Compose y Caddy con SSL automático Let's Encrypt) e integración mediante **Model Context Protocol (MCP)** para gestionar el CRM y ERP directamente desde **Claude Desktop / Claude Code**.

---

## 📁 Estructura del Repositorio

```text
.
├── docker-compose.yml          # Definición de Odoo 18, Postgres 16 y Caddy
├── .env.example                # Variables de entorno de referencia
├── config/
│   ├── odoo.conf               # Configuración de Odoo para producción
│   └── Caddyfile               # Proxy inverso con HTTPS automático
├── addons/                     # Módulos personalizados de Odoo
├── mcp-server/                 # Servidor MCP para Claude
│   ├── server.py               # Servidor FastMCP con herramientas de CRM y ERP
│   ├── requirements.txt        # Dependencias de Python
│   ├── claude_desktop_config.example.json # Plantilla de configuración de Claude
│   └── README.md               # Guía de conexión Claude Desktop
├── scripts/
│   ├── init-droplet.sh         # Script para preparar el Droplet de DigitalOcean
│   └── backup.sh               # Respaldo automático de base de datos
└── .github/workflows/
    └── deploy.yml              # Despliegue continuo (CI/CD)
```

---

## 🚀 Guía de Despliegue Paso a Paso

### 1. Crear el Droplet en DigitalOcean
1. Inicia sesión en **DigitalOcean** > **Create Droplet**.
2. **Distribución**: Ubuntu 24.04 LTS x64.
3. **Plan**: Basic Droplet - Regular SSD:
   - Mínimo: **2 GB RAM / 1 vCPU / 50 GB NVMe SSD** ($12/mes).
4. **Autenticación**: Clave SSH (recomendado) o contraseña segura.
5. Copia la **dirección IP pública** de tu nuevo Droplet.

---

### 2. Configurar el Dominio (DNS)
En tu proveedor de dominio (Cloudflare, GoDaddy, Namecheap, etc.):
- Añade un registro **Tipo A**:
  - **Nombre**: `crm` (o `odoo`)
  - **Valor/Destino**: `IP_DE_TU_DROPLET`
  - **TTL**: Automático / 300s

> Tu Odoo quedará accesible en `https://crm.eruvia.com` con SSL gestionado automáticamente por Caddy.

---

### 3. Inicializar el Servidor con Docker
Conéctate por SSH a tu Droplet:

```bash
ssh root@IP_DE_TU_DROPLET
```

Descarga y ejecuta el script de inicialización:

```bash
curl -fsSL https://raw.githubusercontent.com/TU_USUARIO/TU_REPO/main/scripts/init-droplet.sh -o init-droplet.sh
chmod +x init-droplet.sh
./init-droplet.sh
```

---

### 4. Clonar el Repositorio y Levantar Odoo
Dentro del Droplet:

```bash
cd /opt/eruvia-odoo
git clone https://github.com/TU_USUARIO/TU_REPO.git .
cp .env.example .env
nano .env
```

Edita `.env` con tus contraseñas y tu dominio:
```env
DOMAIN_NAME=crm.eruvia.com
POSTGRES_PASSWORD=TuPasswordSeguroPostgres
ADMIN_PASSWORD=TuMasterPasswordOdoo
```

Inicia los contenedores:

```bash
docker compose up -d
```

Verifica el estado con:
```bash
docker compose ps
docker compose logs -f caddy
```

---

### 5. Configurar Odoo en el Navegador
1. Entra a `https://crm.eruvia.com`.
2. Completa el formulario de inicialización de base de datos:
   - **Database Name**: `eruvia`
   - **Email**: `admin@eruvia.com`
   - **Password**: Tu contraseña de administrador
   - **Idioma**: Spanish / Español
   - **País**: España (o el que corresponda)
3. Ve al menú **Aplicaciones** e instala:
   - **CRM** (`crm`)
   - **Contactos** (`contacts`)
   - **Ventas** (`sale_management`)
   - **Facturación** (`account`)

---

### 6. Conectar Claude con Odoo (MCP)

1. En Odoo, ve a tu usuario (esquina superior derecha) > **Preferencias** > **Seguridad de la cuenta** > **Claves de API** y genera una clave.
2. Sigue las instrucciones detalladas en [mcp-server/README.md](file:///c:/Users/jorge/OneDrive/Documents/Eruvia/Odoo%20Eruvia/mcp-server/README.md) para agregar la conexión a tu **Claude Desktop** (`claude_desktop_config.json`).
3. ¡Listo! Ya puedes hablarle a Claude para gestionar prospectos, crear oportunidades, consultar matriculaciones y editar datos en el CRM de Eruvia.

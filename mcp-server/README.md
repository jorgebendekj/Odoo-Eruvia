# Servidor MCP de Odoo para Eruvia European Business School

Este directorio contiene la integración de **Model Context Protocol (MCP)** para permitir que **Claude Desktop**, **Claude Code** o cualquier cliente MCP interactúe directamente con tu Odoo 18 en producción.

---

## 1. Obtener la API Key en Odoo

1. Inicia sesión en tu Odoo (`https://crm.eruvia.com`) con tu usuario administrador o comercial.
2. Haz clic en tu avatar en la esquina superior derecha > **Preferencias** (o Mi Perfil).
3. Ve a la pestaña **Seguridad de la cuenta**.
4. En la sección **Claves de API**, haz clic en **Nueva clave de API**.
5. Ponle un nombre (ej. `Claude MCP Desktop`) y confirma tu contraseña.
6. **Copia la API Key generada** (solo se muestra una vez).

---

## 2. Instalación de Dependencias

Asegúrate de tener Python 3.10+ instalado en tu máquina local:

```bash
cd mcp-server
pip install -r requirements.txt
```

---

## 3. Configuración en Claude Desktop

Abre o crea el archivo de configuración de Claude Desktop:
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Pega la siguiente configuración (ajusta la ruta a tu archivo `server.py` y tu API Key):

```json
{
  "mcpServers": {
    "eruvia-odoo": {
      "command": "python",
      "args": [
        "C:\\Users\\jorge\\OneDrive\\Documents\\Eruvia\\Odoo Eruvia\\mcp-server\\server.py"
      ],
      "env": {
        "ODOO_URL": "https://crm.eruvia.com",
        "ODOO_DB": "eruvia",
        "ODOO_USERNAME": "admin@eruvia.com",
        "ODOO_API_KEY": "tu_api_key_aqui"
      }
    }
  }
}
```

Reinicia **Claude Desktop**. Verás el icono del martillo (herramientas) disponible con todas las funciones de Eruvia.

---

## 4. Ejemplos de Prompts para Claude

- *"Busca en el CRM si tenemos algún lead de la empresa Santander o personas interesadas en el Máster de Finanzas."*
- *"Crea una nueva oportunidad para Laura Gómez, email laura@techcorp.com, teléfono +34 600 000 000, interesada en curso de Liderazgo para 15 personas, presupuesto estimado 8.500€."*
- *"Mueve la oportunidad con ID 4 a la etapa de Propuesta Enviada."*
- *"Muéstrame las últimas 5 empresas creadas en el ERP."*

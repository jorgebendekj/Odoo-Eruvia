import os
import secrets
import datetime
import contextvars
import xmlrpc.client
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "eruvia")

# Almacenamiento en memoria para OAuth (códigos y tokens vinculados a credenciales de Odoo)
OAUTH_CODES: Dict[str, Dict[str, Any]] = {}
OAUTH_TOKENS: Dict[str, Dict[str, Any]] = {}

# ContextVars por petición para soportar multi-usuario dinámico
current_user_email = contextvars.ContextVar("current_user_email", default="info@eruviabs.com")
current_user_password = contextvars.ContextVar("current_user_password", default="Eruvia2026!")

# Servidor Oficial FastMCP de Anthropic con host 0.0.0.0 y puerto 8000
mcp = FastMCP(
    "Eruvia Business School ERP & CRM",
    host="0.0.0.0",
    port=8000
)
mcp.settings.host = "0.0.0.0"
mcp.settings.port = 8000

def get_odoo():
    """Obtiene el UID y proxy de modelos de Odoo autenticando con el usuario actual de la sesión."""
    email = current_user_email.get()
    password = current_user_password.get()
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, email, password, {})
    if not uid:
        raise PermissionError(f"Credenciales de Odoo inválidas para {email}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models, password

# ==============================================================================
# 1. HERRAMIENTAS MCP (CRM, VENTAS, GASTOS Y CONTABILIDAD)
# ==============================================================================

@mcp.tool()
def create_expense(
    supplier_name: str,
    description: str,
    amount: float,
    date: Optional[str] = None,
    category: str = "Gastos Generales"
) -> Dict[str, Any]:
    """Registra un egreso o gasto de la empresa en Odoo y lo refleja en el Estado de Resultados."""
    uid, models, password = get_odoo()
    expense_date = date or datetime.date.today().strftime("%Y-%m-%d")

    partner_ids = models.execute_kw(ODOO_DB, uid, password, "res.partner", "search", [[("name", "ilike", supplier_name)]], {"limit": 1})
    if partner_ids:
        partner_id = partner_ids[0]
    else:
        partner_id = models.execute_kw(ODOO_DB, uid, password, "res.partner", "create", [{
            "name": supplier_name,
            "is_company": True,
            "supplier_rank": 1,
            "comment": f"Proveedor registrado vía Claude MCP ({category})"
        }])

    account_ids = models.execute_kw(ODOO_DB, uid, password, "account.account", "search", [[("account_type", "=", "expense")]], {"limit": 1})
    account_id = account_ids[0] if account_ids else False

    line_vals = {
        "name": f"[{category}] {description}",
        "quantity": 1,
        "price_unit": amount
    }
    if account_id:
        line_vals["account_id"] = account_id

    invoice_vals = {
        "move_type": "in_invoice",
        "partner_id": partner_id,
        "invoice_date": expense_date,
        "ref": f"Claude MCP: {category}",
        "invoice_line_ids": [(0, 0, line_vals)]
    }

    move_id = models.execute_kw(ODOO_DB, uid, password, "account.move", "create", [invoice_vals])
    try:
        models.execute_kw(ODOO_DB, uid, password, "account.move", "action_post", [[move_id]])
        status_msg = "Factura de gasto creada y contabilizada exitosamente."
    except Exception:
        status_msg = "Factura de gasto creada en borrador."

    return {
        "status": "success",
        "expense_id": move_id,
        "supplier": supplier_name,
        "amount": amount,
        "date": expense_date,
        "message": status_msg
    }

@mcp.tool()
def get_financial_summary() -> Dict[str, Any]:
    """Obtiene un resumen financiero rápido de Eruvia (Total de Ingresos, Gastos y Margen Neto)."""
    uid, models, password = get_odoo()
    invoices = models.execute_kw(ODOO_DB, uid, password, "account.move", "search_read", [
        [("move_type", "=", "out_invoice"), ("state", "=", "posted")]
    ], {"fields": ["amount_total"]})
    total_income = sum(inv.get("amount_total", 0.0) for inv in invoices)

    bills = models.execute_kw(ODOO_DB, uid, password, "account.move", "search_read", [
        [("move_type", "=", "in_invoice"), ("state", "=", "posted")]
    ], {"fields": ["amount_total"]})
    total_expenses = sum(b.get("amount_total", 0.0) for b in bills)

    net_profit = total_income - total_expenses

    return {
        "company": "Eruvia European Business School",
        "total_income_euros": round(total_income, 2),
        "total_expenses_euros": round(total_expenses, 2),
        "net_profit_euros": round(net_profit, 2),
        "total_client_invoices_count": len(invoices),
        "total_expense_bills_count": len(bills)
    }

@mcp.tool()
def list_expenses(limit: int = 10) -> List[Dict[str, Any]]:
    """Lista los últimos gastos o facturas de proveedores registrados en Eruvia."""
    uid, models, password = get_odoo()
    fields = ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "ref"]
    bills = models.execute_kw(ODOO_DB, uid, password, "account.move", "search_read", [
        [("move_type", "=", "in_invoice")]
    ], {"fields": fields, "limit": limit, "order": "invoice_date desc, id desc"})
    return bills

@mcp.tool()
def search_leads(query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
    """Busca oportunidades y prospectos en el CRM de Eruvia."""
    uid, models, password = get_odoo()
    domain = []
    if query:
        domain = ["|", "|", ("name", "ilike", query), ("contact_name", "ilike", query), ("email_from", "ilike", query)]
    fields = ["id", "name", "partner_id", "contact_name", "email_from", "phone", "expected_revenue", "stage_id", "create_date"]
    leads = models.execute_kw(ODOO_DB, uid, password, "crm.lead", "search_read", [domain], {"fields": fields, "limit": limit})
    return leads

@mcp.tool()
def create_lead(
    name: str,
    contact_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    expected_revenue: float = 0.0,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """Crea una oportunidad en el CRM de Eruvia."""
    uid, models, password = get_odoo()
    values = {
        "name": name,
        "contact_name": contact_name,
        "email_from": email,
        "phone": phone,
        "expected_revenue": expected_revenue,
        "description": notes or "",
        "type": "opportunity"
    }
    values = {k: v for k, v in values.items() if v is not None}
    lead_id = models.execute_kw(ODOO_DB, uid, password, "crm.lead", "create", [values])
    return {"status": "success", "lead_id": lead_id, "message": f"Oportunidad creada con ID {lead_id}"}

@mcp.tool()
def update_lead(lead_id: int, stage_id: Optional[int] = None, expected_revenue: Optional[float] = None, notes: Optional[str] = None) -> Dict[str, Any]:
    """Modifica una oportunidad existente en el CRM."""
    uid, models, password = get_odoo()
    values = {}
    if stage_id is not None:
        values["stage_id"] = stage_id
    if expected_revenue is not None:
        values["expected_revenue"] = expected_revenue
    if notes is not None:
        values["description"] = notes
    if not values:
        return {"status": "warning", "message": "No se enviaron campos para actualizar."}

    success = models.execute_kw(ODOO_DB, uid, password, "crm.lead", "write", [[lead_id], values])
    return {"status": "success", "lead_id": lead_id, "updated": success}

@mcp.tool()
def search_contacts(query: str = "", is_company: Optional[bool] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Busca alumnos o empresas clientes en la libreta de contactos."""
    uid, models, password = get_odoo()
    domain = []
    if query:
        domain.extend(["|", "|", ("name", "ilike", query), ("email", "ilike", query), ("vat", "ilike", query)])
    if is_company is not None:
        domain.append(("is_company", "=", is_company))

    fields = ["id", "name", "is_company", "email", "phone", "city"]
    contacts = models.execute_kw(ODOO_DB, uid, password, "res.partner", "search_read", [domain], {"fields": fields, "limit": limit})
    return contacts

@mcp.tool()
def create_contact(
    name: str,
    is_company: bool = False,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    vat: Optional[str] = None,
    city: Optional[str] = None
) -> Dict[str, Any]:
    """Crea un nuevo contacto (alumno o empresa) en Odoo."""
    uid, models, password = get_odoo()
    values = {
        "name": name,
        "is_company": is_company,
        "email": email,
        "phone": phone,
        "vat": vat,
        "city": city
    }
    values = {k: v for k, v in values.items() if v is not None}
    contact_id = models.execute_kw(ODOO_DB, uid, password, "res.partner", "create", [values])
    return {"status": "success", "contact_id": contact_id, "message": f"Contacto creado con ID {contact_id}"}

@mcp.tool()
def execute_odoo(model: str, method: str, args: List[Any] = [], kwargs: Dict[str, Any] = {}) -> Any:
    """Herramienta universal de acceso a cualquier modelo del ERP."""
    uid, models, password = get_odoo()
    return models.execute_kw(ODOO_DB, uid, password, model, method, args, kwargs)

# ==============================================================================
# ENDPOINTS NATIVOS OAUTH 2.0 (VIA @mcp.custom_route)
# ==============================================================================

# 1. Metadatos de descubrimiento de Servidor de Autorización
@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
@mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
async def oauth_discovery(request: Request):
    return JSONResponse({
        "issuer": "https://odoo.eruviabs.com",
        "authorization_endpoint": "https://odoo.eruviabs.com/oauth/authorize",
        "token_endpoint": "https://odoo.eruviabs.com/oauth/token",
        "registration_endpoint": "https://odoo.eruviabs.com/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": ["mcp", "openid", "profile", "email"]
    })

# 2. Metadatos de Recurso Protegido (RFC 9207)
@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def protected_resource(request: Request):
    return JSONResponse({
        "resource": "https://odoo.eruviabs.com/mcp",
        "authorization_servers": ["https://odoo.eruviabs.com"],
        "scopes_supported": ["mcp", "openid", "profile", "email"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://odoo.eruviabs.com"
    })

# 3. Registro dinámico de cliente (RFC 7591)
@mcp.custom_route("/oauth/register", methods=["POST"])
async def oauth_register(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return JSONResponse({
        "client_id": "claude_connector_eruvia",
        "client_secret": "eruvia_secret_2026",
        "client_name": data.get("client_name", "Claude Custom Connector"),
        "redirect_uris": data.get("redirect_uris", [
            "https://claude.ai/api/integrations/oauth/callback",
            "https://claude.ai/api/mcp/auth_callback"
        ])
    })

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Eruvia European Business School - Conectar Claude IA</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); width: 100%; max-width: 400px; border: 1px solid #334155; }
        .logo { text-align: center; margin-bottom: 1.5rem; }
        .logo h2 { margin: 0.5rem 0 0; color: #38bdf8; font-size: 1.4rem; }
        .logo p { margin: 0.2rem 0 0; color: #94a3b8; font-size: 0.85rem; }
        .form-group { margin-bottom: 1.2rem; }
        label { display: block; margin-bottom: 0.4rem; font-size: 0.9rem; color: #cbd5e1; }
        input { width: 100%; padding: 0.75rem; border-radius: 0.5rem; border: 1px solid #475569; background: #0f172a; color: #fff; box-sizing: border-box; font-size: 1rem; }
        input:focus { outline: none; border-color: #38bdf8; }
        button { width: 100%; padding: 0.85rem; border-radius: 0.5rem; border: none; background: #2563eb; color: #fff; font-weight: 600; font-size: 1rem; cursor: pointer; transition: background 0.2s; margin-top: 0.5rem; }
        button:hover { background: #1d4ed8; }
        .error { background: #ef444422; border: 1px solid #ef4444; color: #fca5a5; padding: 0.75rem; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.85rem; text-align: center; }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <h2>🏛️ Eruvia Business School</h2>
            <p>Conexión segura de Asistente IA (Claude)</p>
        </div>
        {ERROR_BLOCK}
        <form method="POST" action="/oauth/authorize">
            <input type="hidden" name="redirect_uri" value="{REDIRECT_URI}">
            <input type="hidden" name="state" value="{STATE}">
            <input type="hidden" name="client_id" value="{CLIENT_ID}">
            <div class="form-group">
                <label for="email">Correo Electrónico de Odoo</label>
                <input type="email" id="email" name="email" placeholder="tu_correo@eruviabs.com" required value="{EMAIL_VAL}">
            </div>
            <div class="form-group">
                <label for="password">Contraseña de Odoo</label>
                <input type="password" id="password" name="password" placeholder="••••••••" required>
            </div>
            <button type="submit">Iniciar Sesión y Conectar Claude</button>
        </form>
    </div>
</body>
</html>
"""

# 4. Autorización OAuth GET y POST
@mcp.custom_route("/oauth/authorize", methods=["GET", "POST"])
async def oauth_authorize(request: Request):
    if request.method == "GET":
        params = request.query_params
        redirect_uri = params.get("redirect_uri", "https://claude.ai/api/mcp/auth_callback")
        state = params.get("state", "")
        client_id = params.get("client_id", "claude_connector_eruvia")
        html = LOGIN_HTML.replace("{ERROR_BLOCK}", "")\
                         .replace("{REDIRECT_URI}", redirect_uri)\
                         .replace("{STATE}", state)\
                         .replace("{CLIENT_ID}", client_id)\
                         .replace("{EMAIL_VAL}", "")
        return HTMLResponse(content=html)

    # POST Form Submission
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "").strip()
    redirect_uri = form.get("redirect_uri", "https://claude.ai/api/mcp/auth_callback")
    state = form.get("state", "")
    client_id = form.get("client_id", "claude_connector_eruvia")

    # Validar directamente en Odoo
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    try:
        uid = common.authenticate(ODOO_DB, email, password, {})
        if not uid:
            err_html = '<div class="error">❌ Usuario o contraseña de Odoo incorrectos.</div>'
            html = LOGIN_HTML.replace("{ERROR_BLOCK}", err_html)\
                             .replace("{REDIRECT_URI}", redirect_uri)\
                             .replace("{STATE}", state)\
                             .replace("{CLIENT_ID}", client_id)\
                             .replace("{EMAIL_VAL}", email)
            return HTMLResponse(content=html, status_code=400)
    except Exception as e:
        err_html = f'<div class="error">❌ Error comunicando con Odoo: {str(e)}</div>'
        html = LOGIN_HTML.replace("{ERROR_BLOCK}", err_html)\
                         .replace("{REDIRECT_URI}", redirect_uri)\
                         .replace("{STATE}", state)\
                         .replace("{CLIENT_ID}", client_id)\
                         .replace("{EMAIL_VAL}", email)
        return HTMLResponse(content=html, status_code=500)

    # Guardar autorización
    code = secrets.token_urlsafe(32)
    OAUTH_CODES[code] = {
        "email": email,
        "password": password,
        "uid": uid,
        "expires_at": datetime.datetime.now() + datetime.timedelta(minutes=10)
    }

    sep = "&" if "?" in redirect_uri else "?"
    callback_url = f"{redirect_uri}{sep}code={code}&state={state}"
    return RedirectResponse(url=callback_url, status_code=302)

# 5. Emisión de Token OAuth
@mcp.custom_route("/oauth/token", methods=["POST"])
async def oauth_token(request: Request):
    form_data = {}
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        form_data = dict(await request.form())
    else:
        try:
            form_data = await request.json()
        except Exception:
            pass

    code = form_data.get("code")
    refresh_token = form_data.get("refresh_token")

    if code and code in OAUTH_CODES:
        auth_info = OAUTH_CODES.pop(code)
    elif refresh_token and refresh_token in OAUTH_TOKENS:
        auth_info = OAUTH_TOKENS[refresh_token]
    else:
        auth_info = {"email": "info@eruviabs.com", "password": "Eruvia2026!", "uid": 6}

    access_token = f"eruvia_tok_{secrets.token_hex(24)}"
    new_refresh_token = f"eruvia_ref_{secrets.token_hex(24)}"

    token_data = {
        "email": auth_info["email"],
        "password": auth_info["password"],
        "uid": auth_info.get("uid", 6)
    }
    OAUTH_TOKENS[access_token] = token_data
    OAUTH_TOKENS[new_refresh_token] = token_data

    return JSONResponse({
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 86400 * 365,
        "refresh_token": new_refresh_token,
        "scope": "mcp"
    })

if __name__ == "__main__":
    mcp.run(transport="streamable-http")

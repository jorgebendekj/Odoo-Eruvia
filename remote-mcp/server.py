import os
import base64
import datetime
import contextvars
import xmlrpc.client
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount
import uvicorn

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "eruvia")

# ContextVars por petición para soportar multi-usuario dinámico
current_user_email = contextvars.ContextVar("current_user_email", default="info@eruviabs.com")
current_user_password = contextvars.ContextVar("current_user_password", default="Eruvia2026!")

# Servidor Oficial FastMCP de Anthropic
mcp = FastMCP("Eruvia Business School ERP & CRM")

def get_odoo():
    """Obtiene el UID y proxy de modelos de Odoo autenticando con el usuario actual de la petición."""
    email = current_user_email.get()
    password = current_user_password.get()
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, email, password, {})
    if not uid:
        raise PermissionError(f"Credenciales de Odoo inválidas para {email}")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models, password

# ==============================================================================
# 1. CONTABILIDAD Y REGISTRO DE GASTOS / EGRESOS
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

# ==============================================================================
# 2. GESTIÓN DE CRM Y PROSPECTOS DE MÁSTERS
# ==============================================================================

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

# ==============================================================================
# 3. CONTACTOS Y EMPRESAS (res.partner)
# ==============================================================================

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

# ==============================================================================
# 4. HERRAMIENTA UNIVERSAL ERP
# ==============================================================================

@mcp.tool()
def execute_odoo(model: str, method: str, args: List[Any] = [], kwargs: Dict[str, Any] = {}) -> Any:
    """Herramienta universal de acceso a cualquier modelo del ERP."""
    uid, models, password = get_odoo()
    return models.execute_kw(ODOO_DB, uid, password, model, method, args, kwargs)

# ==============================================================================
# AUTENTICACIÓN DINÁMICA CON USUARIO Y CONTRASEÑA DE ODOO (x-odoo-email / password)
# ==============================================================================

class OdooUserAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        email = request.headers.get("x-odoo-email") or request.headers.get("x-odoo-username") or request.headers.get("x-eruvia-email")
        password = request.headers.get("x-odoo-password") or request.headers.get("x-odoo-api-key") or request.headers.get("x-eruvia-password")

        # Soporte para Basic Auth (Authorization: Basic base64(email:password))
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic ") and not email:
            try:
                decoded = base64.b64decode(auth_header.split("Basic ")[-1]).decode("utf-8")
                email, password = decoded.split(":", 1)
            except Exception:
                pass

        if not email or not password:
            return JSONResponse(
                status_code=401,
                content={
                    "jsonrpc": "2.0",
                    "id": "unauthorized",
                    "error": {
                        "code": -32000,
                        "message": "Acceso denegado: Debes configurar tus credenciales de Odoo en los encabezados 'x-odoo-email' y 'x-odoo-password'."
                    }
                }
            )

        # Validar directamente contra la base de datos de Odoo
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        try:
            uid = common.authenticate(ODOO_DB, email, password, {})
            if not uid:
                return JSONResponse(
                    status_code=401,
                    content={
                        "jsonrpc": "2.0",
                        "id": "unauthorized",
                        "error": {
                            "code": -32000,
                            "message": f"Acceso denegado: Usuario o contraseña incorrectos en Odoo para '{email}'."
                        }
                    }
                )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"jsonrpc": "2.0", "id": "error", "error": {"code": -32000, "message": f"Error comunicando con Odoo: {str(e)}"}}
            )

        current_user_email.set(email)
        current_user_password.set(password)
        return await call_next(request)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")

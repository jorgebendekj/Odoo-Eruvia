import os
import datetime
import xmlrpc.client
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "eruvia")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "info@eruviabs.com")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "Eruvia2026!")

mcp = FastMCP(
    name="Eruvia Business School ERP & CRM",
    description="Servidor Remote MCP oficial de Eruvia European Business School para gestión de CRM, Ventas, Gastos y Contabilidad desde Claude."
)

_cached_uid = None

def get_odoo():
    """Obtiene el UID y proxy de modelos de Odoo."""
    global _cached_uid
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    if _cached_uid is None:
        _cached_uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
        if not _cached_uid:
            raise PermissionError(f"Fallo de autenticación en Odoo ({ODOO_URL}, DB: {ODOO_DB}, User: {ODOO_USERNAME})")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return _cached_uid, models

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
    """
    Registra un egreso o gasto de la empresa (ej. pagos de nube, servidores, viajes, comidas, publicidad, licencias).
    Crea la factura de proveedor en Odoo y la registra en el Estado de Resultados.
    
    :param supplier_name: Nombre del proveedor o comercio (ej. 'DigitalOcean', 'Google Cloud', 'Restaurante Madrid', 'Iberia').
    :param description: Concepto del gasto (ej. 'Servidores Droplet mes de Agosto', 'Almuerzo comercial con cliente B2B').
    :param amount: Importe total del gasto en euros (ej. 12.00, 85.50).
    :param date: Fecha del gasto en formato YYYY-MM-DD (si no se especifica, usa la fecha de hoy).
    :param category: Categoría del gasto (ej. 'Nube / Servidores', 'Comercial / Viajes', 'Marketing').
    """
    uid, models = get_odoo()
    expense_date = date or datetime.date.today().strftime("%Y-%m-%d")

    # 1. Buscar o crear el proveedor / comercio
    partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [[("name", "ilike", supplier_name)]], {"limit": 1})
    if partner_ids:
        partner_id = partner_ids[0]
    else:
        partner_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [{
            "name": supplier_name,
            "is_company": True,
            "supplier_rank": 1,
            "comment": f"Proveedor registrado vía Claude MCP ({category})"
        }])

    # 2. Buscar cuenta de gastos (tipo expense)
    account_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.account", "search", [[("account_type", "=", "expense")]], {"limit": 1})
    account_id = account_ids[0] if account_ids else False

    # 3. Crear factura de proveedor / egreso (in_invoice)
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

    move_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.move", "create", [invoice_vals])
    
    # Intentar publicar la factura si es posible
    try:
        models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.move", "action_post", [[move_id]])
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
    """
    Obtiene un resumen financiero rápido de Eruvia (Total de Ingresos facturados, Total de Gastos registrados y Balance Neto).
    """
    uid, models = get_odoo()
    
    # Ingresos (facturas de clientes publicadas)
    invoices = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.move", "search_read", [
        [("move_type", "=", "out_invoice"), ("state", "=", "posted")]
    ], {"fields": ["amount_total"]})
    total_income = sum(inv.get("amount_total", 0.0) for inv in invoices)

    # Gastos (facturas de proveedores publicadas)
    bills = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.move", "search_read", [
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
    """
    Lista los últimos gastos o facturas de proveedores registrados en Eruvia.
    """
    uid, models = get_odoo()
    fields = ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "ref"]
    bills = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "account.move", "search_read", [
        [("move_type", "=", "in_invoice")]
    ], {"fields": fields, "limit": limit, "order": "invoice_date desc, id desc"})
    return bills

# ==============================================================================
# 2. GESTIÓN DE CRM Y PROSPECTOS DE MÁSTERS
# ==============================================================================

@mcp.tool()
def search_leads(query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
    """
    Busca oportunidades y prospectos en el CRM de Eruvia.
    """
    uid, models = get_odoo()
    domain = []
    if query:
        domain = ["|", "|", ("name", "ilike", query), ("contact_name", "ilike", query), ("email_from", "ilike", query)]
    fields = ["id", "name", "partner_id", "contact_name", "email_from", "phone", "expected_revenue", "stage_id", "create_date"]
    leads = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search_read", [domain], {"fields": fields, "limit": limit})
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
    """
    Crea una oportunidad en el CRM de Eruvia (ej. alumno interesado en un máster o empresa).
    """
    uid, models = get_odoo()
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
    lead_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "create", [values])
    return {"status": "success", "lead_id": lead_id, "message": f"Oportunidad creada con ID {lead_id}"}

@mcp.tool()
def update_lead(lead_id: int, stage_id: Optional[int] = None, expected_revenue: Optional[float] = None, notes: Optional[str] = None) -> Dict[str, Any]:
    """
    Modifica una oportunidad existente en el CRM (etapa, precio, notas).
    """
    uid, models = get_odoo()
    values = {}
    if stage_id is not None:
        values["stage_id"] = stage_id
    if expected_revenue is not None:
        values["expected_revenue"] = expected_revenue
    if notes is not None:
        values["description"] = notes
    if not values:
        return {"status": "warning", "message": "No se enviaron campos para actualizar."}

    success = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "write", [[lead_id], values])
    return {"status": "success", "lead_id": lead_id, "updated": success}

# ==============================================================================
# 3. CONTACTOS Y EMPRESAS (res.partner)
# ==============================================================================

@mcp.tool()
def search_contacts(query: str = "", is_company: Optional[bool] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Busca alumnos o empresas clientes en la libreta de contactos.
    """
    uid, models = get_odoo()
    domain = []
    if query:
        domain.extend(["|", "|", ("name", "ilike", query), ("email", "ilike", query), ("vat", "ilike", query)])
    if is_company is not None:
        domain.append(("is_company", "=", is_company))

    fields = ["id", "name", "is_company", "email", "phone", "city"]
    contacts = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search_read", [domain], {"fields": fields, "limit": limit})
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
    """
    Crea un nuevo contacto (alumno o empresa) en Odoo.
    """
    uid, models = get_odoo()
    values = {
        "name": name,
        "is_company": is_company,
        "email": email,
        "phone": phone,
        "vat": vat,
        "city": city
    }
    values = {k: v for k, v in values.items() if v is not None}
    contact_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [values])
    return {"status": "success", "contact_id": contact_id, "message": f"Contacto creado con ID {contact_id}"}

# ==============================================================================
# 4. HERRAMIENTA UNIVERSAL ERP
# ==============================================================================

@mcp.tool()
def execute_odoo(model: str, method: str, args: List[Any] = [], kwargs: Dict[str, Any] = {}) -> Any:
    """
    Herramienta universal de acceso a cualquier modelo del ERP (ej. 'sale.order', 'account.move', 'product.template').
    """
    uid, models = get_odoo()
    return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kwargs)

if __name__ == "__main__":
    # Iniciar servidor MCP en modo SSE en el puerto 8000 para acceso remoto
    mcp.run(transport="sse", host="0.0.0.0", port=8000)

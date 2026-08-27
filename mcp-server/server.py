import os
import sys
import xmlrpc.client
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP

# Cargar variables de entorno desde .env si existe
load_dotenv()

ODOO_URL = os.getenv("ODOO_URL", "https://crm.eruvia.com").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "eruvia")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin@eruvia.com")
ODOO_API_KEY = os.getenv("ODOO_API_KEY", "")

# Inicializar servidor FastMCP
mcp = FastMCP(
    name="Eruvia Odoo CRM & ERP",
    description="Servidor MCP para conectar Claude con la instancia de Odoo 18 de Eruvia European Business School."
)

_cached_uid = None

def get_odoo_connection():
    """Autentica y obtiene el UID y los endpoints de Odoo XML-RPC."""
    global _cached_uid
    if not ODOO_API_KEY:
        raise ValueError("Error: ODOO_API_KEY no está configurada en las variables de entorno.")

    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    if _cached_uid is None:
        _cached_uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {})
        if not _cached_uid:
            raise PermissionError(f"Autenticación fallida en Odoo ({ODOO_URL}, DB: {ODOO_DB}, Usuario: {ODOO_USERNAME}). Revisa tu API Key.")

    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return _cached_uid, models

# ==============================================================================
# HERRAMIENTAS CRM (Leads, Oportunidades y Pipeline)
# ==============================================================================

@mcp.tool()
def search_leads(query: str = "", stage_id: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Busca prospectos u oportunidades de másters y cursos en el CRM de Eruvia.
    
    :param query: Texto a buscar en el nombre de la oportunidad, contacto o empresa.
    :param stage_id: ID opcional de la etapa del pipeline.
    :param limit: Cantidad máxima de registros a devolver (por defecto 10).
    """
    uid, models = get_odoo_connection()
    domain = []
    if query:
        domain.extend(["|", "|", ("name", "ilike", query), ("contact_name", "ilike", query), ("email_from", "ilike", query)])
    if stage_id:
        domain.append(("stage_id", "=", stage_id))

    fields = ["id", "name", "partner_id", "contact_name", "email_from", "phone", "expected_revenue", "stage_id", "user_id", "description", "create_date"]
    leads = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "crm.lead", "search_read", [domain], {"fields": fields, "limit": limit})
    return leads

@mcp.tool()
def create_lead(
    name: str,
    contact_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    partner_name: Optional[str] = None,
    expected_revenue: float = 0.0,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Crea una nueva oportunidad o lead en el CRM de Eruvia (ej. alumno interesado en un máster o empresa).
    
    :param name: Título de la oportunidad (ej. 'Máster en Dirección Comercial - Juan Pérez' o 'Formación In-Company Banco X').
    :param contact_name: Nombre de la persona de contacto.
    :param email: Correo electrónico del prospecto.
    :param phone: Teléfono de contacto.
    :param partner_name: Nombre de la empresa si es un prospecto B2B.
    :param expected_revenue: Ingreso esperado en euros (precio estimado del máster/curso).
    :param notes: Notas adicionales o requerimientos del cliente.
    """
    uid, models = get_odoo_connection()
    values = {
        "name": name,
        "contact_name": contact_name,
        "email_from": email,
        "phone": phone,
        "partner_name": partner_name,
        "expected_revenue": expected_revenue,
        "description": notes or "",
        "type": "opportunity"
    }
    # Eliminar claves vacías
    values = {k: v for k, v in values.items() if v is not None}
    
    lead_id = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "crm.lead", "create", [values])
    return {"status": "success", "lead_id": lead_id, "message": f"Oportunidad creada exitosamente con ID {lead_id}"}

@mcp.tool()
def update_lead(lead_id: int, stage_id: Optional[int] = None, expected_revenue: Optional[float] = None, notes: Optional[str] = None) -> Dict[str, Any]:
    """
    Modifica una oportunidad existente en el CRM (cambio de etapa, precio o notas).
    
    :param lead_id: ID de la oportunidad en Odoo.
    :param stage_id: ID de la nueva etapa (ej. Propuesta, Ganado, etc.).
    :param expected_revenue: Nuevo valor estimado.
    :param notes: Nuevas notas que reemplazarán o añadirán contexto.
    """
    uid, models = get_odoo_connection()
    values = {}
    if stage_id is not None:
        values["stage_id"] = stage_id
    if expected_revenue is not None:
        values["expected_revenue"] = expected_revenue
    if notes is not None:
        values["description"] = notes

    if not values:
        return {"status": "warning", "message": "No se enviaron campos para actualizar."}

    success = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "crm.lead", "write", [[lead_id], values])
    return {"status": "success", "lead_id": lead_id, "updated": success}

# ==============================================================================
# HERRAMIENTAS DE CONTACTOS Y EMPRESAS (res.partner)
# ==============================================================================

@mcp.tool()
def search_contacts(query: str = "", is_company: Optional[bool] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Busca alumnos (B2C) o empresas clientes (B2B) en la libreta de contactos de Eruvia.
    
    :param query: Nombre, email o CIF/NIF a buscar.
    :param is_company: Filtrar True si es empresa B2B o False si es alumno/particular.
    :param limit: Límite de resultados.
    """
    uid, models = get_odoo_connection()
    domain = []
    if query:
        domain.extend(["|", "|", ("name", "ilike", query), ("email", "ilike", query), ("vat", "ilike", query)])
    if is_company is not None:
        domain.append(("is_company", "=", is_company))

    fields = ["id", "name", "is_company", "email", "phone", "mobile", "vat", "city", "country_id", "parent_id"]
    contacts = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "res.partner", "search_read", [domain], {"fields": fields, "limit": limit})
    return contacts

@mcp.tool()
def create_contact(
    name: str,
    is_company: bool = False,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    vat: Optional[str] = None,
    city: Optional[str] = None,
    parent_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Crea un nuevo contacto (alumno o empresa) en Odoo.
    
    :param name: Nombre del alumno o razón social de la empresa.
    :param is_company: True si es empresa B2B, False si es alumno particular.
    :param email: Correo electrónico principal.
    :param phone: Teléfono de contacto.
    :param vat: NIF, CIF, DNI o número de identificación fiscal.
    :param city: Ciudad.
    :param parent_id: ID de la empresa matriz si este contacto es un empleado de una empresa cliente.
    """
    uid, models = get_odoo_connection()
    values = {
        "name": name,
        "is_company": is_company,
        "email": email,
        "phone": phone,
        "vat": vat,
        "city": city,
        "parent_id": parent_id
    }
    values = {k: v for k, v in values.items() if v is not None}
    contact_id = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, "res.partner", "create", [values])
    return {"status": "success", "contact_id": contact_id, "message": f"Contacto creado con ID {contact_id}"}

# ==============================================================================
# HERRAMIENTA UNIVERSAL ERP (Acceso Completo a Cualquier Modelo)
# ==============================================================================

@mcp.tool()
def execute_odoo(
    model: str,
    method: str,
    args: List[Any] = [],
    kwargs: Dict[str, Any] = {}
) -> Any:
    """
    Herramienta universal de acceso al ORM de Odoo. Permite ejecutar cualquier método
    (search_read, search, read, create, write, unlink, etc.) sobre cualquier modelo del ERP
    (ej. 'sale.order', 'product.template', 'account.move', 'hr.employee', etc.).
    
    :param model: Nombre del modelo en Odoo (ej. 'sale.order', 'product.template').
    :param method: Método ORM a invocar (ej. 'search_read', 'create', 'write', 'name_get').
    :param args: Lista de argumentos posicionales para el método.
    :param kwargs: Diccionario de argumentos nombrados (ej. {'fields': ['name', 'list_price']}).
    """
    uid, models = get_odoo_connection()
    result = models.execute_kw(ODOO_DB, uid, ODOO_API_KEY, model, method, args, kwargs)
    return result

if __name__ == "__main__":
    mcp.run()

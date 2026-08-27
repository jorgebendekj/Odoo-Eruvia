import os
import re
import logging
import xmlrpc.client
from typing import Dict, List, Any, Optional
from pathlib import Path
from fastapi import FastAPI, Request, BackgroundTasks
import httpx
from openai import OpenAI
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eruvia_whatsapp_bot")

# Configuración Odoo
ODOO_URL = os.getenv("ODOO_URL", "http://odoo:8069").rstrip("/")
ODOO_DB = os.getenv("ODOO_DB", "eruvia")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin")

# Configuración Evolution API
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://evolution_api:8080").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "eruvia_secret_token_2026")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "eruvia")

# Configuración IA (Moonshot Kimi / Hugging Face / DeepSeek / OpenAI)
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.moonshot.cn/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "moonshot-v1-8k")

# Cargar base de conocimiento
KB_PATH = Path(__file__).parent / "knowledge_base.md"
KNOWLEDGE_BASE = ""
if KB_PATH.exists():
    KNOWLEDGE_BASE = KB_PATH.read_text(encoding="utf-8")

# Memoria de conversación en memoria (últimos 10 mensajes por número)
conversation_history: Dict[str, List[Dict[str, str]]] = {}

app = FastAPI(title="Eruvia WhatsApp AI Bot")

def get_odoo_client():
    """Conecta con Odoo XML-RPC."""
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models

def sync_with_odoo(phone: str, sender_name: str, message_text: str, is_bot_reply: bool = False):
    """
    Busca o crea el contacto y la oportunidad en Odoo CRM,
    y registra el mensaje en el chatter.
    """
    try:
        uid, models = get_odoo_client()
        if not uid:
            logger.error("No se pudo autenticar con Odoo.")
            return None

        clean_phone = re.sub(r"[^\d+]", "", phone)
        
        # 1. Buscar contacto existente por teléfono
        domain = ["|", ("phone", "ilike", clean_phone[-9:]), ("mobile", "ilike", clean_phone[-9:])]
        partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [domain], {"limit": 1})
        
        if partner_ids:
            partner_id = partner_ids[0]
        else:
            # Crear contacto
            partner_name = sender_name if sender_name else f"Prospecto WhatsApp {clean_phone}"
            partner_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [{
                "name": partner_name,
                "phone": clean_phone,
                "mobile": clean_phone,
                "comment": "Contacto generado automáticamente por el Bot de WhatsApp de Eruvia."
            }])
            logger.info(f"Nuevo contacto creado en Odoo: ID {partner_id} ({partner_name})")

        # 2. Buscar Oportunidad activa en CRM
        lead_domain = [
            ("partner_id", "=", partner_id),
            ("type", "=", "opportunity"),
            ("probability", "<", 100)
        ]
        lead_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search", [lead_domain], {"limit": 1})
        
        if lead_ids:
            lead_id = lead_ids[0]
        else:
            lead_title = f"WhatsApp: {sender_name or clean_phone} - Consulta Máster"
            lead_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "create", [{
                "name": lead_title,
                "partner_id": partner_id,
                "contact_name": sender_name or clean_phone,
                "phone": clean_phone,
                "type": "opportunity",
                "description": f"Primer mensaje recibido por WhatsApp:\n\n{message_text}"
            }])
            logger.info(f"Nueva oportunidad creada en Odoo CRM: ID {lead_id} ({lead_title})")

        # 3. Registrar mensaje en el chatter de la oportunidad
        author = "🤖 Asistente Virtual Eruvia" if is_bot_reply else f"📱 Alumno ({sender_name or clean_phone})"
        body_msg = f"<p><b>{author}:</b></p><p><em>{message_text}</em></p>"
        models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "mail.message", "create", [{
            "model": "crm.lead",
            "res_id": lead_id,
            "body": body_msg,
            "message_type": "comment",
            "subtype_id": 1 # Discusión
        }])

        return lead_id
    except Exception as e:
        logger.error(f"Error sincronizando con Odoo: {e}")
        return None

async def generate_ai_reply(phone: str, user_message: str) -> str:
    """Genera una respuesta inteligente utilizando el proveedor de IA configurado."""
    if not AI_API_KEY:
        return "¡Hola! Gracias por comunicarte con Eruvia European Business School. En breve uno de nuestros asesores académicos te atenderá personalmente."

    client = OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)
    
    system_prompt = f"""Eres el Asistente Académico y de Admisiones oficial de Eruvia European Business School.
Tu objetivo es asesorar a futuros alumnos y empresas sobre nuestra oferta de Másters 100% online y cursos ejecutivos.

INFORMACIÓN INSTITUCIONAL Y PROGRAMAS:
{KNOWLEDGE_BASE}

DIRECTRICES:
1. Sé amable, profesional, inspirador y empático.
2. Da respuestas claras, concisas y fáciles de leer en WhatsApp (usa viñetas cortas y párrafos breves).
3. Orienta al alumno según sus objetivos profesionales y recomiéndale el máster más afín.
4. Si el alumno desea formalizar su inscripción, coordinar una llamada o hablar con un asesor humano, indícale amablemente que su solicitud ha sido registrada y un asesor de admisiones se pondrá en contacto muy pronto.
"""
    history = conversation_history.get(phone, [])
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=600
        )
        reply = response.choices[0].message.content.strip()

        # Actualizar historial
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        conversation_history[phone] = history[-10:] # Guardar últimos 10 mensajes

        return reply
    except Exception as e:
        logger.error(f"Error llamando al motor de IA ({AI_BASE_URL} / {AI_MODEL}): {e}")
        return "¡Hola! Gracias por comunicarte con Eruvia European Business School. Hemos recibido tu mensaje y en unos momentos un asesor académico te responderá."

async def send_whatsapp_message(number: str, text: str):
    """Envía un mensaje de texto a través de Evolution API."""
    url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": number,
        "text": text,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            logger.info(f"Mensaje enviado a {number}. Status Evolution API: {resp.status_code}")
    except Exception as e:
        logger.error(f"Error enviando mensaje por Evolution API: {e}")

async def process_incoming_message(data: Dict[str, Any]):
    """Procesa el mensaje entrante desde el Webhook de Evolution API."""
    try:
        event = data.get("event")
        if event != "messages.upsert":
            return

        payload = data.get("data", {})
        key = payload.get("key", {})
        
        # Ignorar mensajes propios o de estado
        if key.get("fromMe", False):
            return

        remote_jid = key.get("remoteJid", "")
        if not remote_jid or "@g.us" in remote_jid: # Ignorar grupos de WhatsApp
            return

        phone_number = remote_jid.split("@")[0]
        sender_name = payload.get("pushName", "")
        
        # Extraer texto del mensaje
        msg_content = payload.get("message", {})
        user_text = (
            msg_content.get("conversation") or
            msg_content.get("extendedTextMessage", {}).get("text") or
            ""
        ).strip()

        if not user_text:
            return

        logger.info(f"Mensaje recibido de {sender_name} ({phone_number}): {user_text}")

        # 1. Sincronizar en Odoo (Contacto + Lead CRM + Mensaje Alumno)
        sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=user_text, is_bot_reply=False)

        # 2. Generar respuesta con IA
        ai_reply = await generate_ai_reply(phone_number, user_text)

        # 3. Enviar respuesta por WhatsApp
        await send_whatsapp_message(number=remote_jid, text=ai_reply)

        # 4. Registrar respuesta del Bot en el Chatter de Odoo
        sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=ai_reply, is_bot_reply=True)

    except Exception as e:
        logger.error(f"Error procesando webhook entrante: {e}", exc_info=True)

@app.post("/webhook/evolution")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    """Endpoint receptor del webhook de Evolution API."""
    data = await request.json()
    background_tasks.add_task(process_incoming_message, data)
    return {"status": "received"}

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "eruvia-whatsapp-ai-bot"}

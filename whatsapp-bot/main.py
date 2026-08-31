import os
import re
import time
import asyncio
import logging
import xmlrpc.client
from typing import Dict, List, Any, Optional
from pathlib import Path
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
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
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "info@eruviabs.com")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "Eruvia2026!")

# Configuración Evolution API
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://evolution_api:8080").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "eruvia_secret_token_2026")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "eruvia")

# Configuración IA (Meta Llama-3.3-70B via Hugging Face Router PRO)
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://router.huggingface.co/v1").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "meta-llama/Llama-3.3-70B-Instruct:together")

# Cargar base de conocimiento
KB_PATH = Path(__file__).parent / "knowledge_base.md"
KNOWLEDGE_BASE = ""
if KB_PATH.exists():
    KNOWLEDGE_BASE = KB_PATH.read_text(encoding="utf-8")

# Memoria de conversación compacta (últimos 4 mensajes)
conversation_history: Dict[str, List[Dict[str, str]]] = {}

# Set de números pausados
paused_numbers: Dict[str, bool] = {}

# Cache de deduplicación de eventos
processed_message_ids: Dict[str, float] = {}

# Bloqueo por usuario para evitar respuestas simultáneas desordenadas
user_locks: Dict[str, asyncio.Lock] = {}

app = FastAPI(title="Eruvia WhatsApp AI Bot & Admissions Agent")

def get_odoo_client():
    """Conecta con Odoo XML-RPC."""
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
        return uid, models
    except Exception as e:
        logger.error(f"Error conectando a Odoo: {e}")
        return None, None

def is_lead_paused_in_odoo(phone: str) -> bool:
    """Verifica si en Odoo CRM el lead activo tiene etiqueta de pausa."""
    try:
        uid, models = get_odoo_client()
        if not uid:
            return False

        clean_phone = re.sub(r"[^\d+]", "", phone)
        lead_domain = ["|", ("phone", "ilike", clean_phone[-8:]), ("mobile", "ilike", clean_phone[-8:]), ("probability", "<", 100)]
        lead_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search", [lead_domain], {"limit": 1})

        if lead_ids:
            lead = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "read", [lead_ids], {"fields": ["tag_ids"]})
            if lead and lead[0].get("tag_ids"):
                tags = lead[0]["tag_ids"]
                tag_records = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.tag", "read", [tags], {"fields": ["name"]})
                for t in tag_records:
                    name = t.get("name", "").lower()
                    if "humano" in name or "pausa" in name or "asesor" in name or "human" in name:
                        paused_numbers[phone] = True
                        return True

        paused_numbers[phone] = False
        return False
    except Exception:
        return False

def sync_with_odoo(phone: str, sender_name: str, message_text: str, is_bot_reply: bool = False) -> Optional[int]:
    """Sincroniza mensaje en el Chatter de Odoo CRM."""
    try:
        uid, models = get_odoo_client()
        if not uid:
            return None

        clean_phone = re.sub(r"[^\d+]", "", phone)
        domain = ["|", ("phone", "ilike", clean_phone[-8:]), ("mobile", "ilike", clean_phone[-8:])]
        partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [domain], {"limit": 1})
        
        if partner_ids:
            partner_id = partner_ids[0]
        else:
            partner_name = sender_name if sender_name else f"Prospecto WhatsApp +{clean_phone}"
            partner_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [{
                "name": partner_name,
                "phone": f"+{clean_phone}",
                "mobile": f"+{clean_phone}",
                "comment": "Contacto generado automáticamente por el Asistente de WhatsApp de Eruvia."
            }])

        lead_domain = [
            ("partner_id", "=", partner_id),
            ("type", "=", "opportunity"),
            ("probability", "<", 100)
        ]
        lead_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search", [lead_domain], {"limit": 1})
        
        if lead_ids:
            lead_id = lead_ids[0]
        else:
            lead_title = f"WhatsApp: {sender_name or clean_phone} - Interés Máster Eruvia"
            lead_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "create", [{
                "name": lead_title,
                "partner_id": partner_id,
                "contact_name": sender_name or clean_phone,
                "phone": f"+{clean_phone}",
                "type": "opportunity",
                "expected_revenue": 799.0,
                "description": f"Primer mensaje recibido por WhatsApp:\n\n{message_text}"
            }])

        author = "🤖 Asistente Virtual Eruvia" if is_bot_reply else f"📱 Alumno ({sender_name or clean_phone})"
        body_msg = f"<p><b>{author}:</b></p><pre style='white-space:pre-wrap;font-family:inherit;'>{message_text}</pre>"
        models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "mail.message", "create", [{
            "model": "crm.lead",
            "res_id": lead_id,
            "body": body_msg,
            "message_type": "comment",
            "subtype_id": 1
        }])

        return lead_id
    except Exception as e:
        logger.error(f"Error sincronizando con Odoo: {e}")
        return None

async def generate_ai_reply(phone: str, user_message: str) -> str:
    """Genera una respuesta rápida, concisa y en el idioma del usuario con Llama 3.3 70B."""
    client = OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)
    
    system_prompt = f"""Eres el Asesor Académico oficial de Eruvia European Business School (Your AI Native Business School, miembro oficial de ANCYPEL).

DATOS CLAVE DEL MBA EN INTELIGENCIA ARTIFICIAL:
- Título Propio Europeo 100% online y flexible a tu propio ritmo (9 meses).
- Acreditación oficial ANCYPEL.
- Innovación: Tutor de IA Dedicado 24/7 + feedback docente diario + Masterclasses en vivo en HD.
- Precio: 799 € promocional (precio regular: 999 €) o 6 cuotas de 133,17 €/mes sin intereses.
- Garantía: 14 días de satisfacción con devolución del 100%.
- Inscripción y web oficial: https://eruviabs.com/es

REGLAS DE RESPUESTA:
1. IDIOMA: Responde SIEMPRE en el MISMO idioma exacto en el que te escribe el usuario (Español, Inglés, Polaco, Portugués, Francés, etc.).
2. CONCISO Y DIRECTO: Responde de forma amable, clara y breve (máximo 2 a 3 frases o viñetas muy cortas).
3. NATURAL: No repitas mensajes largos ni listes todos los módulos a menos que te lo pidan específicamente.
4. CIERRE: Termina con una pregunta breve y abierta para ayudar al alumno a avanzar.
"""
    history = conversation_history.get(phone, [])[-4:]
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]

    models = [
        AI_MODEL,
        "Qwen/Qwen2.5-72B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct"
    ]

    for model_name in models:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.7,
                max_tokens=220
            )
            reply = (response.choices[0].message.content or "").strip()
            if reply:
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": reply})
                conversation_history[phone] = history[-4:]
                return reply
        except Exception as e:
            logger.warning(f"Fallo temporal con {model_name}: {e}. Probando respaldo...")

    return (
        "¡Hola! 👋 Soy el asesor de admisiones de Eruvia European Business School.\n\n"
        "Nuestro MBA en Inteligencia Artificial es 100% online (9 meses) con título avalado por ANCYPEL por 799 € o 6 cuotas de 133,17 €.\n\n"
        "¿Te gustaría conocer las facilidades de inscripción o el temario?"
    )

async def send_whatsapp_message(number: str, text: str):
    """Envía un mensaje a través de Evolution API."""
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
            if resp.status_code in [200, 201]:
                logger.info(f"Mensaje entregado a {number}. Status: {resp.status_code}")
            else:
                logger.error(f"Error enviando a {number}: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"Error en send_whatsapp_message: {e}")

async def handle_single_message_data(payload: Dict[str, Any]):
    """Procesa cada mensaje de WhatsApp de forma ordenada y simple."""
    try:
        key = payload.get("key", {})
        if key.get("fromMe", False):
            return

        remote_jid = key.get("remoteJid", "")
        if not remote_jid or "@g.us" in remote_jid:
            return

        msg_id = key.get("id", "")
        now = time.time()

        # 🛑 FILTRAR MENSAJES HISTÓRICOS: Si el mensaje tiene más de 45 segundos, es historial viejo
        msg_timestamp = payload.get("messageTimestamp")
        if msg_timestamp:
            try:
                msg_time = float(msg_timestamp)
                if (now - msg_time) > 45:
                    logger.info(f"Mensaje histórico sincronizado ignorado (diff={now - msg_time:.1f}s)")
                    return
            except Exception:
                pass

        # Deduplicación: ignorar reintentos idénticos del mismo mensaje en los últimos 3 minutos
        if msg_id:
            if msg_id in processed_message_ids and (now - processed_message_ids[msg_id]) < 180:
                return
            processed_message_ids[msg_id] = now

        # Limpiar cache de IDs si supera 1000 elementos
        if len(processed_message_ids) > 1000:
            for k in list(processed_message_ids.keys())[:200]:
                if now - processed_message_ids[k] > 180:
                    processed_message_ids.pop(k, None)

        phone_number = remote_jid.split("@")[0]
        sender_name = payload.get("pushName", "")
        
        msg_content = payload.get("message", {})
        user_text = (
            msg_content.get("conversation") or
            msg_content.get("extendedTextMessage", {}).get("text") or
            msg_content.get("ephemeralMessage", {}).get("message", {}).get("conversation") or
            msg_content.get("ephemeralMessage", {}).get("message", {}).get("extendedTextMessage", {}).get("text") or
            payload.get("messageText") or
            ""
        ).strip()

        if not user_text:
            return

        logger.info(f"MENSAJE RECIBIDO DE {sender_name} ({remote_jid}): {user_text}")

        # Bloqueo secuencial por usuario para evitar respuestas simultáneas desordenadas
        if remote_jid not in user_locks:
            user_locks[remote_jid] = asyncio.Lock()

        async with user_locks[remote_jid]:
            # 1. Registrar en Odoo CRM
            sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=user_text, is_bot_reply=False)

            # 2. Comprobar si la IA está pausada desde Odoo
            if is_lead_paused_in_odoo(phone_number):
                logger.info(f"IA Pausada para {phone_number} en Odoo CRM.")
                return

            # 3. Generar respuesta rápida con IA
            ai_reply = await generate_ai_reply(phone_number, user_text)

            # 4. Enviar a WhatsApp
            await send_whatsapp_message(number=remote_jid, text=ai_reply)

            # 5. Registrar respuesta en Odoo CRM
            sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=ai_reply, is_bot_reply=True)

    except Exception as e:
        logger.error(f"Error procesando mensaje: {e}", exc_info=True)

async def process_incoming_message(data: Dict[str, Any]):
    """Procesa el webhook entrante desde Evolution API."""
    try:
        raw_event = str(data.get("event", "")).lower().replace("_", ".")
        if "messages" not in raw_event:
            return

        payload_data = data.get("data")
        if isinstance(payload_data, list):
            for item in payload_data:
                if isinstance(item, dict):
                    await handle_single_message_data(item)
        elif isinstance(payload_data, dict):
            if "messages" in payload_data and isinstance(payload_data["messages"], list):
                for m in payload_data["messages"]:
                    if isinstance(m, dict):
                        await handle_single_message_data(m)
            else:
                await handle_single_message_data(payload_data)

    except Exception as e:
        logger.error(f"Error en process_incoming_message: {e}", exc_info=True)

@app.post("/webhook/evolution")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    """Endpoint receptor del webhook de Evolution API."""
    try:
        data = await request.json()
        background_tasks.add_task(process_incoming_message, data)
        return {"status": "received"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/qr", response_class=HTMLResponse)
async def view_qr_code():
    """Muestra la página con el código QR de WhatsApp."""
    url = f"{EVOLUTION_URL}/instance/connect/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            data = resp.json()
            
            state = data.get("instance", {}).get("state") or data.get("state")
            if state == "open" or state == "connected":
                html = """
                <!DOCTYPE html>
                <html lang="es">
                <head>
                    <title>WhatsApp Eruvia - Conectado</title>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <style>
                        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #0f172a; color: #f8fafc; text-align: center; }
                        .card { background: #1e293b; padding: 3rem 2rem; border-radius: 1.5rem; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.6); max-width: 420px; width: 90%; border: 1px solid #334155; }
                        .check { font-size: 3.5rem; margin-bottom: 1rem; }
                        h1 { color: #34d399; font-size: 1.5rem; margin: 0 0 0.8rem; }
                        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; margin: 0 0 1.5rem; }
                        .badge { background: #065f46; color: #a7f3d0; padding: 0.5rem 1.2rem; border-radius: 9999px; font-weight: 600; font-size: 0.9rem; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="check">✅</div>
                        <h1>¡WhatsApp Conectado con Éxito!</h1>
                        <p>El Asistente Multilingüe de Eruvia European Business School está activo y sincronizando conversaciones en tiempo real con Odoo CRM.</p>
                        <span class="badge">Estado: 100% Operativo</span>
                    </div>
                </body>
                </html>
                """
                return HTMLResponse(content=html)
            
            base64_img = data.get("base64") or data.get("qrcode", {}).get("base64") or data.get("code")
            if not base64_img and "base64" in str(data):
                base64_img = data.get("instance", {}).get("qrcode")

            if base64_img:
                if not base64_img.startswith("data:image"):
                    base64_img = f"data:image/png;base64,{base64_img}"
                
                html = f"""
                <!DOCTYPE html>
                <html lang="es">
                <head>
                    <title>Vincular WhatsApp - Eruvia Business School</title>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <meta http-equiv="refresh" content="20">
                    <style>
                        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #0f172a; color: #f8fafc; text-align: center; }}
                        .card {{ background: #1e293b; padding: 2.5rem; border-radius: 1.5rem; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6); max-width: 440px; width: 90%; border: 1px solid #334155; }}
                        h1 {{ font-size: 1.4rem; margin: 0.5rem 0 0.4rem; color: #38bdf8; }}
                        p {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 1.5rem; line-height: 1.5; }}
                        .qr-box {{ background: #ffffff; padding: 1rem; border-radius: 1rem; display: inline-block; margin-bottom: 1.5rem; }}
                        img {{ width: 260px; height: 260px; display: block; }}
                        .footer-text {{ color: #64748b; font-size: 0.8rem; }}
                        .badge {{ background: #c59b27; color: #0b1c3d; padding: 0.4rem 1rem; border-radius: 9999px; font-size: 0.85rem; font-weight: 700; display: inline-block; margin-bottom: 1rem; }}
                    </style>
                </head>
                <body>
                    <div class="card">
                        <span class="badge">Eruvia AI WhatsApp Connector</span>
                        <h1>Vincular WhatsApp de Admisiones</h1>
                        <p>Abre WhatsApp en tu teléfono &gt; <b>Ajustes</b> &gt; <b>Dispositivos vinculados</b> &gt; <b>Vincular un dispositivo</b> y escanea:</p>
                        <div class="qr-box">
                            <img src="{base64_img}" alt="Código QR de WhatsApp" />
                        </div>
                        <div class="footer-text">La página se actualiza automáticamente cada 20 segundos. Contacto: info@eruviabs.com</div>
                    </div>
                </body>
                </html>
                """
                return HTMLResponse(content=html)
            
            return HTMLResponse(content="<h3>Generando código QR... por favor recarga en 5 segundos.</h3>")
    except Exception as e:
        return HTMLResponse(content=f"<h3>Error cargando QR: {str(e)}</h3>")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "eruvia-whatsapp-ai-bot"}

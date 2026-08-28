import os
import re
import json
import time
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

# Configuración IA (Llama-3.3-70B via Hugging Face Router PRO)
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://router.huggingface.co/v1").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "meta-llama/Llama-3.3-70B-Instruct:together")

# Cargar base de conocimiento
KB_PATH = Path(__file__).parent / "knowledge_base.md"
KNOWLEDGE_BASE = ""
if KB_PATH.exists():
    KNOWLEDGE_BASE = KB_PATH.read_text(encoding="utf-8")

# Memoria de conversación compacta (últimos 4 mensajes por número)
conversation_history: Dict[str, List[Dict[str, str]]] = {}

# Set de números pausados manualmente
paused_numbers: Dict[str, bool] = {}

# Cache de deduplicación de mensajes (msg_id -> timestamp)
processed_message_ids: Dict[str, float] = {}

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

def check_human_intervention_needed(text: str) -> bool:
    """Detecta si el mensaje del usuario pide expresamente hablar con un humano o asesor en varios idiomas."""
    patterns = [
        # Español
        r"\b(hablar con (un|una) persona|humano|asesor|agente)\b",
        r"\b(atenci[oó]n humana|persona real|alguien real|llamada|ll[aá]menme)\b",
        r"\b(quiero hablar con alguien|comunicarme con un asesor)\b",
        r"\b(transferir|pasar con un asesor|asesor comercial)\b",
        r"\b(/pausar|/humano)\b",
        # English
        r"\b(talk to a human|speak to a person|human agent|advisor|call me|real person|agent|support agent)\b",
        r"\b(transfer me|speak with someone)\b",
        # Português
        r"\b(falar com atendente|pessoa real|humano|consultor|me liga|falar com algu[eé]m)\b",
        # Français
        r"\b(parler [aà] un humain|conseiller|personne r[eé]elle|agent|appelez-moi)\b"
    ]
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)

def is_lead_paused_in_odoo(phone: str) -> bool:
    """
    Verifica si en Odoo CRM el lead activo tiene la etiqueta 'Intervención Humana' o 'Bot Pausado'.
    """
    try:
        uid, models = get_odoo_client()
        if not uid:
            return False

        clean_phone = re.sub(r"[^\d+]", "", phone)
        # Dominio correcto en una sola lista
        lead_domain = ["|", ("phone", "ilike", clean_phone[-9:]), ("mobile", "ilike", clean_phone[-9:]), ("probability", "<", 100)]
        lead_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search", [lead_domain], {"limit": 1})

        if lead_ids:
            lead = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "read", [lead_ids], {"fields": ["tag_ids"]})
            if lead:
                tags = lead[0].get("tag_ids", [])
                if tags:
                    tag_records = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.tag", "read", [tags], {"fields": ["name"]})
                    for t in tag_records:
                        name = t.get("name", "").lower()
                        if "humano" in name or "pausa" in name or "asesor" in name or "human" in name:
                            paused_numbers[phone] = True
                            return True

        paused_numbers[phone] = False
        return False
    except Exception as e:
        logger.error(f"Error verificando estado de pausa en Odoo: {e}")
        return False

def set_human_intervention_in_odoo(phone: str, sender_name: str, lead_id: int):
    """Marca la oportunidad en Odoo con etiqueta de Intervención Humana y alta prioridad."""
    try:
        uid, models = get_odoo_client()
        if not uid or not lead_id:
            return

        paused_numbers[phone] = True

        tag_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.tag", "search", [[("name", "ilike", "Intervención Humana")]], {"limit": 1})
        if not tag_ids:
            tag_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.tag", "create", [{"name": "Intervención Humana", "color": 1}])
        else:
            tag_id = tag_ids[0]

        models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "write", [[lead_id], {
            "priority": "3",
            "tag_ids": [(4, tag_id)]
        }])

        alert_body = f"""
        <div style="background:#fee2e2;border-left:4px solid #ef4444;padding:12px;border-radius:6px;margin:8px 0;">
            <p style="margin:0;color:#991b1b;font-weight:bold;font-size:14px;">🚨 ATENCIÓN REQUERIDA: Intervención Humana Solicitada</p>
            <p style="margin:5px 0 0;color:#7f1d1d;font-size:13px;">El alumno <b>{sender_name or phone}</b> ha solicitado hablar con un asesor. Las respuestas automáticas de la IA han sido <b>PAUSADAS</b> para este contacto.</p>
        </div>
        """
        models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "mail.message", "create", [{
            "model": "crm.lead",
            "res_id": lead_id,
            "body": alert_body,
            "message_type": "notification",
            "subtype_id": 1
        }])
        logger.info(f"Oportunidad {lead_id} marcada para Intervención Humana en Odoo.")
    except Exception as e:
        logger.error(f"Error marcando intervención humana en Odoo: {e}")

def remove_human_intervention_in_odoo(phone: str, lead_id: int):
    """Elimina las etiquetas de pausa en Odoo y reactiva la IA para el lead."""
    try:
        uid, models = get_odoo_client()
        if not uid or not lead_id:
            return

        paused_numbers[phone] = False

        lead = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "read", [[lead_id]], {"fields": ["tag_ids"]})
        if lead and lead[0].get("tag_ids"):
            tags = lead[0]["tag_ids"]
            tag_records = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.tag", "read", [tags], {"fields": ["name"]})
            tags_to_remove = []
            for t in tag_records:
                name = t.get("name", "").lower()
                if "humano" in name or "pausa" in name or "asesor" in name or "human" in name:
                    tags_to_remove.append(t["id"])
            
            if tags_to_remove:
                write_commands = [(3, tid) for tid in tags_to_remove]
                models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "write", [[lead_id], {"tag_ids": write_commands}])
                logger.info(f"Etiquetas de pausa removidas en Odoo para el lead {lead_id}.")
    except Exception as e:
        logger.error(f"Error removiendo etiquetas de pausa en Odoo: {e}")

def sync_with_odoo(phone: str, sender_name: str, message_text: str, is_bot_reply: bool = False) -> Optional[int]:
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
        
        domain = ["|", ("phone", "ilike", clean_phone[-9:]), ("mobile", "ilike", clean_phone[-9:])]
        partner_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [domain], {"limit": 1})
        
        if partner_ids:
            partner_id = partner_ids[0]
        else:
            partner_name = sender_name if sender_name else f"Prospecto WhatsApp {clean_phone}"
            partner_id = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [{
                "name": partner_name,
                "phone": clean_phone,
                "mobile": clean_phone,
                "comment": "Contacto generado automáticamente por el Asistente Multilingüe de WhatsApp de Eruvia."
            }])
            logger.info(f"Nuevo contacto creado en Odoo: ID {partner_id} ({partner_name})")

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
                "phone": clean_phone,
                "type": "opportunity",
                "expected_revenue": 799.0,
                "description": f"Primer mensaje recibido por WhatsApp:\n\n{message_text}"
            }])
            logger.info(f"Nueva oportunidad creada en Odoo CRM: ID {lead_id} ({lead_title})")

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
    """Genera una respuesta inteligente, consultiva y con máxima eficiencia de tokens."""
    client = OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)
    
    system_prompt = f"""Eres el Asesor Académico oficial de Eruvia European Business School (Your AI Native Business School, miembro de ANCYPEL).

DATOS CLAVE:
- MBA en IA: Título propio europeo 100% online y flexible (9 meses).
- Aval: Acreditación oficial ANCYPEL.
- Innovación: Tutor IA 24/7 + feedback docente diario + Masterclasses en vivo en HD.
- Precio: 799 € promocional (regular 999 €) o 6 cuotas de 133,17 €/mes sin intereses.
- Garantía: 14 días con devolución 100% (info@eruviabs.com).
- Web oficial: https://eruviabs.com/es

DIRECTRICES:
1. Responde de forma empática, profesional, cercana y directa en el MISMO idioma del usuario.
2. Formato conciso (1-2 párrafos cortos o viñetas limpias).
3. Haz una pregunta de cierre para guiar al alumno.
"""
    history = conversation_history.get(phone, [])[-4:]
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=250
        )
        msg_obj = response.choices[0].message
        reply = (msg_obj.content or "").strip()
        if not reply and hasattr(msg_obj, "reasoning_content") and msg_obj.reasoning_content:
            reply = msg_obj.reasoning_content.strip()

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        conversation_history[phone] = history[-4:]

        return reply
    except Exception as e:
        logger.error(f"Error llamando a LLM ({AI_BASE_URL} / {AI_MODEL}): {e}")
        return (
            "¡Hola! 👋 Qué gusto saludarte. Soy el asesor de admisiones de Eruvia European Business School.\n\n"
            "¿En qué te puedo ayudar hoy? ¿Te gustaría conocer más sobre el contenido y metodología de nuestro MBA en Inteligencia Artificial o sobre las opciones de financiación?"
        )

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
            "delay": 1000,
            "presence": "composing"
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            logger.info(f"Mensaje enviado a {number}. Status: {resp.status_code}")
    except Exception as e:
        logger.error(f"Error enviando mensaje por Evolution API: {e}")

async def handle_single_message_data(payload: Dict[str, Any]):
    """Procesa un objeto individual de mensaje recibido de WhatsApp con deduplicación estricta."""
    try:
        key = payload.get("key", {})
        
        # Ignorar mensajes salientes enviados por el propio bot
        if key.get("fromMe", False):
            return

        remote_jid = key.get("remoteJid", "")
        if not remote_jid or "@g.us" in remote_jid: # Ignorar grupos
            return

        msg_id = key.get("id", "")
        now = time.time()

        # 🛑 DEDUPLICACIÓN ESTRICTA: Si este mensaje ya se procesó en los últimos 5 minutos, ignorar
        if msg_id:
            if msg_id in processed_message_ids and (now - processed_message_ids[msg_id]) < 300:
                logger.info(f"Mensaje duplicado ignorado (msg_id={msg_id})")
                return
            processed_message_ids[msg_id] = now

        # Limpiar IDs viejos de la memoria de deduplicación
        if len(processed_message_ids) > 1000:
            for k in list(processed_message_ids.keys())[:200]:
                if now - processed_message_ids[k] > 300:
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

        logger.info(f"MENSAJE PROCESADO: {sender_name} ({phone_number}): {user_text}")

        # 1. Sincronizar mensaje entrante en Odoo CRM
        lead_id = sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=user_text, is_bot_reply=False)

        # 2. Comando para reactivar el bot si estaba pausado
        if user_text.lower() in ["/activar", "/bot", "/iniciar", "menu", "/start"]:
            paused_numbers[phone_number] = False
            if lead_id:
                remove_human_intervention_in_odoo(phone_number, lead_id)
            reactivate_msg = "¡Hola de nuevo! 👋 Asistente virtual de Eruvia reactivado. ¿En qué te puedo ayudar hoy sobre nuestro MBA o admisiones?"
            await send_whatsapp_message(number=remote_jid, text=reactivate_msg)
            sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=reactivate_msg, is_bot_reply=True)
            return

        # 3. Comprobar si el usuario solicita intervención humana por WhatsApp
        if check_human_intervention_needed(user_text):
            if lead_id:
                set_human_intervention_in_odoo(phone_number, sender_name, lead_id)
            pause_reply = "¡Entendido! He pausado mis respuestas automáticas y he notificado a nuestro equipo de admisiones. Un asesor humano de Eruvia revisará esta conversación y se comunicará contigo por aquí en breve. 👨‍💼✨"
            await send_whatsapp_message(number=remote_jid, text=pause_reply)
            sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=pause_reply, is_bot_reply=True)
            return

        # 4. Comprobar si la IA está pausada desde Odoo (por etiqueta en CRM)
        if is_lead_paused_in_odoo(phone_number):
            logger.info(f"Bot pausado para {phone_number} (Control humano activo en Odoo). Mensaje registrado en CRM.")
            return

        # 5. Generar respuesta con IA eficiente
        ai_reply = await generate_ai_reply(phone_number, user_text)

        # 6. Enviar respuesta por WhatsApp
        await send_whatsapp_message(number=remote_jid, text=ai_reply)

        # 7. Registrar respuesta del Bot en el Chatter de Odoo
        sync_with_odoo(phone=phone_number, sender_name=sender_name, message_text=ai_reply, is_bot_reply=True)

    except Exception as e:
        logger.error(f"Error procesando mensaje individual: {e}", exc_info=True)

async def process_incoming_message(data: Dict[str, Any]):
    """Procesa el webhook entrante desde Evolution API soportando todas las estructuras."""
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
        logger.error(f"Error procesando webhook general: {e}", exc_info=True)

@app.post("/webhook/evolution")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    """Endpoint receptor robusto del webhook de Evolution API."""
    try:
        raw_body = await request.body()
        if not raw_body:
            return {"status": "empty"}
        try:
            data = json.loads(raw_body.decode("utf-8", errors="replace"))
        except Exception:
            data = await request.json()
    except Exception as e:
        logger.error(f"Error parseando JSON del webhook: {e}")
        return {"status": "error", "detail": str(e)}

    background_tasks.add_task(process_incoming_message, data)
    return {"status": "received"}

@app.get("/qr", response_class=HTMLResponse)
async def view_qr_code():
    """Muestra una página web corporativa con el código QR de WhatsApp listo para vincular."""
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

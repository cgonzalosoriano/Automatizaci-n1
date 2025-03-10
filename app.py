from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

# Diccionario global para almacenar historial, estado y configuración por usuario (número de teléfono)
conversaciones = {}

# Configurar OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# ----------------------------
# CONFIGURACIÓN DE GOOGLE SHEETS
# ----------------------------
def get_sheet_client():
    """Retorna un cliente de gspread autorizado."""
    creds_json = json.loads(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    client = gspread.service_account_from_dict(creds_json)
    return client

def obtener_hoja(nombre_hoja):
    """Obtiene una hoja específica de Google Sheets."""
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        return spreadsheet.worksheet(nombre_hoja)
    except Exception as e:
        print(f"Error al obtener la hoja {nombre_hoja}:", e)
        return None

# ----------------------------
# CONFIGURACIÓN DE GOOGLE CALENDAR
# ----------------------------
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    """Retorna un servicio de Google Calendar autorizado usando credenciales de servicio."""
    creds_json = json.loads(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def crear_evento(summary, start_datetime, end_datetime):
    """Crea un evento en Google Calendar."""
    service = get_calendar_service()
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")
    
    event = {
        'summary': summary,
        'start': {
            'dateTime': start_datetime,
            'timeZone': 'America/Argentina/Buenos_Aires',
        },
        'end': {
            'dateTime': end_datetime,
            'timeZone': 'America/Argentina/Buenos_Aires',
        },
    }
    try:
        event = service.events().insert(calendarId=calendar_id, body=event).execute()
        return f"Evento creado: {event.get('htmlLink')}"
    except Exception as e:
        print("Error al crear evento:", e)
        return "Hubo un error al crear el evento. Por favor, verifica los permisos."

def listar_eventos():
    """Lista los eventos del calendario."""
    service = get_calendar_service()
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")
    
    try:
        now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indica UTC
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        if not events:
            return "No hay eventos próximos."
        
        respuesta = "Eventos próximos:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            respuesta += f"- {event['summary']} (Fecha: {start})\n"
        return respuesta
    except Exception as e:
        print("Error al listar eventos:", e)
        return "Hubo un error al listar los eventos."

# ----------------------------
# FUNCIONES PARA CHATGPT MULTI-TURN
# ----------------------------
def armar_system_prompt(estilo):
    """Crea el system prompt según el estilo (ej. serio, chistes, amable)."""
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    base = f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: America/Argentina/Buenos_Aires). "
    if estilo == "serio":
        base += "Tu tono es formal y serio."
    elif estilo == "chistes":
        base += "Tu tono es divertido y haces chistes."
    elif estilo == "amable":
        base += "Eres muy amable y afectuoso."
    else:
        base += "Responde de forma neutral."
    return {"role": "system", "content": base}

def chatgpt_con_historial(user_data, mensaje_extra=None):
    """Llama a ChatGPT con el historial completo y un system prompt según el estilo."""
    estilo = user_data.get("estilo", None)
    system_prompt = armar_system_prompt(estilo)
    historial = user_data["historial"].copy()
    mensajes = [system_prompt]
    if mensaje_extra:
        mensajes.append({"role": "system", "content": mensaje_extra})
    mensajes += historial
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=mensajes,
            temperature=0.7,
            max_tokens=250
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error en chatgpt_con_historial:", e)
        return "Lo siento, hubo un error llamando a ChatGPT."

# ----------------------------
# RUTA DE WHATSAPP
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.form.get("From")
    incoming_msg = request.form.get("Body", "").strip()
    print(">>> Mensaje entrante:", incoming_msg)

    # Inicializar datos de conversación para el usuario si no existen.
    if from_number not in conversaciones:
        conversaciones[from_number] = {
            "historial": [],
            "estado": None,
            "estilo": None,
            "preferencias": {}
        }
    user_data = conversaciones[from_number]
    user_data["historial"].append({"role": "user", "content": incoming_msg})

    # Configurar el estilo si se solicita
    if "configurar estilo:" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            user_data["estilo"] = partes[1].strip().lower()
            respuesta = f"¡Estilo configurado a '{user_data['estilo']}'!"
            user_data["historial"].append({"role": "assistant", "content": respuesta})
            return responder_whatsapp(respuesta)

    # Interpretar comandos estructurados
    if "agregar evento:" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            descripcion = partes[1].strip()
            respuesta = crear_evento(descripcion, "2023-12-31T10:00:00", "2023-12-31T11:00:00")  # Fechas de ejemplo
        else:
            respuesta = "Por favor, proporciona una descripción para el evento."
    elif "listar eventos" in incoming_msg.lower():
        respuesta = listar_eventos()
    else:
        respuesta = chatgpt_con_historial(user_data)

    user_data["historial"].append({"role": "assistant", "content": respuesta})
    return responder_whatsapp(respuesta)

def responder_whatsapp(texto):
    resp = MessagingResponse()
    resp.message(texto)
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

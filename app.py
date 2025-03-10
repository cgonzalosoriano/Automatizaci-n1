from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from datetime import datetime, date, timedelta
import gspread

app = Flask(__name__)

# Diccionario global: por cada número de teléfono, guardamos historial y estado.
conversaciones = {}

# Configuración de OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# ------------------------------------------------
# CONFIGURACIÓN DE GOOGLE SHEETS
# ------------------------------------------------

def get_sheet_client():
    """Retorna un cliente de gspread autorizado, usando credenciales en variable de entorno."""
    creds_json = json.loads(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    client = gspread.service_account_from_dict(creds_json)
    return client

def crear_hoja(sheet_name):
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        return f"Hoja '{sheet_name}' creada con éxito."
    except Exception as e:
        print("Error al crear hoja:", e)
        return "Error al crear la hoja."

def borrar_hoja(sheet_name):
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        worksheet = spreadsheet.worksheet(sheet_name)
        spreadsheet.del_worksheet(worksheet)
        return f"Hoja '{sheet_name}' borrada con éxito."
    except Exception as e:
        print("Error al borrar hoja:", e)
        return "Error al borrar la hoja."

def listar_hojas():
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        worksheets = spreadsheet.worksheets()
        nombres = [ws.title for ws in worksheets]
        return "Hojas disponibles: " + ", ".join(nombres)
    except Exception as e:
        print("Error al listar hojas:", e)
        return "Error al listar las hojas."

# ------------------------------------------------
# (Comentado) Integración con Google Calendar
# ------------------------------------------------
# TIMEZONE = "America/Argentina/Buenos_Aires"
# CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"

# def get_calendar_service():
#     ...
# def crear_evento(...):
#     ...
# def listar_eventos_por_rango(...):
#     ...
# def actualizar_evento(...):
#     ...
# def borrar_evento(...):
#     ...

# ------------------------------------------------
# CHATGPT MULTI-TURN
# ------------------------------------------------

TIMEZONE = "America/Argentina/Buenos_Aires"

def armar_system_prompt(estilo):
    """
    Crea el system prompt según el estilo y fecha/hora actual.
    'estilo' puede ser 'serio', 'chistes', 'amable', etc.
    """
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    base = f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). "
    if estilo == "serio":
        base += "Tu tono es formal y serio."
    elif estilo == "chistes":
        base += "Tu tono es divertido y sueles hacer chistes."
    elif estilo == "amable":
        base += "Eres muy amable y afectuoso."
    else:
        base += "Responde de forma neutral."
    return {"role": "system", "content": base}

def chatgpt_con_historial(user_data, mensaje_extra=None):
    """
    Llama a ChatGPT con el historial completo, más un system prompt con estilo y fecha/hora.
    'mensaje_extra' se añade como system prompt adicional (opcional).
    """
    estilo = user_data.get("estilo", None)
    system_prompt = armar_system_prompt(estilo)

    historial = user_data["historial"].copy()  # Copiamos el historial del usuario
    mensajes = [system_prompt]
    if mensaje_extra:
        mensajes.append({"role": "system", "content": mensaje_extra})
    mensajes += historial

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=mensajes,
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error en chatgpt_con_historial:", e)
        return "Lo siento, hubo un error llamando a ChatGPT."

# ------------------------------------------------
# FUNCIÓN ÚNICA DE INTERPRETACIÓN
# ------------------------------------------------
def interpretar_instruccion(user_message):
    """
    Usa ChatGPT para interpretar instrucciones tanto de Google Sheets como de Google Calendar (desactivado) 
    o simplemente 'other' (conversación).
    Devuelve un JSON del estilo:
    {
      "modulo": "sheet" | "calendar" | "other",
      "action": "create" | "list" | "update" | "delete" | "other",
      "sheet_name": "...",
      "event_id": "...",
      "summary": "...",
      "start_datetime": "...",
      "end_datetime": "...",
      "time_range_start": "...",
      "time_range_end": "..."
    }
    """
    try:
        system_prompt = (
            "Eres un asistente que maneja Google Sheets y Google Calendar. "
            "Si detectas que el usuario quiere crear, borrar o listar una hoja, devuelves un JSON con: "
            "{\"modulo\":\"sheet\", \"action\":\"create\"|\"delete\"|\"list\", \"sheet_name\":\"...\"}. "
            "Si detectas que quiere crear, listar, actualizar o borrar un evento de Calendar, devuelves un JSON con: "
            "{\"modulo\":\"calendar\", \"action\":\"create\"|\"list\"|\"update\"|\"delete\", \"event_id\":\"...\", etc.} "
            "Si no estás seguro, 'modulo':'other','action':'other'. No añadas texto adicional, solo el JSON."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,
            max_tokens=200
        )
        raw_content = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)
        data = json.loads(raw_content)
        return data
    except Exception as e:
        print("Error en interpretar_instruccion:", e)
        return {"modulo": "other", "action": "other"}

# ------------------------------------------------
# RUTA DE WHATSAPP
# ------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.form.get("From")
    incoming_msg = request.form.get("Body", "").strip()
    print(">>> Mensaje entrante:", incoming_msg)

    if from_number not in conversaciones:
        conversaciones[from_number] = {
            "historial": [],
            "estado": None,
            "estilo": None
        }
    user_data = conversaciones[from_number]
    user_data["historial"].append({"role": "user", "content": incoming_msg})

    # 1) Revisar si el usuario quiere configurar el estilo
    if "configurar estilo:" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            user_data["estilo"] = partes[1].strip().lower()
            respuesta = f"¡Estilo configurado a '{user_data['estilo']}'!"
            user_data["historial"].append({"role": "assistant", "content": respuesta})
            return responder_whatsapp(respuesta)

    # 2) Interpretar instrucción unificada
    instruccion = interpretar_instruccion(incoming_msg)
    modulo = instruccion.get("modulo", "other")
    action = instruccion.get("action", "other")
    print(">>> Modulo:", modulo, "Action:", action)

    if modulo == "sheet":
        # Manejar Google Sheets
        if action == "create":
            sheet_name = instruccion.get("sheet_name", "HojaNueva")
            respuesta = crear_hoja(sheet_name)
        elif action == "delete":
            sheet_name = instruccion.get("sheet_name", "")
            if sheet_name:
                respuesta = borrar_hoja(sheet_name)
            else:
                respuesta = "No especificaste qué hoja borrar."
        elif action == "list":
            respuesta = listar_hojas()
        else:
            # No reconocemos la acción => conversacion libre
            respuesta = chatgpt_con_historial(user_data)
    elif modulo == "calendar":
        # Integración con Calendar está desactivada
        # Podrías comentar o poner un msg
        if action in ["create","list","update","delete"]:
            respuesta = "La integración con Google Calendar está desactivada por el momento."
        else:
            respuesta = chatgpt_con_historial(user_data)
    else:
        # Módulo other => conversacion libre
        respuesta = chatgpt_con_historial(user_data)

    user_data["historial"].append({"role": "assistant", "content": respuesta})
    return responder_whatsapp(respuesta)

def responder_whatsapp(texto):
    resp = MessagingResponse()
    resp.message(texto)
    return Response(str(resp), mimetype="application/xml")

# FIN
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)




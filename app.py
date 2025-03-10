from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from datetime import datetime, date, timedelta
# Importar gspread para Google Sheets
import gspread

app = Flask(__name__)

# Diccionario global para almacenar historial, estado y configuración por usuario (número de teléfono)
conversaciones = {}

# Configurar OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# ----------------------------
# CONFIGURACIÓN DE GOOGLE SHEETS
# ----------------------------
# Usaremos Google Sheets en lugar de Calendar (temporalmente desactivamos Calendar)
# Configura las credenciales y la clave de la hoja de cálculo en variables de entorno:
# GOOGLE_SHEETS_CREDENTIALS: contenido JSON de la cuenta de servicio
# SHEET_SPREADSHEET_KEY: la clave de la hoja de cálculo

def get_sheet_client():
    """Retorna un cliente de gspread autorizado."""
    creds_json = json.loads(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
    client = gspread.service_account_from_dict(creds_json)
    return client

def crear_hoja(sheet_name):
    """Crea una nueva hoja en el archivo de Google Sheets."""
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        # Se crea una nueva hoja con 100 filas y 20 columnas (ajusta según necesidad)
        spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        return f"Hoja '{sheet_name}' creada con éxito."
    except Exception as e:
        print("Error al crear hoja:", e)
        return "Error al crear la hoja."

def borrar_hoja(sheet_name):
    """Borra una hoja existente en el archivo de Google Sheets."""
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
    """Lista los nombres de todas las hojas en el archivo de Google Sheets."""
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        worksheets = spreadsheet.worksheets()
        nombres = [ws.title for ws in worksheets]
        return "Hojas disponibles: " + ", ".join(nombres)
    except Exception as e:
        print("Error al listar hojas:", e)
        return "Error al listar las hojas."

# ----------------------------
# FUNCIONES PARA CHATGPT MULTI-TURN Y PARA CALENDARIO
# ----------------------------

# Variable de ejemplo para Calendar (aunque está desactivado en este ejemplo)
TIMEZONE = "America/Argentina/Buenos_Aires"
# Si en el futuro se reactiva, se usará esta ID
# CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"

def armar_system_prompt(estilo):
    """Crea el system prompt según el estilo (ej. serio, chistes, amable)."""
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    base = f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). "
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

def interpretar_instruccion_evento(user_message):
    """
    Usa ChatGPT para interpretar instrucciones de calendario en español.
    Devuelve un JSON con la siguiente estructura (solo incluye las claves que apliquen):
    {
      "action": "create" | "list" | "update" | "delete" | "other",
      "summary": "...",
      "start_datetime": "YYYY-MM-DDTHH:MM:SS",
      "end_datetime": "YYYY-MM-DDTHH:MM:SS",
      "event_id": "...",
      "time_range_start": "YYYY-MM-DD",
      "time_range_end": "YYYY-MM-DD"
    }
    No añadas texto adicional, solo el JSON.
    """
    try:
        system_prompt = (
            "Eres un asistente que interpreta instrucciones de calendario en español. "
            "El usuario puede querer crear, listar, actualizar o borrar eventos. "
            "Devuelve un JSON con la siguiente estructura (solo las claves que apliquen): "
            "{"
            "  \"action\": \"create\"|\"list\"|\"update\"|\"delete\"|\"other\", "
            "  \"summary\": \"...\", "
            "  \"start_datetime\": \"YYYY-MM-DDTHH:MM:SS\", "
            "  \"end_datetime\": \"YYYY-MM-DDTHH:MM:SS\", "
            "  \"event_id\": \"...\", "
            "  \"time_range_start\": \"YYYY-MM-DD\", "
            "  \"time_range_end\": \"YYYY-MM-DD\" "
            "}. "
            "No añadas texto adicional, solo el JSON."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,
            max_tokens=300
        )
        raw_content = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)
        data = json.loads(raw_content)
        return data
    except Exception as e:
        print("Error en interpretar_instruccion_evento:", e)
        return {"action": "other"}

def obtener_rango_fechas_con_chatgpt(user_message):
    """
    Usa ChatGPT para extraer un rango de fechas en formato YYYY-MM-DD, asumiendo el año actual.
    Devuelve un objeto JSON con "start_date" y "end_date".
    """
    try:
        hoy = date.today().isoformat()  # Ej: "2023-11-25"
        system_prompt = (
            f"Hoy es {hoy}. Eres un asistente para interpretar rangos de fechas. "
            "El usuario te dará una frase como 'eventos de mañana' o 'eventos de la próxima semana'. "
            "Responde únicamente con un objeto JSON con 'start_date' y 'end_date' en formato YYYY-MM-DD, asumiendo el año actual. "
            "No añadas texto adicional. Ejemplo: {\"start_date\": \"2023-11-26\", \"end_date\": \"2023-11-26\"}."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,
            max_tokens=100
        )
        raw_content = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)
        fecha_info = json.loads(raw_content)
        if "start_date" not in fecha_info or "end_date" not in fecha_info:
            raise ValueError("No se encontró 'start_date' o 'end_date'.")
        return fecha_info
    except Exception as e:
        print("Error al obtener rango de fechas con ChatGPT:", e)
        hoy_str = str(date.today())
        return {"start_date": hoy_str, "end_date": hoy_str}

# ----------------------------
# (Comentado: Integración de Google Calendar)
# ----------------------------
# Las funciones de Google Calendar se mantienen aquí, pero desactivadas.
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

# ----------------------------
# FUNCIONES PARA GOOGLE SHEETS
# ----------------------------

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

def get_sheet_client():
    try:
        creds_json = json.loads(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))
        client = gspread.service_account_from_dict(creds_json)
        return client
    except Exception as e:
        print("Error en get_sheet_client:", e)
        raise

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
            "eventos_disponibles": [],
            "chosen_event_id": None
        }
    user_data = conversaciones[from_number]
    user_data["historial"].append({"role": "user", "content": incoming_msg})

    # Primero, revisar si el usuario quiere configurar el estilo.
    if "configurar estilo:" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            user_data["estilo"] = partes[1].strip().lower()
            respuesta = f"¡Estilo configurado a '{user_data['estilo']}'!"
            user_data["historial"].append({"role": "assistant", "content": respuesta})
            return responder_whatsapp(respuesta)

    # Luego, verificar si el mensaje se refiere a hojas de cálculo.
    instruccion_hoja = interpretar_instruccion_hoja(incoming_msg)
    sheet_action = instruccion_hoja.get("action", "other")
    if sheet_action != "other":
        if sheet_action == "create_sheet":
            sheet_name = instruccion_hoja.get("sheet_name", "Hoja nueva")
            respuesta = crear_hoja(sheet_name)
        elif sheet_action == "delete_sheet":
            sheet_name = instruccion_hoja.get("sheet_name", "")
            if not sheet_name:
                respuesta = "No se especificó el nombre de la hoja a borrar."
            else:
                respuesta = borrar_hoja(sheet_name)
        elif sheet_action == "list_sheets":
            respuesta = listar_hojas()
        user_data["historial"].append({"role": "assistant", "content": respuesta})
        return responder_whatsapp(respuesta)

    # Finalmente, interpretamos la instrucción de calendario.
    # (Como desactivamos Google Calendar, redirigimos a Sheets o conversación libre.)
    instruccion = interpretar_instruccion_evento(incoming_msg)
    action = instruccion.get("action", "other")
    print(">>> Acción detectada:", action)

    if action in ["create", "list", "update", "delete"]:
        respuesta = "La integración con Google Calendar está desactivada. Por favor, usa la funcionalidad de Google Sheets para almacenar datos."
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


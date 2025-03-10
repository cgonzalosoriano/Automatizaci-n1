from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from datetime import datetime
import gspread

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

def crear_hoja(sheet_name):
    """Crea una nueva hoja en Google Sheets."""
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        return f"Hoja '{sheet_name}' creada con éxito."
    except Exception as e:
        print("Error al crear hoja:", e)
        return "Error al crear la hoja."

def borrar_hoja(sheet_name):
    """Borra una hoja existente en Google Sheets."""
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
    """Lista los nombres de todas las hojas en Google Sheets."""
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

TIMEZONE = "America/Argentina/Buenos_Aires"

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

def interpretar_instruccion_hoja(user_message):
    """
    Usa ChatGPT para interpretar instrucciones sobre hojas de cálculo.
    Devuelve un JSON con:
      {
         "action": "create_sheet" | "delete_sheet" | "list_sheets" | "other",
         "sheet_name": "..."
      }
    """
    try:
        system_prompt = (
            "Eres un asistente que interpreta instrucciones para manejar hojas de cálculo de Google Sheets. "
            "El usuario puede querer crear, borrar o listar hojas. "
            "Devuelve un JSON con la siguiente estructura: {\"action\": \"create_sheet\"|\"delete_sheet\"|\"list_sheets\"|\"other\", "
            "\"sheet_name\": \"...\"} "
            "Solo incluye las claves que apliquen, sin texto adicional."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,
            max_tokens=150
        )
        raw_content = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)
        data = json.loads(raw_content)
        return data
    except Exception as e:
        print("Error en interpretar_instruccion_hoja:", e)
        return {"action": "other"}

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
            "chosen_event_id": None,
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

    # Interpretar instrucciones de hojas de cálculo
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

    # Si no es una instrucción de hoja, usar ChatGPT para responder
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

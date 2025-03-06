from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import date, datetime

app = Flask(__name__)

# Configura tu API Key de OpenAI desde una variable de entorno
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Define el alcance para Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar']

def obtener_respuesta_chatgpt(prompt):
    """
    Responde con ChatGPT en modo genérico, pero incluyendo la fecha y hora actuales
    en el system prompt para que ChatGPT pueda usarlas si el usuario pregunta.
    """
    try:
        # Obtén la fecha y hora actual
        now = datetime.now()
        fecha_str = now.strftime("%d de %B de %Y")
        hora_str = now.strftime("%H:%M")

        # Mensaje de sistema que informa a ChatGPT la fecha y hora actuales
        system_prompt = (
            f"Eres un asistente que conoce la fecha y hora actuales. "
            f"Hoy es {fecha_str} y son las {hora_str}. "
            "Responde en español. Si el usuario pregunta '¿Qué fecha es hoy?' o '¿Qué hora es?', usa estos datos."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print("Error al obtener respuesta de ChatGPT:", e)
        return "Lo siento, hubo un error procesando tu solicitud."

def obtener_intencion(user_message):
    """
    Usa ChatGPT para decidir la intención del usuario: create, list o other.
    Responde con un JSON { "intention": "create" } o { "intention": "list" } o { "intention": "other" }.
    """
    try:
        system_prompt = (
            "Eres un asistente que clasifica la intención del usuario con respecto a eventos. "
            "Si el usuario quiere crear un evento, responde con un JSON: {\"intention\": \"create\"}. "
            "Si el usuario quiere ver o listar eventos, responde con un JSON: {\"intention\": \"list\"}. "
            "Si no está claro, responde {\"intention\": \"other\"}. "
            "No añadas texto extra."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,  # determinista
            max_tokens=50
        )
        raw_content = response.choices[0].message.content.strip()

        # Aislar un bloque JSON con regex en caso de que ChatGPT añada texto extra
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)

        data = json.loads(raw_content)
        return data.get("intention", "other")
    except Exception as e:
        print("Error al obtener intención:", e)
        return "other"

def obtener_rango_fechas_con_chatgpt(user_message):
    """
    Usa ChatGPT para extraer un rango de fechas en formato YYYY-MM-DD.
    Devuelve un objeto JSON con "start_date" y "end_date".
    """
    try:
        system_prompt = (
            "Eres un asistente para interpretar rangos de fechas. "
            "El usuario te dará una frase relacionada con fechas (por ejemplo, 'eventos de mañana' o 'eventos de la próxima semana'). "
            "Responde únicamente con un objeto JSON que contenga las claves 'start_date' y 'end_date' en formato YYYY-MM-DD. "
            "No incluyas texto adicional ni explicaciones, solo el objeto JSON. "
            "Ejemplo: {\"start_date\": \"2025-03-10\", \"end_date\": \"2025-03-12\"}."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.0,  # Más determinista
            max_tokens=100
        )
        raw_content = response.choices[0].message.content.strip()

        # Intenta aislar un bloque JSON con una expresión regular
        match = re.search(r'\{.*\}', raw_content)
        if match:
            raw_content = match.group(0)

        fecha_info = json.loads(raw_content)

        # Validamos que contenga 'start_date' y 'end_date'
        if "start_date" not in fecha_info or "end_date" not in fecha_info:
            raise ValueError("No se encontró 'start_date' o 'end_date' en el JSON.")

        return fecha_info

    except Exception as e:
        print("Error al obtener rango de fechas con ChatGPT:", e)
        # Si falla, devolvemos un rango por defecto (hoy).
        hoy_str = str(date.today())
        return {"start_date": hoy_str, "end_date": hoy_str}

def get_calendar_service():
    # Lee el contenido del JSON desde la variable de entorno GOOGLE_CREDENTIALS
    credentials_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=credentials)
    return service

def listar_eventos_por_rango(start_date, end_date):
    """
    Lista eventos en el rango dado. start_date y end_date deben estar en formato YYYY-MM-DD.
    """
    try:
        service = get_calendar_service()
        # Reemplaza con la ID real de tu calendario
        calendar_id = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
        time_min = f"{start_date}T00:00:00Z"
        time_max = f"{end_date}T23:59:59Z"
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return f"No se encontraron eventos entre {start_date} y {end_date}."
        event_list = ''
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'Sin título')
            event_list += f"{start} - {summary}\n"
        return event_list
    except Exception as e:
        print("Error al listar eventos por rango:", e)
        return "Hubo un error al obtener los eventos del calendario."

def crear_evento(summary, start_datetime, end_datetime):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_datetime,  # Ejemplo: "2025-03-10T11:00:00"
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
            'end': {
                'dateTime': end_datetime,    # Ejemplo: "2025-03-10T12:00:00"
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
        }
        # Reemplaza con la ID real de tu calendario
        calendar_id = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()
        return f"Evento creado con éxito: {created_event.get('htmlLink')}"
    except Exception as e:
        print("Error al crear evento:", e)
        return "Hubo un error al crear el evento."

@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    print(">>> Llegó una petición a /whatsapp")
    incoming_msg = request.form.get("Body", "").strip()
    print(f">>> Mensaje entrante: {incoming_msg}")

    # 1. Determinar intención con ChatGPT
    intention = obtener_intencion(incoming_msg)
    print(f">>> Intención detectada: {intention}")

    if intention == "create":
        # Crear evento (por ahora, datos fijos)
        respuesta = crear_evento(
            summary="Evento de prueba",
            start_datetime="2025-03-10T11:00:00",
            end_datetime="2025-03-10T12:00:00"
        )
    elif intention == "list":
        # Extraer rango de fechas con ChatGPT
        fecha_info = obtener_rango_fechas_con_chatgpt(incoming_msg)
        start_date = fecha_info["start_date"]
        end_date = fecha_info["end_date"]
        respuesta = listar_eventos_por_rango(start_date, end_date)
    else:
        # Cualquier otra cosa, ChatGPT genérico (incluye fecha/hora actual en el prompt)
        respuesta = obtener_respuesta_chatgpt(incoming_msg)

    resp = MessagingResponse()
    resp.message(respuesta)
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)



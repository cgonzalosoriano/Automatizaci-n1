from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, date

app = Flask(__name__)

# Diccionario global para almacenar el historial de conversación por número de teléfono
conversaciones = {}

# Configura tu API Key de OpenAI desde una variable de entorno
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Define el alcance para Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Reemplaza con la ID real de tu calendario
CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
TIMEZONE = "America/Argentina/Buenos_Aires"


def obtener_respuesta_generica(prompt):
    """
    Respuesta genérica de ChatGPT, incluyendo la fecha y hora actuales
    para que ChatGPT pueda responder preguntas tipo "¿Qué día es hoy? y todo otro tipo de preguntas".
    """
    try:
        now = datetime.now()
        fecha_str = now.strftime("%d de %B de %Y")
        hora_str = now.strftime("%H:%M")

        system_prompt = (
            f"Eres un asistente que conoce la fecha y hora actuales. "
            f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). "
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
        print("Error en obtener_respuesta_generica:", e)
        return "Lo siento, no puedo procesar tu solicitud."


def obtener_intencion(user_message):
    """
    Usa ChatGPT para interpretar la intención del usuario respecto a eventos.
    Retorna un JSON con la estructura:
      {
        "action": "create" | "list" | "update" | "delete" | "other",
        "summary": "...",
        "start_datetime": "YYYY-MM-DDTHH:MM:SS",
        "end_datetime": "YYYY-MM-DDTHH:MM:SS",
        "event_id": "...",
        "time_range_start": "YYYY-MM-DD",
        "time_range_end": "YYYY-MM-DD"
      }
    """
    try:
        system_prompt = (
            "Eres un asistente que interpreta instrucciones del usuario en español. Pueden ser solicitudes de calendario, ordenes y todo otro tipo de asistencias "
            "El usuario puede querer crear, listar, actualizar o borrar eventos. "
            "Devuelve un JSON con la siguiente estructura (solo llaves que apliquen): "
            "{"
            "  \"action\": \"create\"|\"list\"|\"update\"|\"delete\"|\"other\", "
            "  \"summary\": \"...\", "
            "  \"start_datetime\": \"YYYY-MM-DDTHH:MM:SS\", "
            "  \"end_datetime\": \"YYYY-MM-DDTHH:MM:SS\", "
            "  \"event_id\": \"...\", "
            "  \"time_range_start\": \"YYYY-MM-DD\", "
            "  \"time_range_end\": \"YYYY-MM-DD\" "
            "}. "
            "Añade un texto adicional breve pero sin explicaciones, solo el JSON."
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
    Usa ChatGPT para extraer un rango de fechas en formato YYYY-MM-DD.
    Devuelve un objeto JSON con "start_date" y "end_date", asumiendo el año actual.
    """
    try:
        hoy = date.today().isoformat()  # Por ejemplo, "2025-11-25"
        system_prompt = (
            f"Hoy es {hoy}. Eres un asistente para interpretar rangos de fechas. siempre ten en cuenta la fecha e interpretar las instrucciones del usuario. "
            "El usuario te dará una frase relacionada con fechas (por ejemplo, 'eventos de mañana' o 'eventos de la próxima semana'). "
            "Responde únicamente con un objeto JSON que contenga las claves 'start_date' y 'end_date' en formato YYYY-MM-DD, asumiendo la fecha actual. Si te falta algún dato, preguntale al usuario para completar la información necesaria. "
            "No añadas texto adicional ni explicaciones, solo el objeto JSON. "
            "Ejemplo: {\"start_date\": \"2023-11-26\", \"end_date\": \"2023-11-26\"}."
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
            raise ValueError("No se encontró 'start_date' o 'end_date' en el JSON.")
        return fecha_info
    except Exception as e:
        print("Error al obtener rango de fechas con ChatGPT:", e)
        hoy_str = str(date.today())
        return {"start_date": hoy_str, "end_date": hoy_str}


def get_calendar_service():
    credentials_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    service = build('calendar', 'v3', credentials=credentials)
    return service


def crear_evento(summary, start_datetime, end_datetime):
    try:
        service = get_calendar_service()
        event = {
            'summary': summary or "Evento sin título",
            'start': {
                'dateTime': start_datetime,
                'timeZone': TIMEZONE
            },
            'end': {
                'dateTime': end_datetime,
                'timeZone': TIMEZONE
            }
        }
        created_event = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event
        ).execute()
        return f"Evento creado con éxito: {created_event.get('htmlLink')}"
    except Exception as e:
        print("Error al crear evento:", e)
        return "Hubo un error al crear el evento."


def listar_eventos_por_rango(start_date, end_date):
    try:
        service = get_calendar_service()
        time_min = f"{start_date}T00:00:00"
        time_max = f"{end_date}T23:59:59"
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min + "Z",
            timeMax=time_max + "Z",
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return f"No se encontraron eventos entre {start_date} y {end_date}."
        resp = ""
        for ev in events:
            start = ev['start'].get('dateTime', ev['start'].get('date'))
            title = ev.get('summary', 'Sin título')
            event_id = ev.get('id', '')
            resp += f"{start} - {title} (ID: {event_id})\n"
        return resp
    except Exception as e:
        print("Error al listar eventos:", e)
        return "Hubo un error al obtener los eventos del calendario."


def actualizar_evento(event_id, summary=None, start_datetime=None, end_datetime=None):
    try:
        service = get_calendar_service()
        event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
        if summary:
            event['summary'] = summary
        if start_datetime:
            event['start'] = {
                'dateTime': start_datetime,
                'timeZone': TIMEZONE
            }
        if end_datetime:
            event['end'] = {
                'dateTime': end_datetime,
                'timeZone': TIMEZONE
            }
        updated_event = service.events().update(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            body=event
        ).execute()
        return f"Evento actualizado con éxito: {updated_event.get('htmlLink')}"
    except Exception as e:
        print("Error al actualizar evento:", e)
        return "Hubo un error al actualizar el evento."


def borrar_evento(event_id):
    try:
        service = get_calendar_service()
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return "Evento borrado con éxito."
    except Exception as e:
        print("Error al borrar evento:", e)
        return "Hubo un error al borrar el evento."


@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.form.get("From")  # Número del usuario
    incoming_msg = request.form.get("Body", "").strip()
    print(">>> Mensaje entrante:", incoming_msg)

    # Inicializar historial para el usuario si no existe
    if from_number not in conversaciones:
        conversaciones[from_number] = []

    # Agregar el mensaje del usuario al historial
    conversaciones[from_number].append({"role": "user", "content": incoming_msg})

    # Interpretar la instrucción de calendario
    instruccion = interpretar_instruccion_evento(incoming_msg)
    action = instruccion.get("action", "other")
    print(">>> Acción detectada:", action)

    if action == "create":
        summary = instruccion.get("summary", "Evento sin título")
        start_dt = instruccion.get("start_datetime", "2025-03-10T11:00:00")
        end_dt = instruccion.get("end_datetime", "2025-03-10T12:00:00")
        respuesta = crear_evento(summary, start_dt, end_dt)
    elif action == "list":
        start_range = instruccion.get("time_range_start", str(date.today()))
        end_range = instruccion.get("time_range_end", str(date.today()))
        respuesta = listar_eventos_por_rango(start_range, end_range)
    elif action == "update":
        event_id = instruccion.get("event_id")
        if not event_id:
            respuesta = "No se encontró el 'event_id' para actualizar."
        else:
            summary = instruccion.get("summary")
            start_dt = instruccion.get("start_datetime")
            end_dt = instruccion.get("end_datetime")
            respuesta = actualizar_evento(event_id, summary, start_dt, end_dt)
    elif action == "delete":
        event_id = instruccion.get("event_id")
        if not event_id:
            respuesta = "No se encontró el 'event_id' para borrar."
        else:
            respuesta = borrar_evento(event_id)
    else:
        # Si la acción es "other", usar el historial para respuesta contextual
        respuesta = obtener_respuesta_chatgpt_con_historial(conversaciones[from_number])

    # Agregar la respuesta al historial
    conversaciones[from_number].append({"role": "assistant", "content": respuesta})

    resp = MessagingResponse()
    resp.message(respuesta)
    return Response(str(resp), mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, date, timedelta

app = Flask(__name__)

# Diccionario global para almacenar el historial y estado de conversación por número de teléfono.
# Cada entrada será un diccionario con "historial", "estado", "eventos_disponibles" y "chosen_event_id".
conversaciones = {}

# Configura tu API Key de OpenAI y credenciales de Google Calendar mediante variables de entorno.
openai.api_key = os.environ.get("OPENAI_API_KEY")
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
TIMEZONE = "America/Argentina/Buenos_Aires"


def obtener_respuesta_generica(prompt):
    """Respuesta genérica de ChatGPT, con fecha/hora actual en el system prompt."""
    try:
        now = datetime.now()
        fecha_str = now.strftime("%d de %B de %Y")
        hora_str = now.strftime("%H:%M")
        system_prompt = (
            f"Eres un asistente que conoce la fecha y hora actuales. Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). "
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
        return "Lo siento, hubo un error procesando tu solicitud."


def obtener_respuesta_chatgpt_con_historial(historial):
    """Llama a ChatGPT con el historial completo (incluyendo system prompt con fecha/hora actual)."""
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    system_prompt = {
        "role": "system",
        "content": (
            f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). "
            "Eres un asistente amable y mantén el contexto de la conversación."
        )
    }
    mensajes = [system_prompt] + historial
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=mensajes,
            temperature=0.7,
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error al obtener respuesta con historial:", e)
        return "Lo siento, hubo un error procesando tu solicitud."


def interpretar_instruccion_evento(user_message):
    """
    Usa ChatGPT para interpretar instrucciones de calendario.
    Devuelve un JSON con keys: action, summary, start_datetime, end_datetime, event_id,
    time_range_start, time_range_end.
    """
    try:
        system_prompt = (
            "Eres un asistente que interpreta instrucciones de calendario en español. "
            "El usuario puede querer crear, listar, actualizar o borrar eventos. "
            "Devuelve un JSON con la siguiente estructura (solo incluye las claves que apliquen): "
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
    """
    try:
        hoy = date.today().isoformat()
        system_prompt = (
            f"Hoy es {hoy}. Eres un asistente para interpretar rangos de fechas. "
            "El usuario te dará una frase como 'eventos de mañana' o 'eventos de la próxima semana'. "
            "Responde únicamente con un JSON con 'start_date' y 'end_date' en formato YYYY-MM-DD, asumiendo el año actual. "
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


# Función para manejar la elección de evento cuando se está actualizando sin ID.
def manejar_eleccion_evento(user_data):
    lista = user_data.get("eventos_disponibles", [])
    system_content = (
        "Tienes la siguiente lista de eventos:\n" +
        "\n".join([f"{i+1}. {ev.get('summary','(sin título)')} (ID: {ev.get('id','')})"
                   for i, ev in enumerate(lista)]) +
        "\nEl usuario acaba de responder. Identifica cuál de estos eventos quiere modificar. "
        "Responde con un JSON: {\"chosen_event_id\": \"...\"} sin texto adicional."
    )
    resp = llamar_chatgpt_simple(user_data["historial"], system_content)
    match = re.search(r'\{.*\}', resp)
    if match:
        raw = match.group(0)
        try:
            data = json.loads(raw)
            cid = data.get("chosen_event_id")
            if cid:
                user_data["chosen_event_id"] = cid
                return "Evento seleccionado."
        except:
            pass
    return "No pude determinar el evento. Por favor, especifica."

# Función para manejar cambios en el evento una vez elegido.
def manejar_cambio_evento(user_data):
    ev_id = user_data.get("chosen_event_id")
    if not ev_id:
        return "No hay un evento seleccionado."
    system_content = (
        f"Tienes el evento con ID {ev_id}. El usuario quiere modificarlo. "
        "Devuelve un JSON con {\"summary\":\"...\", \"start_datetime\":\"YYYY-MM-DDTHH:MM:SS\", "
        "\"end_datetime\":\"YYYY-MM-DDTHH:MM:SS\"}. Asume que la duración es de 1 hora si no se especifica. "
        "No añadas texto adicional."
    )
    resp = llamar_chatgpt_simple(user_data["historial"], system_content)
    match = re.search(r'\{.*\}', resp)
    if match:
        raw = match.group(0)
        try:
            data = json.loads(raw)
            summary = data.get("summary", "Evento sin título")
            start_dt = data.get("start_datetime", "2025-03-10T11:00:00")
            end_dt = data.get("end_datetime", "2025-03-10T12:00:00")
            return actualizar_evento(ev_id, summary, start_dt, end_dt)
        except:
            return "No pude interpretar los cambios."
    return "No pude interpretar la respuesta del usuario."

def llamar_chatgpt_simple(historial, system_content):
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    system_prompt = {
        "role": "system",
        "content": f"Hoy es {fecha_str} y son las {hora_str}. {system_content}"
    }
    mensajes = [system_prompt] + historial
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=mensajes,
            temperature=0.7,
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error en llamar_chatgpt_simple:", e)
        return "Lo siento, hubo un error llamando a ChatGPT."


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
            "eventos_disponibles": [],
            "chosen_event_id": None
        }
    user_data = conversaciones[from_number]

    # Agregar mensaje del usuario al historial.
    user_data["historial"].append({"role": "user", "content": incoming_msg})

    # Si el estado es "update_pendiente" o "update_escogido", se maneja el flujo de actualización.
    if user_data["estado"] == "update_pendiente":
        # Esperamos que el usuario elija el evento.
        respuesta = manejar_eleccion_evento(user_data)
        if user_data.get("chosen_event_id"):
            user_data["estado"] = "update_escogido"
            followup = f"Se ha seleccionado el evento con ID {user_data['chosen_event_id']}. ¿Qué cambios deseas hacer?"
            resp2 = llamar_chatgpt_simple(user_data["historial"], followup)
            user_data["historial"].append({"role": "assistant", "content": resp2})
            respuesta = resp2
    elif user_data["estado"] == "update_escogido":
        respuesta = manejar_cambio_evento(user_data)
        user_data["estado"] = None  # Finalizamos la actualización.
    else:
        # Si no hay estado definido, interpretar la instrucción.
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
                # Si no se especificó event_id, cambiar estado y mostrar lista.
                user_data["estado"] = "update_pendiente"
                service = get_calendar_service()
                now_iso = datetime.now().isoformat() + "Z"
                future_iso = (datetime.now() + timedelta(days=7)).isoformat() + "Z"
                events_result = service.events().list(
                    calendarId=CALENDAR_ID,
                    timeMin=now_iso,
                    timeMax=future_iso,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                lista = events_result.get('items', [])
                user_data["eventos_disponibles"] = lista
                chat_prompt = (
                    "El usuario quiere modificar un evento. Aquí la lista de eventos:\n" +
                    "\n".join([f"{i+1}. {ev.get('summary','(sin título)')} (ID: {ev.get('id','')})"
                               for i, ev in enumerate(lista)]) +
                    "\nPor favor, indica cuál de estos eventos deseas modificar (puedes decir el número o el título)."
                )
                respuesta = llamar_chatgpt_simple(user_data["historial"], chat_prompt)
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
            respuesta = obtener_respuesta_generica(incoming_msg)

    # Agregar la respuesta al historial.
    user_data["historial"].append({"role": "assistant", "content": respuesta})
    resp = MessagingResponse()
    resp.message(respuesta)
    return Response(str(resp), mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


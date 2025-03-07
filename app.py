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

# Diccionario global para almacenar el historial y estado de cada conversación (por número de teléfono)
conversaciones = {}

# Configuración de OpenAI (API Key desde variable de entorno)
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Configuración de Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
TIMEZONE = "America/Argentina/Buenos_Aires"

# ----------------------------------------------------
# FUNCIONES DE CHATGPT Y CALENDARIO
# ----------------------------------------------------

def obtener_respuesta_generica(prompt):
    """Respuesta genérica de ChatGPT con fecha/hora actuales."""
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
    """Llama a ChatGPT con todo el historial para mantener contexto."""
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    system_prompt = {
        "role": "system",
        "content": f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: {TIMEZONE}). Eres un asistente amable y mantén el contexto de la conversación."
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
    Usa ChatGPT para interpretar instrucciones de calendario en español.
    Devuelve un JSON con la estructura:
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
    Devuelve un JSON con "start_date" y "end_date".
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
        time_min = f"{start_date}T00:00:00Z"
        time_max = f"{end_date}T23:59:59Z"
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
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
            event['start'] = {'dateTime': start_datetime, 'timeZone': TIMEZONE}
        if end_datetime:
            event['end'] = {'dateTime': end_datetime, 'timeZone': TIMEZONE}
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

def manejar_eleccion_evento(user_data):
    lista = user_data.get("eventos_disponibles", [])
    system_extra = (
        "Tienes la siguiente lista de eventos:\n" +
        "\n".join([f"{i+1}. {ev.get('summary','(sin título)')} (ID: {ev.get('id','')})"
                   for i, ev in enumerate(lista)]) +
        "\nEl usuario acaba de responder. Identifica cuál de estos eventos quiere modificar. "
        "Responde con un JSON {\"chosen_event_id\": \"...\"} sin texto adicional."
    )
    resp = llamar_chatgpt_simple(user_data["historial"], system_extra)
    match = re.search(r'\{.*\}', resp)
    if match:
        raw = match.group(0)
        try:
            data = json.loads(raw)
            cid = data.get("chosen_event_id")
            if cid:
                user_data["chosen_event_id"] = cid
                user_data["estado"] = "update_escogido"
                followup = f"Se ha seleccionado el evento con ID {cid}. ¿Qué cambios deseas hacer?"
                return followup
        except:
            pass
    return "No pude determinar el evento. Por favor, especifica un número o título."

def manejar_cambio_evento(user_data):
    ev_id = user_data.get("chosen_event_id")
    if not ev_id:
        return "No hay un evento seleccionado."
    system_extra = (
        f"El usuario quiere modificar el evento con ID {ev_id}. "
        "Devuelve un JSON con {\"summary\":\"...\", \"start_datetime\":\"YYYY-MM-DDTHH:MM:SS\", "
        "\"end_datetime\":\"YYYY-MM-DDTHH:MM:SS\"}, asumiendo 1 hora de duración si no se especifica fin. "
        "No añadas texto adicional."
    )
    resp = llamar_chatgpt_simple(user_data["historial"], system_extra)
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

# ----------------------------------------------------
# RUTA PRINCIPAL DE WHATSAPP
# ----------------------------------------------------

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
            "estilo": None,
            "eventos_disponibles": [],
            "chosen_event_id": None
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

    # 2) Interpretar la instrucción de calendario
    action, instruccion = None, {}
    try:
        instruccion = interpretar_instruccion_evento(incoming_msg)
        action = instruccion.get("action", "other")
    except Exception as e:
        print("Error al interpretar instrucción:", e)
        action = "other"
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
            system_extra = (
                "El usuario quiere modificar un evento. Aquí la lista de eventos:\n" +
                "\n".join([f"{i+1}. {ev.get('summary','(sin título)')} (ID: {ev.get('id','')})"
                           for i, ev in enumerate(lista)]) +
                "\nIndica cuál de estos eventos deseas modificar (por número o título)."
            )
            respuesta = llamar_chatgpt_simple(user_data["historial"], system_extra)
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
        if user_data["estado"] == "update_pendiente":
            respuesta = manejar_eleccion_evento(user_data)
        elif user_data["estado"] == "update_escogido":
            respuesta = manejar_cambio_evento(user_data)
            user_data["estado"] = None
        else:
            respuesta = obtener_respuesta_chatgpt_con_historial(user_data["historial"])

    user_data["historial"].append({"role": "assistant", "content": respuesta})
    return responder_whatsapp(respuesta)

def responder_whatsapp(texto):
    resp = MessagingResponse()
    resp.message(texto)
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

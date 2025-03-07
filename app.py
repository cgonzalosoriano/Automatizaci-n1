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

# Diccionario global: { from_number: { "historial": [...], "estado": ..., "estilo": ..., "eventos_disponibles": [...], "chosen_event_id": ... } }
conversaciones = {}

# Configurar OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Configurar Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = "4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com"
TIMEZONE = "America/Argentina/Buenos_Aires"

# -------------------------------------------------------
# FUNCIONES DE ESTILO, CALENDARIO Y CHAT
# -------------------------------------------------------

def get_calendar_service():
    """Retorna un objeto service para la API de Google Calendar."""
    credentials_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    service = build('calendar', 'v3', credentials=credentials)
    return service

def crear_evento(summary, start_datetime, end_datetime):
    """Crea un evento en Google Calendar."""
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
        created_event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f"Evento creado con éxito: {created_event.get('htmlLink')}"
    except Exception as e:
        print("Error al crear evento:", e)
        return "Hubo un error al crear el evento."

def listar_eventos_por_rango(start_date, end_date):
    """Lista eventos en un rango [start_date, end_date]. Formato YYYY-MM-DD."""
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
    """Actualiza un evento existente por event_id."""
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
            calendarId=CALENDAR_ID, eventId=event_id, body=event
        ).execute()
        return f"Evento actualizado con éxito: {updated_event.get('htmlLink')}"
    except Exception as e:
        print("Error al actualizar evento:", e)
        return "Hubo un error al actualizar el evento."

def borrar_evento(event_id):
    """Borra un evento existente por event_id."""
    try:
        service = get_calendar_service()
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return "Evento borrado con éxito."
    except Exception as e:
        print("Error al borrar evento:", e)
        return "Hubo un error al borrar el evento."


# -------------------------------------------------------
# FUNCIONES PARA CHATGPT MULTI-TURN
# -------------------------------------------------------

def armar_system_prompt(estilo):
    """
    Crea el system prompt según el estilo actual y la fecha/hora.
    'estilo' puede ser 'serio', 'chistes', 'amable', etc.
    """
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")

    # Mensaje base
    base = (
        f"Hoy es {fecha_str}, son las {hora_str} (zona horaria: {TIMEZONE}). "
        "Eres un asistente que habla español. "
    )

    # Ajustar según estilo
    if estilo == "serio":
        base += "Tu tono es formal y serio."
    elif estilo == "chistes":
        base += "Tu tono es divertido y sueles hacer chistes."
    elif estilo == "amable":
        base += "Eres muy amable y afectuoso en tus respuestas."
    else:
        base += "Responde de forma neutral."

    return {"role": "system", "content": base}


def chatgpt_con_historial(user_data, mensaje_extra=None):
    """
    Llama a ChatGPT con todo el historial, añadiendo un system prompt según el estilo.
    'mensaje_extra' se añade como system prompt adicional (opcional).
    """
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

# -------------------------------------------------------
# DETECCIÓN DE INTENCIONES
# -------------------------------------------------------

def interpretar_intencion(usuario_msg):
    """
    Detecta si el usuario quiere configurar el estilo.
    Ej: "configurar estilo: chistes" => ("set_style", "chistes")
    Si no, devuelves (None, None) y lo interpretará la lógica de calendario.
    """
    if "configurar estilo:" in usuario_msg.lower():
        # extraer la palabra que sigue
        # Ej: "configurar estilo: chistes"
        partes = usuario_msg.split(":")
        if len(partes) >= 2:
            estilo = partes[1].strip().lower()
            return ("set_style", estilo)
    return (None, None)


def interpretar_calendario(usuario_msg):
    """
    Lógica para ver si el usuario quiere crear, listar, update o delete un evento.
    Reutiliza la función interpret_instruccion_evento.
    """
    # Llamar a la API de interpret_instruccion_evento
    # para ver si es create, list, update, delete, other
    instruccion = interpretar_instruccion_evento(usuario_msg)
    action = instruccion.get("action", "other")
    return action, instruccion


# -------------------------------------------------------
# RUTA PRINCIPAL DE WHATSAPP
# -------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.form.get("From")
    incoming_msg = request.form.get("Body", "").strip()

    # Inicializar la estructura de conversación si no existe
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
    intent_style, estilo_nuevo = interpretar_intencion(incoming_msg)
    if intent_style == "set_style":
        user_data["estilo"] = estilo_nuevo
        respuesta = f"¡Estilo configurado a '{estilo_nuevo}'!"
        user_data["historial"].append({"role": "assistant", "content": respuesta})
        return responder_whatsapp(respuesta)

    # 2) Interpretar lógica de calendario
    action, instruccion = interpretar_calendario(incoming_msg)

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
            # Si no hay event_id, ChatGPT debe guiar para saber cuál.
            # Para simplificar, creamos un sub-lógica o "estado".
            user_data["estado"] = "update_pendiente"
            # Obtenemos los eventos próximos
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
            # Pedimos a ChatGPT que pregunte al usuario cuál evento modificar
            system_extra = (
                "El usuario quiere modificar un evento, pero no especificó cuál. Aquí la lista:\n" +
                "\n".join([f"{i+1}. {ev.get('summary','(sin titulo)')} (ID: {ev.get('id','')})"
                           for i, ev in enumerate(lista)]) +
                "\nPregúntale al usuario cuál de estos eventos desea modificar (por número o título)."
            )
            respuesta = chatgpt_con_historial(user_data, system_extra)
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
        # 3) Si no es nada de calendario, ChatGPT libre con historial
        # Revisar si el estado actual es "update_pendiente" o "update_escogido"
        # para guiar al usuario en la elección.
        if user_data["estado"] == "update_pendiente":
            # El usuario está eligiendo cuál evento modificar
            respuesta = manejar_eleccion_evento(user_data)
        elif user_data["estado"] == "update_escogido":
            # El usuario está indicando qué cambios hacer
            respuesta = manejar_cambio_evento(user_data)
            user_data["estado"] = None  # Fin
        else:
            # Conversación libre
            respuesta = chatgpt_con_historial(user_data)

    # Agregar respuesta al historial
    user_data["historial"].append({"role": "assistant", "content": respuesta})
    return responder_whatsapp(respuesta)


def manejar_eleccion_evento(user_data):
    """
    Llama a ChatGPT para que interprete la elección del usuario
    entre la lista de eventos_disponibles.
    """
    lista = user_data["eventos_disponibles"]
    system_extra = (
        "Tienes la siguiente lista de eventos:\n" +
        "\n".join([f"{i+1}. {ev.get('summary','(sin titulo)')} (ID: {ev.get('id','')})"
                   for i, ev in enumerate(lista)]) +
        "\nEl usuario acaba de responder. Identifica cuál de estos eventos quiere modificar. "
        "Responde con un JSON {\"chosen_event_id\": \"...\"} sin texto adicional."
    )
    resp = chatgpt_con_historial(user_data, system_extra)
    match = re.search(r'\{.*\}', resp)
    if match:
        raw = match.group(0)
        try:
            data = json.loads(raw)
            cid = data.get("chosen_event_id")
            if cid:
                user_data["chosen_event_id"] = cid
                user_data["estado"] = "update_escogido"
                # Pedir al usuario que indique qué cambios desea
                followup = f"Se ha seleccionado el evento con ID {cid}. ¿Qué cambios deseas hacer?"
                return followup
        except:
            pass
    return "No pude determinar el evento. Por favor, especifica un número o título."

def manejar_cambio_evento(user_data):
    """
    ChatGPT parsea la nueva info y actualizamos el evento con chosen_event_id.
    """
    ev_id = user_data.get("chosen_event_id")
    if not ev_id:
        return "No hay un evento seleccionado."

    system_extra = (
        f"El usuario quiere modificar el evento con ID {ev_id}. "
        "Devuelve un JSON con {\"summary\":\"...\", \"start_datetime\":\"YYYY-MM-DDTHH:MM:SS\", "
        "\"end_datetime\":\"YYYY-MM-DDTHH:MM:SS\"}, asumiendo 1 hora si no se especifica fin."
    )
    resp = chatgpt_con_historial(user_data, system_extra)
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

def responder_whatsapp(texto):
    resp = MessagingResponse()
    resp.message(texto)
    return Response(str(resp), mimetype="application/xml")

# FIN DE CÓDIGO

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

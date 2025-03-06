from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Configura tu API Key de OpenAI a través de una variable de entorno
openai.api_key = os.environ.get("OPENAI_API_KEY")

# Define el alcance para Google Calendar API
SCOPES = ['https://www.googleapis.com/auth/calendar']

def obtener_respuesta_chatgpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error al obtener respuesta de ChatGPT:", e)
        return "Lo siento, hubo un error procesando tu solicitud."

def get_calendar_service():
    # Lee el contenido del JSON desde la variable de entorno GOOGLE_CREDENTIALS
    credentials_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=credentials)
    return service

def listar_eventos():
    try:
        service = get_calendar_service()
        calendar_id = '4b3b738826123b6b5715b6a4348f46bc395aa7efcfb72182c9f3baeee992105f@group.calendar.google.com'  # O especifica otro calendario si lo prefieres
        events_result = service.events().list(
            calendarId=calendar_id, 
            maxResults=10, 
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return 'No se encontraron eventos.'
        event_list = ''
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'Sin título')
            event_list += f"{start} - {summary}\n"
        return event_list
    except Exception as e:
        print("Error al listar eventos:", e)
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

        # Reemplaza esto con la ID real de tu calendario
        calendar_id = "4b3b738826123b6b5715b3a4ab6f46bc395a7efcfb72182c9f3baeee99210f5f@group.calendar.google.com"

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

    incoming_lower = incoming_msg.lower()
    
    if "crear evento" in incoming_lower or "agregar evento" in incoming_lower:
        # Llama a crear_evento con datos fijos de prueba
        respuesta = crear_evento(
            summary="Evento de prueba",
            start_datetime="2025-03-10T11:00:00",
            end_datetime="2025-03-10T12:00:00"
        )
    elif "evento" in incoming_lower or "calendario" in incoming_lower:
        respuesta = listar_eventos()
    else:
        respuesta = obtener_respuesta_chatgpt(incoming_msg)

    resp = MessagingResponse()
    resp.message(respuesta)
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


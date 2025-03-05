from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Configura tu API Key de OpenAI (reemplaza "TU_API_KEY" con tu clave real)
import os
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

@app.route("/", methods=["GET"])
def home():
    return "¡Bienvenido! La aplicación está funcionando."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    # Log para confirmar que se entra al endpoint
    print(">>> Llegó una petición a /whatsapp")

    # Obtiene el mensaje que envió el usuario a través de WhatsApp
    incoming_msg = request.form.get("Body", "").strip()
    # Log para ver el contenido del mensaje
    print(f">>> Mensaje entrante: {incoming_msg}")

    # Se envía el mensaje a ChatGPT y se obtiene la respuesta
    respuesta_chatgpt = obtener_respuesta_chatgpt(incoming_msg)

    # Se crea la respuesta para enviar de vuelta a través de Twilio
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(respuesta_chatgpt)

    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

def get_calendar_service():
    # Lee el contenido del JSON desde la variable de entorno
    credentials_info = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=credentials)
    return service

def listar_eventos():
    try:
        service = get_calendar_service()
        calendar_id = 'primary'  # O puedes especificar otro calendario si lo prefieres
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

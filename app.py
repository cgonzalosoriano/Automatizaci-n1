from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai

app = Flask(__name__)

# Configura tu API Key de OpenAI (reemplaza "TU_API_KEY" con tu clave real)
openai.api_key = "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"


def obtener_respuesta_chatgpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",  # Puedes usar otro modelo si lo deseas
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,       # Ajusta la creatividad de la respuesta (opcional)
            max_tokens=150         # Limita la cantidad de tokens en la respuesta (opcional)
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
    # Obtiene el mensaje que envió el usuario a través de WhatsApp
    incoming_msg = request.form.get("Body", "").strip()
    print(f"Mensaje entrante: {incoming_msg}")

    # Se envía el mensaje a ChatGPT y se obtiene la respuesta
    respuesta_chatgpt = obtener_respuesta_chatgpt(incoming_msg)

    # Se crea la respuesta para enviar de vuelta a través de Twilio
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(respuesta_chatgpt)

    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    app.run(debug=True)

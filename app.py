from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import openai
import os
import json
import re
from datetime import datetime, timedelta
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

def obtener_hoja(nombre_hoja):
    """Obtiene una hoja específica de Google Sheets."""
    try:
        client = get_sheet_client()
        spreadsheet = client.open_by_key(os.environ.get("SHEET_SPREADSHEET_KEY"))
        return spreadsheet.worksheet(nombre_hoja)
    except Exception as e:
        print(f"Error al obtener la hoja {nombre_hoja}:", e)
        return None

def agregar_columna(nombre_hoja, nombre_columna):
    """Agrega una nueva columna a una hoja de Google Sheets."""
    hoja = obtener_hoja(nombre_hoja)
    if not hoja:
        return f"Error: No se pudo acceder a la hoja {nombre_hoja}."
    
    try:
        # Obtener la primera fila (encabezados)
        encabezados = hoja.row_values(1)
        if nombre_columna in encabezados:
            return f"La columna '{nombre_columna}' ya existe en la hoja {nombre_hoja}."
        
        # Agregar la nueva columna
        nueva_columna_index = len(encabezados) + 1
        hoja.update_cell(1, nueva_columna_index, nombre_columna)
        return f"Columna '{nombre_columna}' agregada a la hoja {nombre_hoja}."
    except Exception as e:
        print(f"Error al agregar columna a {nombre_hoja}:", e)
        return f"Hubo un error al agregar la columna a {nombre_hoja}."

# ----------------------------
# FUNCIONES PARA "LISTA DE PENDIENTES"
# ----------------------------
def agregar_tarea(descripcion, fecha_vencimiento, recordatorio=None):
    """Agrega una tarea a la hoja 'Lista de Pendientes'."""
    hoja = obtener_hoja("Lista de Pendientes")
    if not hoja:
        return "Error: No se pudo acceder a la hoja 'Lista de Pendientes'."
    
    try:
        # Validar formato de fecha
        try:
            datetime.strptime(fecha_vencimiento, "%d/%m/%Y")
        except ValueError:
            return "Formato de fecha inválido. Usa DD/MM/AAAA."
        
        # Obtener la próxima fila vacía
        nueva_fila = [
            str(len(hoja.get_all_values()) + 1),  # ID
            datetime.now().strftime("%d/%m/%Y %H:%M"),  # Fecha de creación
            fecha_vencimiento,  # Fecha de vencimiento
            descripcion,  # Descripción
            recordatorio if recordatorio else "Sin recordatorio"  # Recordatorio
        ]
        hoja.append_row(nueva_fila)
        return f"Tarea agregada: {descripcion} (Vence: {fecha_vencimiento})"
    except Exception as e:
        print("Error al agregar tarea:", e)
        return "Hubo un error al agregar la tarea."

def listar_tareas():
    """Lista todas las tareas pendientes."""
    hoja = obtener_hoja("Lista de Pendientes")
    if not hoja:
        return "Error: No se pudo acceder a la hoja 'Lista de Pendientes'."
    
    try:
        tareas = hoja.get_all_records()
        if not tareas:
            return "No hay tareas pendientes."
        
        respuesta = "Tareas pendientes:\n"
        for tarea in tareas:
            respuesta += f"- {tarea['Descripción']} (Vence: {tarea['Fecha de Vencimiento']})\n"
        return respuesta
    except Exception as e:
        print("Error al listar tareas:", e)
        return "Hubo un error al listar las tareas."

# ----------------------------
# FUNCIONES PARA "BASE DE DATOS DE CLIENTES"
# ----------------------------
def agregar_nota_cliente(id_cliente, nota):
    """Agrega una nota a la hoja 'Notas de Clientes'."""
    hoja_maestro = obtener_hoja("Maestro de Clientes")
    hoja_notas = obtener_hoja("Notas de Clientes")
    if not hoja_maestro or not hoja_notas:
        return "Error: No se pudo acceder a las hojas de clientes."
    
    try:
        # Buscar el cliente en el maestro
        cliente = next((row for row in hoja_maestro.get_all_records() if row["ID"] == id_cliente), None)
        if not cliente:
            return f"Error: No se encontró un cliente con ID {id_cliente}."
        
        # Agregar la nota
        nueva_fila = [
            id_cliente,
            datetime.now().strftime("%d/%m/%Y %H:%M"),  # Fecha de creación
            cliente["Nombre Cliente"],
            cliente["CUIT"],
            cliente["Descripción"],
            nota  # Nota
        ]
        hoja_notas.append_row(nueva_fila)
        return f"Nota agregada para {cliente['Nombre Cliente']}: {nota}"
    except Exception as e:
        print("Error al agregar nota:", e)
        return "Hubo un error al agregar la nota."

def listar_notas_cliente(id_cliente):
    """Lista todas las notas de un cliente."""
    hoja_notas = obtener_hoja("Notas de Clientes")
    if not hoja_notas:
        return "Error: No se pudo acceder a la hoja 'Notas de Clientes'."
    
    try:
        notas = [row for row in hoja_notas.get_all_records() if row["ID"] == id_cliente]
        if not notas:
            return f"No hay notas para el cliente con ID {id_cliente}."
        
        respuesta = f"Notas del cliente {notas[0]['Nombre Cliente']}:\n"
        for nota in notas:
            respuesta += f"- {nota['Nota']} (Fecha: {nota['Fecha de Creación']})\n"
        return respuesta
    except Exception as e:
        print("Error al listar notas:", e)
        return "Hubo un error al listar las notas."

# ----------------------------
# FUNCIONES PARA CHATGPT MULTI-TURN
# ----------------------------
def armar_system_prompt(estilo):
    """Crea el system prompt según el estilo (ej. serio, chistes, amable)."""
    now = datetime.now()
    fecha_str = now.strftime("%d de %B de %Y")
    hora_str = now.strftime("%H:%M")
    base = f"Hoy es {fecha_str} y son las {hora_str} (zona horaria: America/Argentina/Buenos_Aires). "
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

    # Interpretar comandos estructurados
    if "agregar tarea:" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            descripcion = partes[1].strip()
            respuesta = agregar_tarea(descripcion, "31/12/2023")  # Fecha de vencimiento por defecto
        else:
            respuesta = "Por favor, proporciona una descripción para la tarea."
    elif "agregar nota para" in incoming_msg.lower():
        partes = incoming_msg.split(":")
        if len(partes) >= 2:
            id_cliente = partes[0].split("para")[1].strip()
            nota = partes[1].strip()
            respuesta = agregar_nota_cliente(id_cliente, nota)
        else:
            respuesta = "Por favor, proporciona un ID de cliente y una nota."
    elif "listar tareas" in incoming_msg.lower():
        respuesta = listar_tareas()
    elif "listar notas de" in incoming_msg.lower():
        id_cliente = incoming_msg.split("de")[1].strip()
        respuesta = listar_notas_cliente(id_cliente)
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

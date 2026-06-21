import os
import requests
from fastapi import FastAPI, Request, Response
import uvicorn

app = FastAPI()

# --- CONFIGURATION ---
VERIFY_TOKEN = "my_secret_token"
META_TOKEN = "EAAOGYZBunM4gBR41s5oRTzddS55fd2Dq1gOepp8IN2i2zmVc4wVosgRiD0UAof5QuZBwr7j088ZCjKIQjBydwi9yb5l3c1i8tGLJS210QyhZCkN234xZCtpKi7IDEagGGrLMCyZB063ec5rznbwB2ZB2dcLZB7tn9LSXMw8ZCx8DIq5jxnCubMKHngkNDZAbbRlhRgSbyZBsOa6InWxTZB22jAJWRZBZC0ffDJeWBMehQZB5JB5OZC6TlBo1LZAcg5Q7GRi0b7qiqG6ZCfZAeGJ2bDop3zZAZAdZAc"
PHONE_NUMBER_ID = "1084503298089868"
API_VERSION = "v19.0"

# --- META API HELPERS ---
def send_meta_payload(phone, payload):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ API Error: {response.text}")
    except Exception as e:
        print(f"❌ Network Error: {e}")

def send_text(phone, text):
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text}}
    send_meta_payload(phone, payload)

def send_image(phone, image_url, caption):
    payload = {
        "messaging_product": "whatsapp", "to": phone, "type": "image",
        "image": {"link": image_url, "caption": caption}
    }
    send_meta_payload(phone, payload)

def send_buttons(phone, header, body, footer, buttons_list):
    # buttons_list should be a list of dicts: [{"id": "btn1", "title": "Click Me"}, ...]
    payload = {
        "messaging_product": "whatsapp", "to": phone, "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": footer},
            "action": {"buttons": [{"type": "reply", "reply": btn} for btn in buttons_list]}
        }
    }
    send_meta_payload(phone, payload)

# --- BOT LOGIC ---
def process_message(phone, text):
    text_lower = text.lower().strip()
    
    # 1. Main Menu (Buttons)
    if text_lower in ["hi", "hello", "hey", "start", "menu"]:
        send_buttons(
            phone=phone,
            header="Welcome to Waakye Bot! 🍛",
            body="We serve the best Waakye in town. How can we help you today? Tap a button below:",
            footer="Powered by Aleem Tech Solutions",
            buttons_list=[
                {"id": "view_menu", "title": "🍛 View Menu"},
                {"id": "view_location", "title": "📍 Location"},
                {"id": "contact_us", "title": "📞 Call Us"}
            ]
        )
        
    # 2. Handle Button Clicks
    elif text_lower == "view_menu":
        # Send a picture of food first! (Using a free Unsplash image of rice/food)
        send_image(
            phone=phone, 
            image_url="https://images.pexels.com/photos/1640772/pexels-photo-1640772.jpeg?auto=compress&cs=tinysrgb&w=800", 
            caption="🍛 *OUR SPECIAL MENU*\n\n• Waakye & Fish (GHS 35)\n• Waakye & Meat (GHS 30)\n• Waakye & Egg (GHS 25)\n\n*Add-ons:* Shito (GHS 5), Extra Rice (GHS 10)\n\nReply 'HI' to go back to the main menu."
        )
        
    elif text_lower == "view_location":
        send_text(phone, "📍 *OUR LOCATION*\nWe are located right at the Main Station, opposite the old post office.\n\n⏰ *HOURS:* Mon-Sat, 6:00 AM to 2:00 PM.\n\nReply 'HI' to go back to the main menu.")
        
    elif text_lower == "contact_us":
        send_text(phone, "📞 *CONTACT US*\nNeed to place a bulk order? Call or WhatsApp the Boss directly at +233 20 000 0000.\n\nReply 'HI' to go back to the main menu.")
        
    # 3. Fallback
    else:
        send_text(phone, "I didn't quite catch that. Tap the button below to see what I can do!")
        send_buttons(
            phone=phone,
            header="Main Menu",
            body="Please select an option below:",
            footer="",
            buttons_list=[
                {"id": "view_menu", "title": "🍛 View Menu"},
                {"id": "view_location", "title": "📍 Location"},
                {"id": "contact_us", "title": "📞 Call Us"}
            ]
        )

# --- WEBHOOK VERIFICATION (GET) ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)

# --- WEBHOOK RECEIVER (POST) ---
@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        data = await request.json()
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            message = messages[0]
            phone = message.get("from")
            msg_type = message.get("type")
            
            # Handle Text Messages
            if msg_type == "text":
                text = message.get("text", {}).get("body", "")
                print(f"📩 Text from {phone}: {text}")
                process_message(phone, text)
                
            # Handle Button Clicks (Interactive Messages)
            elif msg_type == "interactive":
                interactive = message.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    button_id = interactive.get("button_reply", {}).get("id")
                    print(f"👆 Button clicked by {phone}: {button_id}")
                    process_message(phone, button_id)
                    
    except Exception as e:
        print(f"❌ Webhook Error: {e}")

    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

import os
import sqlite3
import requests
import threading
from datetime import datetime
from fastapi import FastAPI, Request, Response
import uvicorn

app = FastAPI()

# --- BUSINESS CONFIGURATION ---
VERIFY_TOKEN = "my_secret_token"
META_TOKEN = "EAAOGYZBunM4gBR7ScL41J7zktTECREWwmttoKLbo2ZBhdOsWkXCJhHSfwhMangXrfapPYG3bMZAQuXe8vPHK6cipgHLzXdJ1REZCNQxLDoVUcrm7j63oG7t594yfOVAKx9Gm8nUZBL5fQ12UeL73MQrRtQZBEAWVnM5DpzvUf072UF0uizDyscPwrF7fAA"
PHONE_NUMBER_ID = "1084503298089868"
OWNER_NUMBER = "233209354460" # Boss's WhatsApp (no +)

# Payment Details
MOMO_NUMBER = "0241234567"
MOMO_NAME = "Aleem Tech"
API_VERSION = "v19.0"

# --- SQLITE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('orders.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, items TEXT, location TEXT, 
                  payment_method TEXT, total_price TEXT, status TEXT DEFAULT 'new', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()
init_db()

# --- DATABASE HELPERS ---
def save_order(phone, items, location, payment_method, total_price):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute("INSERT INTO orders (phone, items, location, payment_method, total_price) VALUES (?, ?, ?, ?, ?)",
              (phone, items, location, payment_method, total_price))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id

def get_daily_summary():
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*), SUM(CAST(total_price AS REAL)) FROM orders WHERE created_at LIKE ?", (f"%{today}%",))
    total_orders, total_revenue = c.fetchone()
    conn.close()
    return total_orders or 0, total_revenue or 0.0

# --- META API HELPERS ---
def send_meta_payload(phone, payload):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    requests.post(url, headers=headers, json=payload)

def send_text(phone, text):
    send_meta_payload(phone, {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text}})

def send_image(phone, image_url, caption):
    send_meta_payload(phone, {"messaging_product": "whatsapp", "to": phone, "type": "image", "image": {"link": image_url, "caption": caption}})

def send_buttons(phone, header, body, footer, buttons_list):
    payload = {
        "messaging_product": "whatsapp", "to": phone, "type": "interactive",
        "interactive": {
            "type": "button", "header": {"type": "text", "text": header}, "body": {"text": body},
            "footer": {"text": footer}, "action": {"buttons": [{"type": "reply", "reply": btn} for btn in buttons_list]}
        }
    }
    send_meta_payload(phone, payload)

# --- CONVERSATION STATE (In-Memory for simplicity) ---
# In a real app, use DB, but for this MVP, a dictionary is fine and faster.
user_states = {}

def get_state(phone):
    return user_states.get(phone, {"step": "greeting", "order": "", "location": ""})

def set_state(phone, step, order=None, location=None):
    state = get_state(phone)
    state["step"] = step
    if order is not None: state["order"] = order
    if location is not None: state["location"] = location
    user_states[phone] = state

# --- OWNER COMMANDS ---
def handle_owner_command(phone, text):
    text_lower = text.lower().strip()
    if text_lower == "summary":
        orders, revenue = get_daily_summary()
        send_text(OWNER_NUMBER, f"📊 *DAILY SUMMARY*\nTotal Orders: {orders}\nTotal Revenue: GHS {revenue:.2f}")
    else:
        send_text(OWNER_NUMBER, "I only understand 'summary' right now, Boss.")

# --- CUSTOMER FLOW ---
def handle_customer_flow(phone, text, button_id=None):
    state = get_state(phone)
    text_lower = text.lower().strip() if text else ""

    # 1. GREETING
    if state["step"] == "greeting" or text_lower in ["hi", "hello", "start", "menu"]:
        set_state(phone, "greeting")
        send_buttons(phone, "Welcome! 🍛", "How can we serve you today?", "Powered by Aleem Tech", [
            {"id": "view_menu", "title": "🍛 View Menu"},
            {"id": "place_order", "title": "🛒 Place Order"}
        ])

    # 2. MENU
    elif button_id == "view_menu":
        send_image(phone, "https://images.pexels.com/photos/1640772/pexels-photo-1640772.jpeg?auto=compress&cs=tinysrgb&w=800", 
                   " *OUR MENU*\n• Fried Rice & Chicken - GHS 35\n• Fried Rice & Fish - GHS 30\n• Jollof & Beef - GHS 35\n• Banku & Tilapia - GHS 40\n\nReply 'HI' to order.")

    # 3. ORDER TAKING
    elif button_id == "place_order" or (state["step"] == "greeting" and text_lower == "place order"):
        set_state(phone, "taking_order")
        send_text(phone, " *PLACE YOUR ORDER*\n\nReply with what you want.\n*Example:* 2 Fried Rice & Chicken, 1 Jollof & Beef.")

    elif state["step"] == "taking_order":
        set_state(phone, "ask_location", order=text)
        send_buttons(phone, "Pickup or Delivery?", "How will you get your food?", "", [
            {"id": "pickup", "title": "🏃 Pickup"},
            {"id": "delivery", "title": "🛵 Delivery"}
        ])

    # 4. LOCATION
    elif button_id in ["pickup", "delivery"]:
        if button_id == "pickup":
            set_state(phone, "ask_payment", location="Pickup at shop")
            ask_payment(phone)
        else:
            set_state(phone, "ask_location_text")
            send_text(phone, "📍 *DELIVERY LOCATION*\n\nPlease type your location or landmark.\n*Example:* East Legon, near the American House.")

    elif state["step"] == "ask_location_text":
        set_state(phone, "ask_payment", location=text)
        ask_payment(phone)

    # 5. PAYMENT
    def ask_payment(phone):
        send_buttons(phone, "Payment Method", "How would you like to pay?", "", [
    {"id": "cash", "title": "💵 Cash on Delivery"},
    {"id": "momo_delivery", "title": "📱 MoMo on Delivery"},
    {"id": "momo_now", "title": " Pay Now (MoMo)"}
])

    elif button_id in ["cash", "momo_delivery", "momo_now"]:
        state = get_state(phone)
        order_id = save_order(phone, state["order"], state["location"], button_id, "35") # Hardcoded price for MVP, calculate later if needed
        
        # Format message for the Boss
        boss_msg = (f"🔔 *NEW ORDER #{order_id}!*\n\n"
                    f"🍛 *Items:* {state['order']}\n"
                    f"📍 *Location:* {state['location']}\n"
                    f"💰 *Payment:* {button_id.replace('_', ' ').title()}\n"
                    f"📞 *Customer:* {phone}")
        
        send_text(OWNER_NUMBER, boss_msg)

        # Reply to customer
        if button_id == "momo_now":
            send_text(phone, f"✅ *Order #{order_id} Received!*\n\nPlease send GHS 35 to MoMo:\n*{MOMO_NUMBER}* ({MOMO_NAME})\n\nShow the transaction alert to the rider/driver. Thank you!")
        else:
            send_text(phone, f"✅ *Order #{order_id} Received!*\n\nPlease have GHS 35 ready for the rider/driver. The boss will call you shortly. Thank you!")
        
        set_state(phone, "greeting")

    else:
        send_text(phone, "I didn't catch that. Reply 'HI' to see the menu.")

# --- MAIN ROUTER ---
def process_incoming(phone, text, button_id=None):
    if phone == OWNER_NUMBER:
        handle_owner_command(phone, text)
    else:
        handle_customer_flow(phone, text, button_id)

# --- WEBHOOKS ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)

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
            
            if msg_type == "text":
                text = message.get("text", {}).get("body", "")
                threading.Thread(target=process_incoming, args=(phone, text, None)).start()
            elif msg_type == "interactive":
                interactive = message.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    button_id = interactive.get("button_reply", {}).get("id")
                    threading.Thread(target=process_incoming, args=(phone, None, button_id)).start()
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

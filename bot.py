import os
import sqlite3
import requests
import threading
from datetime import datetime
from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# --- FRANK FRIED KITCHEN CONFIGURATION ---
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
META_TOKEN = os.getenv("META_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
OWNER_NUMBER = os.getenv("OWNER_NUMBER")  
MOMO_NUMBER = os.getenv("MOMO_NUMBER")    
MOMO_NAME = os.getenv("MOMO_NAME")        ohia
API_VERSION = "v19.0"

# --- FRANK FRIED KITCHEN MENU ---
MENU = {
    "fried rice": 30,
    "assorted fried rice": 60,
    "jollof rice": 30,
    "assorted jollof rice": 60,
    "waakye with chicken": 30,
    "waakye with fish": 30,
    "waakye with egg": 20,
    "spaghetti with chicken": 30,
    "assorted spaghetti": 40,
    "indomie with chicken": 30,
    "assorted indomie": 50,
    "plain rice with fish": 30,
    "plain rice with chicken": 30,
    "plain rice with egg": 20
}

# --- SQLITE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('orders.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, item TEXT, 
                  quantity INTEGER, delivery_type TEXT, location TEXT, 
                  payment_method TEXT, total_price TEXT, status TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_state
                 (phone TEXT PRIMARY KEY, state TEXT, data TEXT)''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect('orders.db', timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def save_state(phone, state, data=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_state (phone, state, data) VALUES (?, ?, ?)",
              (phone, state, data))
    conn.commit()
    conn.close()

def get_state(phone):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT state, data FROM user_state WHERE phone=?", (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return row['state'], row['data']
    return None, None

def clear_state(phone):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM user_state WHERE phone=?", (phone,))
    conn.commit()
    conn.close()

# --- WHATSAPP API ---
def send_whatsapp_message(phone, message):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
    except Exception as e:
        print(f"Error sending message: {e}")

# --- ORDER PROCESSING ---
def process_message(phone, text):
    text_lower = text.lower().strip()
    state, data = get_state(phone)
    
    # Owner verification command
    if phone == OWNER_NUMBER and text_lower.startswith("verified"):
        parts = text_lower.split()
        if len(parts) >= 2:
            customer_phone = parts[1]
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE orders SET status='confirmed' WHERE phone=? AND status='pending'",
                      (customer_phone,))
            conn.commit()
            conn.close()
            send_whatsapp_message(OWNER_NUMBER, f"✅ Payment verified for {customer_phone}. Order confirmed.")
            send_whatsapp_message(customer_phone, "✅ Payment confirmed. Your order is being prepared. Thank you!")
            return
    
    # Owner summary command
    if phone == OWNER_NUMBER and text_lower == "summary":
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders WHERE status='pending'")
        pending = c.fetchall()
        conn.close()
        
        if not pending:
            send_whatsapp_message(OWNER_NUMBER, "No pending orders.")
            return
        
        summary = "📋 PENDING ORDERS:\n\n"
        for order in pending:
            summary += f"ID: {order['id']}\n"
            summary += f"Customer: {order['phone']}\n"
            summary += f"Item: {order['item']} x{order['quantity']}\n"
            summary += f"Total: GHS {order['total_price']}\n"
            summary += f"Delivery: {order['delivery_type']} to {order['location']}\n"
            summary += f"Payment: {order['payment_method']}\n"
            summary += f"Status: {order['status']}\n"
            summary += f"Time: {order['created_at']}\n"
            summary += "---\n"
        
        send_whatsapp_message(OWNER_NUMBER, summary)
        return
    
    # New customer starts
    if state is None:
        menu_text = "🍽️ *FRANK FRIED KITCHEN MENU*\n\n"
        for item, price in MENU.items():
            menu_text += f"• {item.title()} - GHS {price}\n"
        menu_text += "\n📍 Location: Ben-Barquarye street (Old St. Francis)\n"
        menu_text += "🕐 Hours: 7:00am - 10:00pm (Mon-Sat)\n"
        menu_text += "🚚 Delivery: 8:00am - 3:00pm\n\n"
        menu_text += "What would you like to order? (e.g., 'waakye with chicken')"
        send_whatsapp_message(phone, menu_text)
        save_state(phone, "awaiting_item")
        return
    
    # Customer selecting item
    if state == "awaiting_item":
        # Find matching menu item
        found_item = None
        for menu_item in MENU.keys():
            if menu_item in text_lower:
                found_item = menu_item
                break
        
        if found_item:
            save_state(phone, "awaiting_quantity", found_item)
            send_whatsapp_message(phone, f"How many {found_item.title()} would you like?")
        else:
            send_whatsapp_message(phone, "Sorry, I didn't find that item. Please choose from the menu above.")
        return
    
    # Customer entering quantity
    if state == "awaiting_quantity":
        try:
            quantity = int(text)
            if quantity < 1:
                send_whatsapp_message(phone, "Please enter a number greater than 0.")
                return
            
            item = data
            price = MENU[item]
            total = quantity * price
            
            save_state(phone, "awaiting_delivery", f"{item}|{quantity}|{total}")
            send_whatsapp_message(phone, "Is this for Pickup or Delivery?")
        except ValueError:
            send_whatsapp_message(phone, "Please enter a valid number.")
        return
    
    # Customer selecting delivery type
    if state == "awaiting_delivery":
        if "pickup" in text_lower:
            item, quantity, total = data.split("|")
            save_state(phone, "awaiting_payment", f"{item}|{quantity}|{total}|pickup|N/A")
            send_whatsapp_message(phone, "How would you like to pay? (MoMo or Cash)")
        elif "delivery" in text_lower:
            save_state(phone, "awaiting_location", f"{data}|delivery")
            send_whatsapp_message(phone, "Please enter your delivery location.")
        else:
            send_whatsapp_message(phone, "Please reply with 'Pickup' or 'Delivery'.")
        return
    
    # Customer entering delivery location
    if state == "awaiting_location":
        item, quantity, total, delivery_type = data.split("|")
        location = text
        save_state(phone, "awaiting_payment", f"{item}|{quantity}|{total}|{delivery_type}|{location}")
        send_whatsapp_message(phone, "How would you like to pay? (MoMo or Cash)")
        return
    
    # Customer selecting payment method
    if state == "awaiting_payment":
        if "momo" in text_lower or "mobile money" in text_lower:
            item, quantity, total, delivery_type, location = data.split("|")
            save_state(phone, "awaiting_momo_confirmation", data)
            
            momo_msg = f"📱 *PAYMENT DETAILS*\n\n"
            momo_msg += f"Item: {item.title()} x{quantity}\n"
            momo_msg += f"Total: GHS {total}\n"
            momo_msg += f"Delivery: {delivery_type}"
            if location != "N/A":
                momo_msg += f" to {location}"
            momo_msg += f"\n\n💳 Send GHS {total} to:\n"
            momo_msg += f"*MTN MoMo:* {MOMO_NUMBER}\n"
            momo_msg += f"*Name:* {MOMO_NAME}\n\n"
            momo_msg += "Reply YES once you've sent the money."
            send_whatsapp_message(phone, momo_msg)
        elif "cash" in text_lower:
            item, quantity, total, delivery_type, location = data.split("|")
            
            if delivery_type == "delivery":
                send_whatsapp_message(phone, "Cash payment is only available for Pickup. Please choose MoMo for Delivery.")
                return
            
            # Save order to database
            conn = get_db()
            c = conn.cursor()
            c.execute("INSERT INTO orders (phone, item, quantity, delivery_type, location, payment_method, total_price, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (phone, item, quantity, delivery_type, location, "cash", total, "pending"))
            conn.commit()
            conn.close()
            
            # Notify owner
            owner_msg = f"🔔 *NEW ORDER (CASH)*\n\n"
            owner_msg += f"Customer: {phone}\n"
            owner_msg += f"Item: {item.title()} x{quantity}\n"
            owner_msg += f"Total: GHS {total}\n"
            owner_msg += f"Type: Pickup\n"
            owner_msg += f"Reply 'verified {phone}' when customer pays."
            send_whatsapp_message(OWNER_NUMBER, owner_msg)
            
            send_whatsapp_message(phone, "⏳ Order received. Please pay GHS " + total + " in cash when you pick up. We'll confirm once payment is received.")
            clear_state(phone)
        else:
            send_whatsapp_message(phone, "Please reply with 'MoMo' or 'Cash'.")
        return
    
    # Customer confirming MoMo payment
    if state == "awaiting_momo_confirmation":
        if "yes" in text_lower:
            item, quantity, total, delivery_type, location = data.split("|")
            
            # Save order to database
            conn = get_db()
            c = conn.cursor()
            c.execute("INSERT INTO orders (phone, item, quantity, delivery_type, location, payment_method, total_price, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (phone, item, quantity, delivery_type, location, "momo", total, "pending"))
            conn.commit()
            conn.close()
            
            # Notify owner
            owner_msg = f"⏳ *PENDING VERIFICATION*\n\n"
            owner_msg += f"Customer: {phone}\n"
            owner_msg += f"Item: {item.title()} x{quantity}\n"
            owner_msg += f"Total: GHS {total}\n"
            owner_msg += f"Delivery: {delivery_type}"
            if location != "N/A":
                owner_msg += f" to {location}"
            owner_msg += f"\n\nCheck your MoMo. If you see GHS {total}, reply:\n"
            owner_msg += f"'verified {phone}'"
            send_whatsapp_message(OWNER_NUMBER, owner_msg)
            
            send_whatsapp_message(phone, "⏳ Payment verification in progress. The owner will check their MoMo and confirm shortly. Thank you!")
            clear_state(phone)
        else:
            send_whatsapp_message(phone, "Please reply YES once you've sent the money.")
        return

# --- WEBHOOK ENDPOINTS ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Verification failed", status_code=403)

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        messages = changes.get("value", {}).get("messages", [])
        if messages:
            message = messages[0]
            phone = message.get("from")
            text = message.get("text", {}).get("body", "")
            process_message(phone, text)
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            print("⚠️ Database locked (Meta retry). Ignoring safely.")
        else:
            print(f"Webhook DB Error: {e}")
    except Exception as e:
        print(f"Webhook Error: {e}")
    
    return {"status": "ok"}

# Initialize database on startup
init_db()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

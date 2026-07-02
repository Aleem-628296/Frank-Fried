import os
import sqlite3
import httpx
import asyncio
import json
import hmac
import hashlib
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
MOMO_NAME = os.getenv("MOMO_NAME")
APP_SECRET = os.getenv("APP_SECRET")
API_VERSION = os.getenv("API_VERSION", "v19.0")

# --- MENU ARCHITECTURE ---
MENU = {
    "rice": {
        "title": "🍚 Rice Dishes",
        "items": {
            "fried_rice": {"name": "Fried Rice", "price": 30},
            "assorted_fried": {"name": "Assorted Fried Rice", "price": 60},
            "jollof_rice": {"name": "Jollof Rice", "price": 30},
            "assorted_jollof": {"name": "Assorted Jollof", "price": 60},
            "waakye_chicken": {"name": "Waakye w/ Chicken", "price": 30},
            "waakye_fish": {"name": "Waakye w/ Fish", "price": 30},
            "waakye_egg": {"name": "Waakye w/ Egg", "price": 20}
        }
    },
    "pasta": {
        "title": "🍝 Pasta & Noodles",
        "items": {
            "spaghetti_chicken": {"name": "Spaghetti w/ Chicken", "price": 30},
            "assorted_spaghetti": {"name": "Assorted Spaghetti", "price": 40},
            "indomie_chicken": {"name": "Indomie w/ Chicken", "price": 30},
            "assorted_indomie": {"name": "Assorted Indomie", "price": 50}
        }
    },
    "specials": {
        "title": "🍗 Specials",
        "items": {
            "plain_rice_fish": {"name": "Plain Rice w/ Fish", "price": 30},
            "plain_rice_chicken": {"name": "Plain Rice w/ Chicken", "price": 30},
            "plain_rice_egg": {"name": "Plain Rice w/ Egg", "price": 20}
        }
    },
    "drinks": {
        "title": "🥤 Drinks",
        "items": {
            "water": {"name": "Bottled Water", "price": 5},
            "soda": {"name": "Soda (Coca/Sprite)", "price": 10},
            "juice": {"name": "Fresh Juice", "price": 15}
        }
    }
}

# --- ASYNC DATABASE LAYER (Gilfoyle Protocol) ---
def _sync_db_call(query, params=(), fetch_one=False, fetch_all=False):
    conn = sqlite3.connect('orders.db', timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(query, params)
    if fetch_one: res = c.fetchone()
    elif fetch_all: res = c.fetchall()
    else: conn.commit(); res = None
    conn.close()
    return res

async def db_execute(query, params=()): return await asyncio.to_thread(_sync_db_call, query, params)
async def db_fetch_one(query, params=()): return await asyncio.to_thread(_sync_db_call, query, params, fetch_one=True)

def init_db():
    _sync_db_call('''CREATE TABLE IF NOT EXISTS orders
        (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, items TEXT, total_price REAL,
        delivery_type TEXT, location TEXT, payment_method TEXT, status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    _sync_db_call('''CREATE TABLE IF NOT EXISTS user_state
        (phone TEXT PRIMARY KEY, state TEXT, data TEXT)''')

# --- STATE MANAGEMENT (JSON) ---
async def save_state(phone, state, data=None):
    data_str = json.dumps(data) if data is not None else "{}"
    await db_execute("INSERT OR REPLACE INTO user_state (phone, state, data) VALUES (?, ?, ?)", (phone, state, data_str))

async def get_state(phone):
    row = await db_fetch_one("SELECT state, data FROM user_state WHERE phone=?", (phone,))
    if row: return row['state'], json.loads(row['data']) if row['data'] else {}
    return None, {}

async def clear_state(phone): await db_execute("DELETE FROM user_state WHERE phone=?", (phone,))

# --- ASYNC WHATSAPP API (Speed & Scale) ---
async def send_payload(phone, payload):
    url = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    payload["messaging_product"] = "whatsapp"
    payload["to"] = phone
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, headers=headers, json=payload, timeout=10.0)
            res.raise_for_status()
        except Exception as e:
            print(f"API Error to {phone}: {e}")

async def send_text(phone, text):
    await send_payload(phone, {"type": "text", "text": {"body": text}})

async def send_list(phone, header, body, button_text, sections):
    await send_payload(phone, {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "action": {"button": button_text, "sections": sections}
        }
    })

async def send_buttons(phone, body, buttons):
    btn_payload = [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]
    await send_payload(phone, {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": btn_payload}
        }
    })

# --- UI BUILDERS ---
def build_category_list():
    sections = [{"title": "Menu", "rows": []}]
    for cat_key, cat_data in MENU.items():
        sections[0]["rows"].append({"id": f"cat_{cat_key}", "title": cat_data["title"]})
    return sections

def build_item_list(category_key):
    cat = MENU[category_key]
    sections = [{"title": cat["title"], "rows": []}]
    for item_key, item_data in cat["items"].items():
        sections[0]["rows"].append({"id": f"item_{item_key}", "title": f"{item_data['name']} - GHS {item_data['price']}"})
    return sections

def get_cart_summary(data):
    cart = data.get("cart", [])
    if not cart: return "Your cart is empty.", 0
    summary = "🛒 *YOUR CART*\n\n"
    total = 0
    for entry in cart:
        line_total = entry['price']
        total += line_total
        summary += f"• {entry['name']} - GHS {line_total}\n"
    summary += f"\n*Total: GHS {total}*"
    return summary, total

# --- STATE MACHINE ROUTER ---
async def route_interaction(phone, payload_id, data):
    state, data = await get_state(phone)
    
    # 1. CATEGORY SELECTION
    if payload_id.startswith("cat_"):
        cat_key = payload_id.replace("cat_", "")
        sections = build_item_list(cat_key)
        await send_list(phone, "Frank Fried Kitchen", f"Select your {MENU[cat_key]['title'].replace('🍚 ', '').replace('🍝 ', '').replace('🍗 ', '').replace('🥤 ', '')}", "View Items", sections)
        await save_state(phone, "viewing_items", data)
        return

    # 2. ITEM SELECTION (ADD TO CART)
    if payload_id.startswith("item_"):
        item_key = payload_id.replace("item_", "")
        item_data = None
        for cat in MENU.values():
            if item_key in cat["items"]:
                item_data = cat["items"][item_key]
                break
        
        if item_data:
            cart = data.get("cart", [])
            cart.append({"id": item_key, "name": item_data["name"], "price": item_data["price"]})
            data["cart"] = cart
            
            summary, total = get_cart_summary(data)
            await send_buttons(phone, f"✅ *{item_data['name']} added!*\n\n{summary}", [
                {"id": "cart_add_more", "title": "➕ Add More"},
                {"id": "cart_checkout", "title": "✅ Checkout"},
                {"id": "cart_remove", "title": "🗑 Remove Item"}
            ])
            await save_state(phone, "cart_view", data)
        return

    # 3. CART ACTIONS
    if payload_id == "cart_add_more":
        sections = build_category_list()
        await send_list(phone, "Frank Fried Kitchen", "What else are you craving?", "View Categories", sections)
        await save_state(phone, "main_menu", data)
        return
        
    if payload_id == "cart_remove":
        cart = data.get("cart", [])
        if cart:
            removed = cart.pop()
            data["cart"] = cart
            if not cart:
                await send_text(phone, "🗑 Cart cleared.")
                sections = build_category_list()
                await send_list(phone, "Frank Fried Kitchen", "Start fresh. What are you craving?", "View Categories", sections)
                await save_state(phone, "main_menu", data)
            else:
                summary, total = get_cart_summary(data)
                await send_buttons(phone, f"🗑 Removed {removed['name']}.\n\n{summary}", [
                    {"id": "cart_add_more", "title": "➕ Add More"},
                    {"id": "cart_checkout", "title": "✅ Checkout"},
                    {"id": "cart_remove", "title": "🗑 Remove Item"}
                ])
                await save_state(phone, "cart_view", data)
        return

    if payload_id == "cart_checkout":
        summary, total = get_cart_summary(data)
        await send_buttons(phone, f"{summary}\n\nHow do you want to get your food?", [
            {"id": "del_pickup", "title": "🏃 Pickup"},
            {"id": "del_delivery", "title": "🚚 Delivery"},
            {"id": "back_cart", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_delivery", data)
        return

    # 4. DELIVERY & BACK NAVIGATION
    if payload_id == "back_cart":
        summary, total = get_cart_summary(data)
        await send_buttons(phone, summary, [
            {"id": "cart_add_more", "title": "➕ Add More"},
            {"id": "cart_checkout", "title": "✅ Checkout"},
            {"id": "cart_remove", "title": "🗑 Remove Item"}
        ])
        await save_state(phone, "cart_view", data)
        return

    if payload_id == "del_pickup":
        data["delivery_type"] = "pickup"
        data["location"] = "N/A"
        await send_buttons(phone, "How will you pay?", [
            {"id": "pay_momo", "title": "📱 MoMo"},
            {"id": "pay_cash", "title": "💵 Cash"},
            {"id": "back_delivery", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_payment", data)
        return

    if payload_id == "del_delivery":
        data["delivery_type"] = "delivery"
        await send_text(phone, "📍 Please type your exact delivery location.")
        await save_state(phone, "awaiting_location", data)
        return

    if payload_id == "back_delivery":
        summary, total = get_cart_summary(data)
        await send_buttons(phone, f"{summary}\n\nHow do you want to get your food?", [
            {"id": "del_pickup", "title": "🏃 Pickup"},
            {"id": "del_delivery", "title": "🚚 Delivery"},
            {"id": "back_cart", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_delivery", data)
        return

    # 5. PAYMENT & CONFIRMATION
    if payload_id == "pay_momo" or payload_id == "pay_cash":
        data["payment_method"] = "momo" if payload_id == "pay_momo" else "cash"
        
        if data["payment_method"] == "cash" and data["delivery_type"] == "delivery":
            await send_text(phone, "❌ Cash is only for Pickup. Please choose MoMo for Delivery.")
            return

        summary, total = get_cart_summary(data)
        confirm_msg = f"*ORDER CONFIRMATION*\n\n{summary}\n\n"
        confirm_msg += f"📍 Type: {data['delivery_type'].title()}"
        if data['delivery_type'] == 'delivery': confirm_msg += f" to {data.get('location', 'Unknown')}"
        confirm_msg += f"\n💳 Payment: {data['payment_method'].upper()}\n\nConfirm this order?"
        
        await send_buttons(phone, confirm_msg, [
            {"id": "confirm_yes", "title": "✅ Confirm"},
            {"id": "confirm_no", "title": "❌ Cancel"},
            {"id": "back_payment", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_confirm", data)
        return

    if payload_id == "back_payment":
        await send_buttons(phone, "How will you pay?", [
            {"id": "pay_momo", "title": "📱 MoMo"},
            {"id": "pay_cash", "title": "💵 Cash"},
            {"id": "back_delivery", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_payment", data)
        return

    if payload_id == "confirm_yes":
        summary, total = get_cart_summary(data)
        
        # Save to DB
        await db_execute("INSERT INTO orders (phone, items, total_price, delivery_type, location, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (phone, json.dumps(data['cart']), total, data['delivery_type'], data.get('location', 'N/A'), data['payment_method'], "pending"))
        
        # Notify Owner
        owner_msg = f"🔔 *NEW ORDER*\n\nCustomer: {phone}\n{summary}\nTotal: GHS {total}\nType: {data['delivery_type'].title()}"
        if data['delivery_type'] == 'delivery': owner_msg += f" to {data.get('location')}"
        owner_msg += f"\nPayment: {data['payment_method'].upper()}\n\nReply 'verified {phone}' when cleared."
        await send_text(OWNER_NUMBER, owner_msg)
        
        # Notify Customer
        if data['payment_method'] == 'momo':
            cust_msg = f"✅ Order received!\n\nSend GHS {total} to:\n*MTN MoMo:* {MOMO_NUMBER}\n*Name:* {MOMO_NAME}\n\nReply 'yes' when sent."
            await send_text(phone, cust_msg)
            await save_state(phone, "awaiting_momo_confirmation", data)
        else:
            await send_text(phone, f"✅ Order received! Pay GHS {total} in cash upon pickup. See you soon!")
            await clear_state(phone)
        return

    if payload_id == "confirm_no":
        await send_text(phone, "❌ Order cancelled. Let us know if you need anything!")
        await clear_state(phone)
        return

    if payload_id == "back_confirm":
        summary, total = get_cart_summary(data)
        confirm_msg = f"*ORDER CONFIRMATION*\n\n{summary}\n\n📍 Type: {data['delivery_type'].title()}\n💳 Payment: {data['payment_method'].upper()}\n\nConfirm this order?"
        await send_buttons(phone, confirm_msg, [
            {"id": "confirm_yes", "title": "✅ Confirm"},
            {"id": "confirm_no", "title": "❌ Cancel"},
            {"id": "back_payment", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_confirm", data)
        return

# --- TEXT FALLBACK & OWNER COMMANDS ---
async def process_text(phone, text, data):
    text_lower = text.lower().strip()
    state, _ = await get_state(phone)

    # Owner Commands
    if phone == OWNER_NUMBER:
        if text_lower.startswith("verified"):
            parts = text_lower.split()
            if len(parts) >= 2:
                cust = parts[1]
                await db_execute("UPDATE orders SET status='confirmed' WHERE phone=? AND status='pending'", (cust,))
                await send_text(OWNER_NUMBER, f"✅ Verified {cust}.")
                await send_text(cust, "✅ Payment confirmed. We're cooking!")
            return
        if text_lower == "summary":
            pending = await asyncio.to_thread(_sync_db_call, "SELECT * FROM orders WHERE status='pending'", fetch_all=True)
            if not pending: await send_text(OWNER_NUMBER, "No pending orders."); return
            msg = "📋 PENDING:\n\n"
            for o in pending: msg += f"ID:{o['id']} | {o['phone']} | GHS{o['total_price']} | {o['delivery_type']}\n"
            await send_text(OWNER_NUMBER, msg)
            return

    # Customer Location Input
    if state == "awaiting_location":
        data["location"] = text
        await send_buttons(phone, "How will you pay?", [
            {"id": "pay_momo", "title": "📱 MoMo"},
            {"id": "pay_cash", "title": "💵 Cash"},
            {"id": "back_delivery", "title": "⬅ Back"}
        ])
        await save_state(phone, "checkout_payment", data)
        return

    # Customer MoMo Confirmation
    if state == "awaiting_momo_confirmation":
        if "yes" in text_lower:
            await db_execute("UPDATE orders SET status='paid' WHERE phone=? AND status='pending'", (phone,))
            await send_text(OWNER_NUMBER, f"💰 {phone} claims they sent MoMo. Verify and reply 'verified {phone}'.")
            await send_text(phone, "⏳ Waiting for owner to check MoMo. We'll confirm shortly!")
            await clear_state(phone)
        return

    # Default: Show Main Menu
    sections = build_category_list()
    await send_list(phone, "🍽️ *FRANK FRIED KITCHEN*", "Welcome! Tap below to view our menu and order.", "View Categories", sections)
    await save_state(phone, "main_menu", data)

# --- WEBHOOK ENDPOINTS (Alderson Security Protocol) ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Verification failed", status_code=403)

background_tasks = set()

@app.post("/webhook")
async def handle_webhook(request: Request):
    signature = request.headers.get('X-Hub-Signature-256')
    if not signature or not APP_SECRET: return Response(status_code=403)
    
    body = await request.body()
    expected_sig = 'sha256=' + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig): return Response(status_code=403)

    data = json.loads(body)
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        messages = changes.get("value", {}).get("messages", [])
        
        if messages:
            msg = messages[0]
            phone = msg.get("from")
            _, user_data = await get_state(phone)
            
            if msg.get("type") == "interactive":
                interactive = msg["interactive"]
                if "button_reply" in interactive:
                    payload_id = interactive["button_reply"]["id"]
                elif "list_reply" in interactive:
                    payload_id = interactive["list_reply"]["id"]
                else:
                    return {"status": "ok"}
                
                task = asyncio.create_task(route_interaction(phone, payload_id, user_data))
            elif msg.get("type") == "text":
                text = msg["text"].get("body", "")
                task = asyncio.create_task(process_text(phone, text, user_data))
            else:
                task = asyncio.create_task(send_text(phone, "Please use the menu buttons to order."))
                
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
            
    except Exception as e:
        print(f"Webhook Error: {e}")
        
    return {"status": "ok"}

init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

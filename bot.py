"""
Frank Fried WhatsApp Order Bot
Complete tap-to-order system with cart, MoMo, and delivery.
"""
from datetime import datetime, time
import os
import hashlib
import hmac
import time
import logging
import requests
from fastapi import FastAPI, Request, HTTPException, Header
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# 1. CONFIGURATION — Env vars mapped to YOUR actual .env keys
# ============================================================
WHATSAPP_TOKEN = os.getenv("META_TOKEN")
APP_SECRET = os.getenv("APP_SECRET")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
OWNER_PHONE = os.getenv("OWNER_NUMBER")
MOMO_MTN = os.getenv("MOMO_MTN")
MOMO_TELECEL = os.getenv("MOMO_TELECEL")
MOMO_NAME = os.getenv("MOMO_NAME")

if not all([WHATSAPP_TOKEN, PHONE_NUMBER_ID, VERIFY_TOKEN, OWNER_PHONE]):
    raise RuntimeError("Missing required environment variables. Check your .env file.")

API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}

# Session timeout in seconds (30 minutes)
SESSION_TIMEOUT = 1800

# ============================================================
# 2. MENU — Single source of truth. NEVER trust client prices.
# ============================================================
MENU = {
    "Rice Dishes": {
        "Fried Rice": 30.0,
        "Assorted Fried Rice": 60.0,
        "Jollof Rice": 30.0,
        "Assorted Jollof Rice": 60.0,
        "Plain Rice & Fish": 30.0,
        "Plain Rice & Chicken": 30.0,
        "Plain Rice & Egg": 20.0,
    },
    "Waakye": {
        "Waakye & Chicken": 30.0,
        "Waakye & Fish": 30.0,
        "Waakye & Egg": 20.0,
    },
    "Noodles & Pasta": {
        "Spaghetti & Chicken": 30.0,
        "Assorted Spaghetti": 40.0,
        "Indomie & Chicken": 30.0,
        "Assorted Indomie": 50.0,
    }
}
# Flat lookup: item_name -> price (built once at startup)
PRICE_LOOKUP = {}
for category, items in MENU.items():
    for item_name, price in items.items():
        PRICE_LOOKUP[item_name] = price

# Reverse lookup: item_name -> category
CATEGORY_LOOKUP = {}
for category, items in MENU.items():
    for item_name in items:
        CATEGORY_LOOKUP[item_name] = category


# ============================================================
# 3. STATE MANAGEMENT — Per-user session with timestamps
# ============================================================
user_states = {}


def get_state(phone: str) -> dict:
    """Get or create user state. Auto-expires stale sessions."""
    now = time.time()

    if phone in user_states:
        session = user_states[phone]
        if now - session.get("last_active", 0) > SESSION_TIMEOUT:
            logger.info(f"Session expired for {phone}. Resetting.")
            del user_states[phone]
        else:
            session["last_active"] = now
            return session

    user_states[phone] = {
        "state": "MAIN_MENU",
        "cart": {},
        "context": {},
        "last_active": now,
    }
    return user_states[phone]


def set_state(phone: str, state: str, cart: dict = None, context: dict = None):
    """Update user state fields without overwriting untouched fields."""
    session = get_state(phone)
    session["state"] = state
    session["last_active"] = time.time()
    if cart is not None:
        session["cart"] = cart
    if context is not None:
        session["context"] = context


def reset_state(phone: str):
    """Clear cart and context after order completion."""
    user_states[phone] = {
        "state": "MAIN_MENU",
        "cart": {},
        "context": {},
        "last_active": time.time(),
    }


# ============================================================
# 4. WHATSAPP API HELPERS
# ============================================================
def send_whatsapp_payload(payload: dict):
    """Send payload to WhatsApp Cloud API with error handling."""
    try:
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"API OK: {response.json().get('messages', [{}])[0].get('id', 'n/a')}")
    except requests.exceptions.RequestException as e:
        error_body = e.response.text if e.response else str(e)
        logger.error(f"WhatsApp API error: {error_body}")


def send_text(phone: str, text: str):
    """Send a plain text message."""
    send_whatsapp_payload({
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    })


def send_list(phone: str, body: str, button_text: str, sections: list):
    """Send an interactive list message (up to 10 rows per section)."""
    send_whatsapp_payload({
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": sections,
            },
        },
    })


def send_buttons(phone: str, body: str, buttons: list):
    """Send interactive reply buttons (MAX 3 buttons)."""
    if len(buttons) > 3:
        logger.error(f"Attempted to send {len(buttons)} buttons. Max is 3.")
        return

    send_whatsapp_payload({
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    })


# ============================================================
# 5. SCREEN FUNCTIONS — Each function renders one "screen"
# ============================================================
def screen_main_menu(phone: str):
    """Show food categories as a list."""
    sections = [
        {
            "title": "Categories",
            "rows": [
                {
                    "id": f"cat_{cat}",
                    "title": cat,
                    "description": f"{len(items)} items available",
                }
                for cat, items in MENU.items()
            ],
        }
    ]
    
    # --- UPDATED WELCOME MESSAGE ---
    send_list(
        phone, 
        "🍗 *Welcome to Frank Fried!*\n\n"
        "📍 Ben-Barquarye St (Old St. Francis)\n"
        "🕐 7am-10pm Mon-Sat\n"
        "🚚 Delivery 8am-3pm\n\n"
        "What are you craving today?", 
        "View Menu", 
        sections
    )
    
    set_state(phone, "MAIN_MENU")

def screen_category_items(phone: str, category: str):
    """Show items in a category with prices."""
    if category not in MENU:
        send_text(phone, "❌ Category not found.")
        return screen_main_menu(phone)

    items = MENU[category]
    rows = [
        {
            "id": f"item_{item_name}",
            "title": item_name[:24],  # WhatsApp title limit is 24 chars
            "description": f"GHS {price:.2f}",
        }
        for item_name, price in items.items()
    ]

    # Add a back row
    rows.append({"id": "nav_back_menu", "title": "⬅ Back to Categories", "description": ""})

    sections = [{"title": category, "rows": rows}]
    send_list(phone, f"📋 *{category}*\n\nTap an item to add to your cart.", "Select Item", sections)
    set_state(phone, "VIEW_ITEMS", context={"current_category": category})


def screen_cart_actions(phone: str):
    """After adding an item, show cart action buttons."""
    state = get_state(phone)
    cart = state["cart"]

    if not cart:
        send_text(phone, "🛒 Your cart is empty.")
        return screen_main_menu(phone)

    # Build cart preview text
    cart_text = "🛒 *Your Cart:*\n\n"
    total = 0.0
    for item_name, qty in cart.items():
        price = PRICE_LOOKUP.get(item_name, 0.0)
        subtotal = price * qty
        total += subtotal
        cart_text += f"• {qty}x {item_name} — GHS {subtotal:.2f}\n"
    cart_text += f"\n*Total: GHS {total:.2f}*"

    # 3 buttons (WhatsApp max)
    send_buttons(phone, cart_text, [
        {"id": "cart_add_more", "title": "➕ Add More"},
        {"id": "cart_checkout", "title": "✅ Checkout"},
        {"id": "cart_remove", "title": "🗑 Remove"},
    ])
    set_state(phone, "CART_ACTIONS")


def screen_remove_item(phone: str):
    """Show items in cart for removal selection."""
    state = get_state(phone)
    cart = state["cart"]

    if not cart:
        send_text(phone, "🛒 Your cart is already empty.")
        return screen_main_menu(phone)

    rows = []
    for item_name, qty in cart.items():
        price = PRICE_LOOKUP.get(item_name, 0.0)
        rows.append({
            "id": f"rm_{item_name}",
            "title": item_name[:24],
            "description": f"Qty: {qty} | GHS {price * qty:.2f}",
        })

    rows.append({"id": "nav_back_cart", "title": "⬅ Back to Cart", "description": ""})

    sections = [{"title": "Remove Item", "rows": rows}]
    send_list(phone, "🗑 Select an item to remove (removes 1 quantity).", "Select", sections)
    set_state(phone, "CART_REMOVE_SELECT")


def screen_checkout_type(phone: str):
    """Ask pickup or delivery."""
    send_buttons(phone, "📦 *How will you receive your order?*", [
        {"id": "type_pickup", "title": "🏃 Pickup"},
        {"id": "type_delivery", "title": "🛵 Delivery"},
        {"id": "nav_back_cart", "title": "⬅ Back"},
    ])
    set_state(phone, "CHECKOUT_TYPE")


def screen_payment_method(phone: str):
    """Ask payment method."""
    send_buttons(phone, "💳 *How would you like to pay?*", [
        {"id": "pay_cash", "title": "💵 Cash"},
        {"id": "pay_momo", "title": "📱 MoMo"},
        {"id": "nav_back_type", "title": "⬅ Back"},
    ])
    set_state(phone, "PAYMENT_METHOD")


def screen_momo_details(phone: str):
    """Show MoMo payment details and ask for confirmation."""
    state = get_state(phone)
    total = state["context"].get("total", 0.0)

    momo_msg = (
        f"📱 *Mobile Money Payment*\n\n"
        f"Send *GHS {total:.2f}* to:\n\n"
        f"👤 *Name:* {MOMO_NAME}\n\n"
        f"🟡 *MTN:* {MOMO_MTN}\n"
        f"🔴 *Telecel:* {MOMO_TELECEL}\n\n"
        f"Tap below after sending payment."
    )
    send_text(phone, momo_msg)

    send_buttons(phone, "Have you sent the payment?", [
        {"id": "momo_confirmed", "title": "✅ Yes, Sent"},
        {"id": "nav_back_pay", "title": "⬅ Back"},
    ])
    set_state(phone, "AWAITING_MOMO_CONFIRM")

# ============================================================
# 6. BUSINESS LOGIC
# ============================================================
def build_order_summary(cart: dict) -> tuple:
    """Build order summary text and calculate total from backend prices."""
    summary = "🧾 *Order Summary*\n\n"
    total = 0.0

    for item_name, qty in cart.items():
        price = PRICE_LOOKUP.get(item_name, 0.0)
        subtotal = price * qty
        total += subtotal
        summary += f"• {qty}x {item_name} — GHS {subtotal:.2f}\n"

    summary += f"\n*Total: GHS {total:.2f}*"
    return summary, total


def finalize_order(phone: str):
    """Send final confirmation to customer and notify owner."""
    state = get_state(phone)
    ctx = state["context"]
    cart = state["cart"]

    summary_text, total = build_order_summary(cart)

    # --- Customer confirmation ---
    confirm = f"✅ *Order Confirmed!*\n\n{summary_text}\n\n"
    confirm += f"📦 *Type:* {ctx.get('order_type', 'N/A')}\n"
    if ctx.get("order_type") == "Delivery":
        confirm += f"📍 *Address:* {ctx.get('address', 'N/A')}\n"
    confirm += f"💳 *Payment:* {ctx.get('payment', 'N/A')}\n"
    if ctx.get("payment") == "Mobile Money":
        confirm += f"📞 *MoMo:* {MOMO_NUMBER}\n"
    confirm += "\nThank you for ordering from Frank Fried! 🍗"

    send_text(phone, confirm)

    # --- Owner notification ---
    owner_msg = f"🚨 *NEW ORDER from {phone}*\n\n{summary_text}\n\n"
    owner_msg += f"📦 *Type:* {ctx.get('order_type', 'N/A')}\n"
    if ctx.get("order_type") == "Delivery":
        owner_msg += f"📍 *Address:* {ctx.get('address', 'N/A')}\n"
    owner_msg += f"💳 *Payment:* {ctx.get('payment', 'N/A')}\n"
    owner_msg += f"⏰ *Time:* {time.strftime('%Y-%m-%d %H:%M:%S')}"

    send_text(OWNER_PHONE, owner_msg)

    # Reset session
    reset_state(phone)


# ============================================================
# 7. MESSAGE HANDLERS — Process user actions per state
# ============================================================
def handle_list_reply(phone: str, reply_id: str, current_state: str):
    """Handle list message selections."""

    # --- MAIN MENU: Category selected ---
    if current_state == "MAIN_MENU" and reply_id.startswith("cat_"):
        category = reply_id[4:]  # strip "cat_"
        return screen_category_items(phone, category)

    # --- VIEW ITEMS: Item selected or back ---
    if current_state == "VIEW_ITEMS":
        if reply_id == "nav_back_menu":
            return screen_main_menu(phone)

        if reply_id.startswith("item_"):
            item_name = reply_id[5:]  # strip "item_"
            price = PRICE_LOOKUP.get(item_name)

            if price is None:
                send_text(phone, "❌ Item not found. Please try again.")
                return screen_main_menu(phone)

            # Add to cart (server-side only)
            state = get_state(phone)
            cart = state["cart"]
            cart[item_name] = cart.get(item_name, 0) + 1
            set_state(phone, "CART_ACTIONS", cart=cart)

            send_text(phone, f"✅ *{item_name}* added!\nQuantity: {cart[item_name]}")
            return screen_cart_actions(phone)

    # --- CART REMOVE SELECT: Item to remove or back ---
    if current_state == "CART_REMOVE_SELECT":
        if reply_id == "nav_back_cart":
            return screen_cart_actions(phone)

        if reply_id.startswith("rm_"):
            item_name = reply_id[3:]  # strip "rm_"
            state = get_state(phone)
            cart = state["cart"]

            if item_name in cart:
                cart[item_name] -= 1
                if cart[item_name] <= 0:
                    del cart[item_name]
                set_state(phone, "CART_ACTIONS", cart=cart)

                if cart:
                    send_text(phone, f"🗑 Removed 1x *{item_name}*.")
                    return screen_cart_actions(phone)
                else:
                    send_text(phone, "🛒 Cart is now empty.")
                    return screen_main_menu(phone)
            else:
                send_text(phone, "Item not in cart.")
                return screen_cart_actions(phone)

    logger.warning(f"Unhandled list_reply: state={current_state}, id={reply_id}")
    send_text(phone, "Something went wrong. Starting over.")
    return screen_main_menu(phone)


def handle_button_reply(phone: str, reply_id: str, current_state: str):
    """Handle button reply selections."""

    # --- CART ACTIONS ---
    if current_state == "CART_ACTIONS":
        if reply_id == "cart_add_more":
            return screen_main_menu(phone)
            
        if reply_id == "cart_checkout":
            state = get_state(phone)
            if not state["cart"]:
                send_text(phone, "🛒 Your cart is empty! Add items first.")
                return screen_main_menu(phone)
                
            # --- FIX: Calculate total immediately upon checkout ---
            _, total = build_order_summary(state["cart"])
            state["context"]["total"] = total
            set_state(phone, "CHECKOUT_TYPE", context=state["context"])
            
            return screen_checkout_type(phone)
            
        if reply_id == "cart_remove":
            return screen_remove_item(phone)
    # --- CHECKOUT TYPE ---
    if current_state == "CHECKOUT_TYPE":
        if reply_id == "nav_back_cart":
            return screen_cart_actions(phone)
        if reply_id == "type_pickup":
            state = get_state(phone)
            ctx = state["context"]
            ctx["order_type"] = "Pickup"
            set_state(phone, "PAYMENT_METHOD", context=ctx)
            return screen_payment_method(phone)
            
        if reply_id == "type_delivery":
            # --- SECURITY & LOGIC: ENFORCE DELIVERY HOURS ---
            current_time = datetime.now().time()
            delivery_start = time(8, 0)   # 08:00 AM
            delivery_end = time(15, 0)    # 03:00 PM (15:00 in 24hr format)
            
            if not (delivery_start <= current_time <= delivery_end):
                send_text(
                    phone, 
                    "⚠️ *Delivery Unavailable*\n\n"
                    "Frank Fried delivery is only available from *8:00 AM to 3:00 PM*.\n\n"
                    "Please select *Pickup* or try again during delivery hours!"
                )
                return screen_checkout_type(phone) # Keep them on this screen

            # If time is valid, proceed to address
            state = get_state(phone)
            ctx = state["context"]
            ctx["order_type"] = "Delivery"
            set_state(phone, "AWAITING_ADDRESS", context=ctx)
            
            send_text(
                phone, 
                "📍 Please reply with your *delivery address*.\n\n"
                "🛵 *Note:* Delivery charges apply, but you get discounts on multiple orders!"
            )
            return
    # --- PAYMENT METHOD ---
    if current_state == "PAYMENT_METHOD":
        if reply_id == "nav_back_type":
            return screen_checkout_type(phone)
        if reply_id == "pay_cash":
            state = get_state(phone)
            ctx = state["context"]
            ctx["payment"] = "Cash"
            set_state(phone, "CONFIRMED", context=ctx)
            return finalize_order(phone)
        if reply_id == "pay_momo":
            state = get_state(phone)
            ctx = state["context"]
            ctx["payment"] = "Mobile Money"
            set_state(phone, "AWAITING_MOMO_CONFIRM", context=ctx)
            return screen_momo_details(phone)

    # --- MOMO CONFIRMATION ---
    if current_state == "AWAITING_MOMO_CONFIRM":
        if reply_id == "nav_back_pay":
            return screen_payment_method(phone)
        if reply_id == "momo_confirmed":
            return finalize_order(phone)

    logger.warning(f"Unhandled button_reply: state={current_state}, id={reply_id}")
    send_text(phone, "Something went wrong. Starting over.")
    return screen_main_menu(phone)


def handle_text_message(phone: str, text: str, current_state: str):
    """Handle plain text messages."""
    text_lower = text.strip().lower()

    # Global commands (work in any state)
    if text_lower in ("hi", "hello", "start", "menu", "home"):
        reset_state(phone)
        return screen_main_menu(phone)

    if text_lower in ("cart", "my cart", "view cart"):
        state = get_state(phone)
        if state["cart"]:
            return screen_cart_actions(phone)
        else:
            send_text(phone, "🛒 Your cart is empty. Type *menu* to start ordering.")
            return

    # --- AWAITING ADDRESS ---
    if current_state == "AWAITING_ADDRESS":
        if len(text.strip()) < 5:
            send_text(phone, "⚠️ Please enter a valid address (at least 5 characters).")
            return

        state = get_state(phone)
        ctx = state["context"]
        ctx["address"] = text.strip()
        # Pre-calculate total for MoMo display later
        _, total = build_order_summary(state["cart"])
        ctx["total"] = total
        set_state(phone, "PAYMENT_METHOD", context=ctx)
        return screen_payment_method(phone)

    # Fallback
    send_text(
        phone,
        "I didn't understand that.\n\n"
        "Type *menu* to browse food\n"
        "Type *cart* to view your order"
    )


# ============================================================
# 8. WEBHOOK SECURITY — HMAC Signature Verification
# ============================================================
def verify_signature(payload_body: bytes, signature_header: str) -> bool:
    """Verify the X-Hub-Signature-256 header from Meta."""
    if not APP_SECRET:
        logger.warning("APP_SECRET not set. Skipping signature verification.")
        return True  # Allow in dev if no secret

    if not signature_header:
        return False

    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


# ============================================================
# 9. FASTAPI APPLICATION
# ============================================================
app = FastAPI(title="Frank Fried WhatsApp Bot")


@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta sends a GET request to verify you own this URL.
    It sends: hub.mode=subscribe, hub.verify_token=YOUR_TOKEN, hub.challenge=RANDOM_INT
    You must return the challenge integer.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return int(challenge)

    logger.warning(f"Webhook verification failed. Token received: {token}")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Handle incoming WhatsApp messages and button interactions."""

    # Read raw body for signature verification
    body_bytes = await request.body()

    # --- SECURITY: Verify request actually came from Meta ---
    if APP_SECRET and not verify_signature(body_bytes, x_hub_signature_256):
        logger.warning("Invalid webhook signature. Rejecting request.")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        body = await request.json()
    except Exception:
        logger.error("Failed to parse JSON body")
        return {"status": "error"}

    # --- Parse WhatsApp payload ---
    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
    except (IndexError, TypeError):
        return {"status": "ok"}

    # Ignore status updates, delivery receipts, etc.
    messages = value.get("messages")
    if not messages:
        return {"status": "ok"}

    message = messages[0]
    phone = message.get("from")
    msg_type = message.get("type")

    if not phone or not msg_type:
        return {"status": "ok"}

    state = get_state(phone)
    current_state = state["state"]

    logger.info(f"[{phone}] state={current_state} type={msg_type}")

    # --- Route by message type ---
    try:
        if msg_type == "interactive":
            interactive = message.get("interactive", {})
            int_type = interactive.get("type")

            if int_type == "list_reply":
                reply_id = interactive["list_reply"]["id"]
                handle_list_reply(phone, reply_id, current_state)

            elif int_type == "button_reply":
                reply_id = interactive["button_reply"]["id"]
                handle_button_reply(phone, reply_id, current_state)

        elif msg_type == "text":
            text_body = message["text"]["body"]
            handle_text_message(phone, text_body, current_state)

        else:
            send_text(phone, "I can only process text and button selections. Type *menu* to start.")

    except Exception as e:
        logger.error(f"Error processing message from {phone}: {e}", exc_info=True)
        send_text(phone, "⚠️ Something went wrong. Type *menu* to start over.")
        reset_state(phone)

    return {"status": "ok"}


# ============================================================
# 10. ENTRY POINT
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8000, reload=True)

import os
import sqlite3
import logging
import json
import re
from datetime import datetime
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Config ──────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
DB_PATH = os.environ.get("DB_PATH", "cards.db")

client = Anthropic(api_key=ANTHROPIC_KEY)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Database ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        card_name TEXT,
        buy_price REAL,
        current_price REAL,
        alert_pct REAL DEFAULT 15,
        added_date TEXT,
        UNIQUE(chat_id, card_name)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_name TEXT,
        price REAL,
        date TEXT
    )""")
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH)

# ── AI Price Search ───────────────────────────────────────────────────────────
async def search_card_price(card_name: str) -> dict:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system="""Sei un esperto di carte One Piece TCG. Cerca il prezzo della carta su Cardmarket Europa.
Rispondi SOLO con JSON valido, nessun altro testo:
{"found": true, "name": "nome carta", "price": 12.50, "trend": 11.80, "set": "OP-01", "url": "https://www.cardmarket.com/..."}
Se non trovata: {"found": false, "name": "nome cercato"}""",
            messages=[{"role": "user", "content": f"Prezzo attuale su Cardmarket Europa: {card_name}"}]
        )
        for block in response.content:
            if block.type == "text":
                text = re.sub(r"```json|```", "", block.text).strip()
                return json.loads(text)
    except Exception as e:
        logger.error(f"search_card_price error: {e}")
    return {"found": False, "name": card_name}

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt(n): return f"{n:.2f}"
def pl_str(pl, pct): return f"{'+'if pl>=0 else ''}€{fmt(pl)} ({'+'if pct>=0 else ''}{pct:.1f}%)"
def pl_emoji(pl): return "📈" if pl > 0 else "📉" if pl < 0 else "➡️"

# ── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🃏 *OnePiece Card Tracker*\n\n"
        "Traccia prezzi, ricevi alert e monitora le tue carte su Cardmarket\.\n\n"
        "*Comandi:*\n"
        "/cerca `<carta>` — cerca prezzo su Cardmarket\n"
        "/aggiungi `<carta> <€acquisto>` — aggiungi alla watchlist\n"
        "/lista — watchlist con P/L\n"
        "/storico `<carta>` — storico prezzi\n"
        "/aggiorna — aggiorna tutti i prezzi ora\n"
        "/rimuovi `<carta>` — rimuovi dalla watchlist\n"
        "/eventi — prossimi tornei in Italia\n\n"
        "*Esempio:* /cerca Monkey D\. Luffy OP01\-060",
        parse_mode="MarkdownV2"
    )

async def cmd_cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /cerca <nome carta>\nEsempio: /cerca Luffy OP01-060")
        return
    card = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Cerco *{card}* su Cardmarket...", parse_mode="Markdown")
    result = await search_card_price(card)
    if result.get("found"):
        price = result.get("price", "N/D")
        trend = result.get("trend", "N/D")
        text = (
            f"🃏 *{result['name']}*\n\n"
            f"💰 Prezzo: *€{fmt(price) if isinstance(price, float) else price}*\n"
            f"📊 Trend: €{fmt(trend) if isinstance(trend, float) else trend}\n"
            f"📦 Set: {result.get('set', 'N/D')}\n\n"
            f"[Vedi su Cardmarket]({result.get('url', '#')})\n\n"
            f"Aggiungi con /aggiungi {result['name']} <prezzo\_acquisto>"
        )
    else:
        text = f"❌ Carta *{card}* non trovata su Cardmarket."
    await msg.edit_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /aggiungi <carta> <prezzo acquisto>\nEsempio: /aggiungi Luffy OP01-060 15.50")
        return
    try:
        buy_price = float(context.args[-1].replace(",", "."))
        card_name = " ".join(context.args[:-1])
    except ValueError:
        await update.message.reply_text("Il prezzo deve essere un numero. Es: /aggiungi Luffy 15.50")
        return

    chat_id = str(update.effective_chat.id)
    msg = await update.message.reply_text(f"🔍 Cerco prezzo attuale di *{card_name}*...", parse_mode="Markdown")
    result = await search_card_price(card_name)
    current_price = result.get("price", buy_price) if result.get("found") and isinstance(result.get("price"), float) else buy_price

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO watchlist (chat_id, card_name, buy_price, current_price, added_date) VALUES (?,?,?,?,?)",
        (chat_id, card_name, buy_price, current_price, datetime.now().isoformat())
    )
    c.execute("INSERT INTO price_history (card_name, price, date) VALUES (?,?,?)",
              (card_name, current_price, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    pl = current_price - buy_price
    pct = (pl / buy_price * 100) if buy_price > 0 else 0
    await msg.edit_text(
        f"✅ *{card_name}* aggiunta!\n\n"
        f"💸 Acquisto: €{fmt(buy_price)}\n"
        f"💰 Attuale: €{fmt(current_price)}\n"
        f"{pl_emoji(pl)} P/L: {pl_str(pl, pct)}\n\n"
        f"🔔 Alert automatico: ±15%",
        parse_mode="Markdown"
    )

async def cmd_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT card_name, buy_price, current_price FROM watchlist WHERE chat_id=?", (chat_id,))
    cards = c.fetchall()
    conn.close()

    if not cards:
        await update.message.reply_text("Watchlist vuota — usa /aggiungi per aggiungere carte!")
        return

    text = "📋 *Watchlist:*\n\n"
    total_buy = total_curr = 0
    for name, buy, curr in cards:
        pl = curr - buy
        pct = (pl / buy * 100) if buy > 0 else 0
        text += f"{pl_emoji(pl)} *{name}*\n"
        text += f"   €{fmt(buy)} → €{fmt(curr)} | {pl_str(pl, pct)}\n\n"
        total_buy += buy
        total_curr += curr

    total_pl = total_curr - total_buy
    total_pct = (total_pl / total_buy * 100) if total_buy > 0 else 0
    text += f"━━━━━━━━━━━━━━\n"
    text += f"💼 Investito: €{fmt(total_buy)}\n"
    text += f"💰 Valore: €{fmt(total_curr)}\n"
    text += f"📊 P/L totale: {pl_str(total_pl, total_pct)}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_storico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /storico <carta>")
        return
    card = " ".join(context.args)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT price, date FROM price_history WHERE card_name LIKE ? ORDER BY date DESC LIMIT 15", (f"%{card}%",))
    history = c.fetchall()
    conn.close()

    if not history:
        await update.message.reply_text(f"Nessuno storico per *{card}*.\nAggiungi la carta con /aggiungi per iniziare a tracciare!", parse_mode="Markdown")
        return

    text = f"📈 *Storico: {card}*\n\n"
    for price, date in history:
        dt = datetime.fromisoformat(date)
        text += f"`{dt.strftime('%d/%m %H:%M')}` → €{fmt(price)}\n"

    if len(history) > 1:
        delta = history[0][0] - history[-1][0]
        delta_pct = (delta / history[-1][0] * 100) if history[-1][0] > 0 else 0
        text += f"\n📊 Variazione: {pl_str(delta, delta_pct)}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /rimuovi <carta>")
        return
    card = " ".join(context.args)
    chat_id = str(update.effective_chat.id)
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE chat_id=? AND card_name LIKE ?", (chat_id, f"%{card}%"))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        await update.message.reply_text(f"✅ *{card}* rimossa.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Carta non trovata nella watchlist.")

async def cmd_eventi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Cerco tornei One Piece in Italia...")
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system="Sei un assistente TCG. Rispondi sempre in italiano. Sii conciso e diretto.",
            messages=[{"role": "user", "content": f"Prossimi tornei e eventi One Piece Card Game in Italia, prossimi 2 mesi. Data oggi: {datetime.now().strftime('%d/%m/%Y')}. Cerca su cardmarket, onepiece-cardgame.com, e siti italiani di TCG."}]
        )
        text = "🏆 *Prossimi eventi One Piece TCG in Italia:*\n\n"
        for block in response.content:
            if block.type == "text":
                text += block.text
                break
    except Exception as e:
        text = "❌ Impossibile recuperare gli eventi al momento."
    await msg.edit_text(text, parse_mode="Markdown")

async def cmd_aggiorna(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT card_name, buy_price, current_price FROM watchlist WHERE chat_id=?", (chat_id,))
    cards = c.fetchall()
    conn.close()

    if not cards:
        await update.message.reply_text("Watchlist vuota!")
        return

    msg = await update.message.reply_text(f"🔄 Aggiorno {len(cards)} carte...")
    updated = 0
    for name, buy, old_price in cards:
        result = await search_card_price(name)
        if result.get("found") and isinstance(result.get("price"), float):
            new_price = result["price"]
            conn = get_conn()
            c = conn.cursor()
            c.execute("UPDATE watchlist SET current_price=? WHERE chat_id=? AND card_name=?", (new_price, chat_id, name))
            c.execute("INSERT INTO price_history (card_name, price, date) VALUES (?,?,?)", (name, new_price, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            updated += 1

    await msg.edit_text(f"✅ Aggiornate {updated}/{len(cards)} carte.\nUsa /lista per vedere i prezzi aggiornati.")

# ── Scheduler ─────────────────────────────────────────────────────────────────
async def check_alerts(app):
    logger.info("Running scheduled alert check...")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT chat_id, card_name, current_price, alert_pct FROM watchlist")
    cards = c.fetchall()
    conn.close()

    for chat_id, card_name, old_price, alert_pct in cards:
        if not old_price:
            continue
        result = await search_card_price(card_name)
        if not result.get("found") or not isinstance(result.get("price"), float):
            continue

        new_price = result["price"]
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE watchlist SET current_price=? WHERE chat_id=? AND card_name=?", (new_price, chat_id, card_name))
        c.execute("INSERT INTO price_history (card_name, price, date) VALUES (?,?,?)", (card_name, new_price, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        change_pct = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0
        if abs(change_pct) >= alert_pct:
            direction = "SALITO 🚀" if change_pct > 0 else "SCESO 📉"
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 *ALERT — {card_name}*\n\nPrezzo {direction} del {abs(change_pct):.1f}%!\n💰 €{fmt(old_price)} → €{fmt(new_price)}",
                parse_mode="Markdown"
            )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("cerca", cmd_cerca))
    app.add_handler(CommandHandler("aggiungi", cmd_aggiungi))
    app.add_handler(CommandHandler("lista", cmd_lista))
    app.add_handler(CommandHandler("storico", cmd_storico))
    app.add_handler(CommandHandler("rimuovi", cmd_rimuovi))
    app.add_handler(CommandHandler("eventi", cmd_eventi))
    app.add_handler(CommandHandler("aggiorna", cmd_aggiorna))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_alerts, "interval", hours=6, args=[app])
    scheduler.start()

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

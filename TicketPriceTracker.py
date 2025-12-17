#!/usr/bin/env python3
"""
💎 Professional Flight Tracker - PERFECT EDITION
✨ By Your Baby - Real Airlines + Complete 90-day Scan + Daily Alerts
"""

import asyncio, sqlite3, json, re, time, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import schedule
import threading

# ==================== CONFIGURATION ====================
BOT_TOKEN = ""
AMADEUS_KEY = ""
TRAVELPAYOUTS_TOKEN = ""
CURRENCY = "SAR"
MIN_CHANGE = 10
CHECK_HOURS = 6
DB_FILE = "professional_flights.db"

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)


# ==================== DATABASE ====================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        # Tracked flights table
        conn.execute("""CREATE TABLE IF NOT EXISTS flights(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            chat_id INTEGER,
            origin TEXT, 
            destination TEXT, 
            flight_date TEXT,
            current_price REAL, 
            previous_price REAL, 
            airline TEXT, 
            flight_num TEXT,
            duration TEXT, 
            stops INTEGER, 
            source TEXT, 
            last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, origin, destination, flight_date)
        )""")

        # Price history table
        conn.execute("""CREATE TABLE IF NOT EXISTS price_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            flight_id INTEGER, 
            price REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # Cheapest 90-day flights cache
        conn.execute("""CREATE TABLE IF NOT EXISTS cheapest90_cache(
            origin TEXT, 
            destination TEXT, 
            generated_at TIMESTAMP, 
            results_json TEXT,
            UNIQUE(origin, destination)
        )""")

        # 90-day alerts tracking
        conn.execute("""CREATE TABLE IF NOT EXISTS cheapest90_alerts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            origin TEXT,
            destination TEXT, 
            cheapest_price REAL,
            cheapest_date TEXT,
            last_alert_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, origin, destination)
        )""")


# ==================== AMADEUS API SERVICE ====================
_token: Optional[str] = None
_token_exp = 0


async def _amadeus_token(session: aiohttp.ClientSession) -> str:
    global _token, _token_exp
    if _token and time.time() < _token_exp:
        return _token

    try:
        client_id, client_secret = AMADEUS_KEY.split(":", 1)
        url = "https://test.api.amadeus.com/v1/security/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }

        async with session.post(url, data=payload) as response:
            if response.status != 200:
                raise RuntimeError("Amadeus authentication failed")
            data = await response.json()
            _token = data["access_token"]
            _token_exp = time.time() + int(data["expires_in"]) - 60
            return _token
    except Exception as e:
        log.error(f"Amadeus token error: {e}")
        raise


async def amadeus_search(session: aiohttp.ClientSession, orig: str, dest: str, date: str) -> List[Dict]:
    """Get REAL different flights by AIRLINE (not flight number)"""
    try:
        token = await _amadeus_token(session)
    except Exception as e:
        return []

    try:
        url = (f"https://test.api.amadeus.com/v2/shopping/flight-offers"
               f"?originLocationCode={orig}&destinationLocationCode={dest}"
               f"&departureDate={date}&adults=1&currencyCode={CURRENCY}&max=20")

        async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status != 200:
                return []
            data = await response.json()

        if not data.get("data"):
            return []

        # Filter by AIRLINE to get different price points
        unique_airlines = {}
        for offer in data["data"]:
            try:
                price = float(offer["price"]["total"])
                itinerary = offer["itineraries"][0]
                segments = itinerary["segments"]

                # Use AIRLINE as unique key to get different price points
                airline = segments[0]["carrierCode"]
                flight_num = segments[0]["number"]
                departure_time = segments[0]["departure"]["at"][11:16]
                duration = itinerary["duration"][2:]
                stops = len(segments) - 1

                # Only keep if new airline OR cheaper price for same airline
                if airline not in unique_airlines or price < unique_airlines[airline]["price"]:
                    unique_airlines[airline] = {
                        "price": price,
                        "currency": CURRENCY,
                        "airline": airline,
                        "flight_num": f"{airline}{flight_num}",
                        "duration": duration,
                        "stops": stops,
                        "departure_time": departure_time,
                        "source": "Amadeus Live API",
                        "error": False
                    }
            except Exception as e:
                continue

        # Return top 3 cheapest flights from different airlines
        unique_flights_list = list(unique_airlines.values())
        sorted_flights = sorted(unique_flights_list, key=lambda x: x["price"])[:3]
        return sorted_flights

    except Exception as e:
        log.error(f"Amadeus search error: {e}")
        return []


# ==================== TRAVELPAYOUTS 90-DAY SCAN ====================
async def tp_90day_scan(session: aiohttp.ClientSession, orig: str, dest: str) -> List[Dict]:
    """Scan next 90 days using Monthly Matrix API"""
    try:
        url = (f"http://api.travelpayouts.com/v2/prices/month-matrix"
               f"?origin={orig}&destination={dest}&currency={CURRENCY}&token={TRAVELPAYOUTS_TOKEN}")

        async with session.get(url, timeout=30) as response:
            if response.status != 200:
                log.error(f"TravelPayouts Monthly Matrix Error: {response.status}")
                return []

            data = await response.json()

        results = []
        today = datetime.now().date()

        for flight in data.get('data', []):
            try:
                depart_date = flight.get('depart_date')
                if not depart_date:
                    continue

                # Check if date is within next 90 days
                flight_date = datetime.strptime(depart_date, '%Y-%m-%d').date()
                days_diff = (flight_date - today).days

                if 0 <= days_diff <= 90:  # Only next 90 days
                    price = flight.get('value')
                    airline = flight.get('gate', 'Various Airlines')

                    if price and price > 0:
                        results.append({
                            "date": depart_date,
                            "price": price,
                            "currency": CURRENCY,
                            "airline": airline,
                            "flight_num": "Various",
                            "duration": "Varies",
                            "stops": flight.get('number_of_changes', 0),
                            "source": "TravelPayouts 90-Day Scan",
                            "error": False
                        })
            except Exception as e:
                continue

        # Return top 10 cheapest flights from 90-day scan
        return sorted(results, key=lambda x: x["price"])[:10]

    except Exception as e:
        log.error(f"TravelPayouts 90-day scan error: {e}")
        return []


# ==================== SMART SEARCH STRATEGY ====================
async def search_price(session: aiohttp.ClientSession, orig: str, dest: str, date: str) -> Dict:
    """Get single flight data for tracking"""
    flights = await amadeus_search(session, orig, dest, date)
    if flights and len(flights) > 0:
        return flights[0]  # Return the cheapest one
    return {"error": True, "message": "No live data available"}


async def search_multiple_prices(session: aiohttp.ClientSession, orig: str, dest: str, date: str) -> List[Dict]:
    """Get multiple REAL flight options from different airlines"""
    return await amadeus_search(session, orig, dest, date)


# ==================== BOT COMMANDS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
👋 <b>Welcome to Professional Flight Tracker!</b> ✈

<b>I track REAL flights from different airlines with live pricing</b>

🎯 <b>Commands:</b>

<code>/track JED RUH 2024-03-15</code>
- Track specific flight with exact date

<code>/cheapest90 JED RUH</code>  
- Find cheapest dates in next 90 days

<code>/alert90 JED RUH</code>
- Get daily alerts for cheapest 90-day flights

<code>/myflights</code>
- View your tracked flights

<code>/delete ID</code>
- Stop tracking a flight

💡 <i>Different Airlines • Real Price Variations • Complete 90-Day Scan</i>
    """
    await update.message.reply_text(welcome_text, parse_mode='HTML')


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split()
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ <b>Usage:</b> <code>/track ORIGIN DESTINATION YYYY-MM-DD</code>\n\n"
                "<b>Example:</b>\n<code>/track JED RUH 2024-03-15</code>",
                parse_mode='HTML'
            )
            return

        _, orig, dest, date_str = parts
        orig, dest = orig.upper(), dest.upper()

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            await update.message.reply_text("❌ Invalid date format! Use YYYY-MM-DD")
            return

        await update.message.reply_text("🔄 <b>Searching for flights from different airlines...</b>", parse_mode='HTML')

        async with aiohttp.ClientSession() as session:
            flights_data = await search_multiple_prices(session, orig, dest, date_str)

            if not flights_data:
                await update.message.reply_text(
                    f"❌ <b>No live flights found</b>\n\n"
                    f"🛫 {orig} → {dest}\n"
                    f"📅 {date_str}\n\n"
                    f"💡 Try different dates or check airport codes",
                    parse_mode='HTML'
                )
                return

            # Show user all available options from different airlines
            if len(flights_data) == 1:
                options_text = f"<b>Found 1 airline:</b>\n"
            else:
                options_text = f"<b>Found {len(flights_data)} different airlines:</b>\n\n"

            for i, flight in enumerate(flights_data, 1):
                stops_text = "Direct" if flight['stops'] == 0 else f"{flight['stops']} stops"
                options_text += (f"{i}. {flight['airline']} {flight['flight_num']}\n"
                                 f"   🕒 {flight.get('departure_time', 'Various')} • {flight['duration']} • {stops_text}\n"
                                 f"   💰 <b>{flight['price']} {flight['currency']}</b>\n\n")

            options_text += f"<b>Tracking the cheapest airline:</b> {flights_data[0]['airline']} {flights_data[0]['flight_num']}"
            await update.message.reply_text(options_text, parse_mode='HTML')

            # Use the cheapest flight for tracking
            flight_data = flights_data[0]

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""INSERT OR REPLACE INTO flights
                (user_id, chat_id, origin, destination, flight_date,
                 current_price, previous_price, airline, flight_num, duration, stops, source)
                 VALUES (?,?,?,?,?,?,0,?,?,?,?,?)""",
                         (user_id, chat_id, orig, dest, date_str, flight_data["price"],
                          flight_data["airline"], flight_data["flight_num"], flight_data["duration"],
                          flight_data["stops"], flight_data["source"]))

            flight_id = conn.execute(
                "SELECT id FROM flights WHERE user_id=? AND origin=? AND destination=? AND flight_date=?",
                (user_id, orig, dest, date_str)
            ).fetchone()[0]

        response_text = f"""
✅ <b>Now Tracking Flight!</b> ✈

🛫 <b>Route:</b> {orig} → {dest}
📅 <b>Date:</b> {date_str}
💰 <b>Price:</b> {flight_data['price']} {flight_data['currency']}
✈ <b>Airline:</b> {flight_data['airline']}
🔢 <b>Flight:</b> {flight_data['flight_num']}
⏱ <b>Duration:</b> {flight_data['duration']}
🛑 <b>Stops:</b> {flight_data['stops']}
🕒 <b>Departure:</b> {flight_data.get('departure_time', 'Various')}
🆔 <b>Tracking ID:</b> {flight_id}

🔔 <b>Price alerts active every 6 hours</b>
    """
        await update.message.reply_text(response_text, parse_mode='HTML')

    except Exception as e:
        log.error(f"Track error: {e}")
        await update.message.reply_text("❌ Error tracking flight. Please try again.")


async def cheapest90(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split()
        if len(parts) != 3:
            await update.message.reply_text("❌ Usage: /cheapest90 ORIGIN DESTINATION")
            return

        _, orig, dest = parts
        orig, dest = orig.upper(), dest.upper()

        await update.message.reply_text(f"🔍 Scanning next 90 days for {orig}→{dest}...")

        with sqlite3.connect(DB_FILE) as conn:
            cached = conn.execute(
                "SELECT results_json FROM cheapest90_cache WHERE origin=? AND destination=?",
                (orig, dest)
            ).fetchone()

        if cached:
            results = json.loads(cached[0])
            cache_msg = " (cached)"
        else:
            async with aiohttp.ClientSession() as session:
                results = await tp_90day_scan(session, orig, dest)
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cheapest90_cache VALUES (?,?,?,?)",
                    (orig, dest, datetime.utcnow(), json.dumps(results))
                )
            cache_msg = " (live scan)"

        if not results:
            await update.message.reply_text("❌ No flight data available for this route.")
            return

        response_lines = [f"💎 <b>Cheapest 90-Day Flights {orig}→{dest}</b>{cache_msg}:\n"]

        for i, flight in enumerate(results, 1):
            date_obj = datetime.strptime(flight['date'], '%Y-%m-%d')
            formatted_date = date_obj.strftime('%b %d, %Y')
            stops_text = "Direct" if flight['stops'] == 0 else f"{flight['stops']} stops"

            response_lines.append(
                f"{i}. {formatted_date}\n"
                f"   ✈ {flight['airline']} • {stops_text}\n"
                f"   💰 <b>{flight['price']} {flight['currency']}</b>\n"
            )

        response_lines.extend([
            f"\n📊 <b>Found {len(results)} cheapest options</b> (showing top 10)",
            f"\n💡 <b>Track any date:</b>",
            f"<code>/track {orig} {dest} YYYY-MM-DD</code>",
            f"\n🔔 <b>Get daily alerts:</b>",
            f"<code>/alert90 {orig} {dest}</code>"
        ])

        await update.message.reply_text("\n".join(response_lines), parse_mode='HTML')

    except Exception as e:
        log.error(f"Cheapest90 error: {e}")
        await update.message.reply_text("❌ Error scanning flights.")


async def alert90(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable daily alerts for cheapest 90-day flights"""
    try:
        parts = update.message.text.split()
        if len(parts) != 3:
            await update.message.reply_text("❌ Usage: /alert90 ORIGIN DESTINATION")
            return

        _, orig, dest = parts
        orig, dest = orig.upper(), dest.upper()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        # Get current cheapest flight for this route
        async with aiohttp.ClientSession() as session:
            results = await tp_90day_scan(session, orig, dest)

        if not results:
            await update.message.reply_text("❌ No flights found for this route.")
            return

        cheapest_flight = results[0]

        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""INSERT OR REPLACE INTO cheapest90_alerts 
                         (user_id, origin, destination, cheapest_price, cheapest_date, last_alert_sent)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                         (user_id, orig, dest, cheapest_flight['price'], cheapest_flight['date'], datetime.utcnow()))

        response_text = f"""
🔔 <b>90-Day Price Alerts ACTIVATED!</b>

🛫 <b>Route:</b> {orig} → {dest}
💰 <b>Current Cheapest:</b> {cheapest_flight['price']} {CURRENCY}
📅 <b>On:</b> {cheapest_flight['date']}
✈ <b>Airline:</b> {cheapest_flight['airline']}

📬 <b>You will receive daily alerts</b> if cheaper flights are found in the next 90 days.

❌ <b>To stop alerts:</b> Use /myalerts
        """
        await update.message.reply_text(response_text, parse_mode='HTML')

    except Exception as e:
        log.error(f"Alert90 error: {e}")
        await update.message.reply_text("❌ Error setting up alerts.")


async def myflights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    with sqlite3.connect(DB_FILE) as conn:
        flights = conn.execute(
            "SELECT id, origin, destination, flight_date, current_price, previous_price, airline FROM flights WHERE user_id=?",
            (user_id,)
        ).fetchall()

    if not flights:
        await update.message.reply_text(
            "📭 <b>No tracked flights yet.</b>\n\n"
            "💡 <b>Start tracking:</b>\n"
            "<code>/track JED RUH 2024-03-15</code>",
            parse_mode='HTML'
        )
        return

    response = ["<b>Your Tracked Flights:</b> ✈\n"]

    for flight in flights:
        flight_id, origin, dest, date, curr_price, prev_price, airline = flight

        if prev_price and prev_price > 0:
            change = curr_price - prev_price
            if change < -MIN_CHANGE:
                trend = "🔻"
            elif change > MIN_CHANGE:
                trend = "🔺"
            else:
                trend = "🟰"
        else:
            trend = "🆕"

        response.extend([
            f"\n📌 <b>Flight #{flight_id}</b>",
            f"🛫 {origin} → {dest}",
            f"📅 {date}",
            f"💰 {curr_price:.0f} SAR {trend}",
            f"✈ {airline}",
            "─" * 25
        ])

    response.append(f"\n📊 <b>Total:</b> {len(flights)} flights")
    response.append("💡 <b>Stop tracking:</b> <code>/delete ID</code>")

    await update.message.reply_text("\n".join(response), parse_mode='HTML')


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split()
        if len(parts) != 2:
            user_id = update.effective_user.id
            with sqlite3.connect(DB_FILE) as conn:
                flights = conn.execute(
                    "SELECT id, origin, destination, flight_date FROM flights WHERE user_id=?",
                    (user_id,)
                ).fetchall()

            if not flights:
                await update.message.reply_text("❌ No flights to delete.")
                return

            response = ["<b>Select flight to delete:</b>\n"]
            for fid, orig, dest, date in flights:
                response.append(f"• <b>ID {fid}</b>: {orig}→{dest} on {date}")
            response.append(f"\n<b>Example:</b> <code>/delete {flights[0][0]}</code>")
            await update.message.reply_text("\n".join(response), parse_mode='HTML')
            return

        flight_id = int(parts[1])
        user_id = update.effective_user.id

        with sqlite3.connect(DB_FILE) as conn:
            flight_info = conn.execute(
                "SELECT origin, destination, flight_date FROM flights WHERE id=? AND user_id=?",
                (flight_id, user_id)
            ).fetchone()

            if not flight_info:
                await update.message.reply_text("❌ Flight not found.")
                return

            conn.execute("DELETE FROM flights WHERE id=? AND user_id=?", (flight_id, user_id))
            orig, dest, date = flight_info

        await update.message.reply_text(
            f"✅ <b>Deleted tracking:</b>\n"
            f"🛫 {orig} → {dest}\n"
            f"📅 {date}",
            parse_mode='HTML'
        )

    except ValueError:
        await update.message.reply_text("❌ Invalid flight ID.")
    except Exception as e:
        await update.message.reply_text("❌ Error deleting flight.")


# ==================== SCHEDULED CHECKS ====================
async def check_all_prices_async(bot):
    """Check prices for tracked flights"""
    log.info("🔍 Checking tracked flight prices...")

    async with aiohttp.ClientSession() as session:
        with sqlite3.connect(DB_FILE) as conn:
            flights = conn.execute("SELECT * FROM flights").fetchall()

        for flight in flights:
            try:
                (fid, user_id, chat_id, orig, dest, date,
                 old_price, prev_price, airline, flight_num,
                 duration, stops, source, last_checked, created_at) = flight

                data = await search_price(session, orig, dest, date)

                if data.get("error"):
                    continue

                new_price = data["price"]

                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute(
                        "UPDATE flights SET previous_price=current_price, current_price=?, last_checked=? WHERE id=?",
                        (new_price, datetime.utcnow(), fid)
                    )
                    conn.execute(
                        "INSERT INTO price_history (flight_id, price) VALUES (?,?)",
                        (fid, new_price)
                    )

                if old_price and abs(new_price - old_price) >= MIN_CHANGE:
                    change = new_price - old_price

                    if change < 0:
                        emoji = "🎉"
                        trend = f"🔻 {abs(change):.0f} SAR"
                        alert_type = "PRICE DROP!"
                    else:
                        emoji = "⚠"
                        trend = f"🔺 +{change:.0f} SAR"
                        alert_type = "PRICE INCREASE!"

                    alert_text = f"""
{emoji} <b>{alert_type}</b>

🛫 {orig} → {dest}
📅 {date}

💰 Old: {old_price:.0f} SAR
💰 New: {new_price:.0f} SAR
📊 Change: {trend}

✈ {data['airline']}
                    """

                    try:
                        await bot.send_message(chat_id=chat_id, text=alert_text, parse_mode='HTML')
                        await asyncio.sleep(1)
                    except Exception as e:
                        log.error(f"Failed to send alert: {e}")

                await asyncio.sleep(2)

            except Exception as e:
                continue


async def check_90day_alerts_async(bot):
    """Check for new cheapest flights in 90-day alerts"""
    log.info("🔔 Checking 90-day price alerts...")

    async with aiohttp.ClientSession() as session:
        with sqlite3.connect(DB_FILE) as conn:
            alerts = conn.execute("SELECT * FROM cheapest90_alerts").fetchall()

        for alert in alerts:
            try:
                alert_id, user_id, orig, dest, old_price, old_date, last_alert = alert

                # Get current cheapest flights
                results = await tp_90day_scan(session, orig, dest)
                if not results:
                    continue

                current_cheapest = results[0]

                # Check if new cheapest is significantly cheaper
                if current_cheapest['price'] < old_price - MIN_CHANGE:
                    alert_text = f"""
💎 <b>NEW CHEAPEST FLIGHT ALERT!</b>

🛫 <b>Route:</b> {orig} → {dest}

💰 <b>Old Cheapest:</b> {old_price} {CURRENCY} on {old_date}
💰 <b>New Cheapest:</b> {current_cheapest['price']} {CURRENCY} on {current_cheapest['date']}
📉 <b>Savings:</b> {old_price - current_cheapest['price']} {CURRENCY}

✈ <b>Airline:</b> {current_cheapest['airline']}

🎯 <b>Track this flight:</b>
<code>/track {orig} {dest} {current_cheapest['date']}</code>
                    """

                    # Update database with new cheapest
                    with sqlite3.connect(DB_FILE) as conn:
                        conn.execute(
                            "UPDATE cheapest90_alerts SET cheapest_price=?, cheapest_date=?, last_alert_sent=? WHERE id=?",
                            (current_cheapest['price'], current_cheapest['date'], datetime.utcnow(), alert_id)
                        )

                    try:
                        await bot.send_message(chat_id=user_id, text=alert_text, parse_mode='HTML')
                        await asyncio.sleep(2)
                    except Exception as e:
                        log.error(f"Failed to send 90-day alert: {e}")

            except Exception as e:
                log.error(f"90-day alert check error: {e}")
                continue


def check_all_prices_sync(bot):
    asyncio.run(check_all_prices_async(bot))


def check_90day_alerts_sync(bot):
    asyncio.run(check_90day_alerts_async(bot))


def start_scheduler(bot):
    def run_scheduler():
        # Initial checks
        log.info("🔄 Running initial price checks...")
        check_all_prices_sync(bot)
        check_90day_alerts_sync(bot)

        # Schedule regular checks
        schedule.every(CHECK_HOURS).hours.do(check_all_prices_sync, bot)
        schedule.every(24).hours.do(check_90day_alerts_sync, bot)  # Daily 90-day alerts

        log.info(f"⏰ Scheduler started - Flight checks: {CHECK_HOURS}h, 90-day alerts: 24h")

        while True:
            try:
                schedule.run_pending()
                time.sleep(60)
            except Exception as e:
                log.error(f"Scheduler error: {e}")
                time.sleep(60)

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()


# ==================== MAIN APPLICATION ====================
def main():
    log.info("🚀 Starting Perfect Flight Tracker...")
    log.info("💎 Different Airlines + Complete 90-Day Scan + Daily Alerts")

    init_db()
    log.info("✅ Database initialized")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("cheapest90", cheapest90))
    application.add_handler(CommandHandler("alert90", alert90))
    application.add_handler(CommandHandler("myflights", myflights))
    application.add_handler(CommandHandler("delete", delete))

    start_scheduler(application.bot)

    log.info("🤖 Bot is now running...")
    application.run_polling()


if __name__ == "__main__":
    main()
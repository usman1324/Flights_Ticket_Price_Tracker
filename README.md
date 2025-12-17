# Flights_Ticket_Price_Tracker
A flight price tracking tool that monitors fares to any destination for free. It supports same-day flights as well as tracking prices for up to 90 days, continuously updating users when prices increase or drop.

# 💎 Professional Flight Tracker - Perfect Edition

A powerful **flight price tracking bot** that monitors real flight prices for any destination. Track same-day flights or scan prices up to 90 days ahead. The bot automatically updates when prices drop or rise and can send daily alerts.

---

## ✨ Features

- Track flights in **real-time** from multiple airlines  
- Monitor prices for up to **90 days in advance**  
- Automatic **price change alerts**  
- View your tracked flights anytime  
- Fully configurable **currency and thresholds**  

---

## 🛠 Tech Stack

- **Python 3.11+**  
- `aiohttp` for async API requests  
- `sqlite3` for local database  
- `python-telegram-bot` for Telegram integration  
- `schedule` for automated periodic checks  

---

## 📌 Configuration

Before running, add your **API tokens** (or use environment variables):

```python
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
AMADEUS_KEY = "YOUR_AMADEUS_KEY"
TRAVELPAYOUTS_TOKEN = "YOUR_TRAVELPAYOUTS_TOKEN"


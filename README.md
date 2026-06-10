# OnePiece Card Tracker Bot

Bot Telegram per tracciare prezzi carte One Piece su Cardmarket.

## Comandi
- `/cerca <carta>` — cerca prezzo su Cardmarket
- `/aggiungi <carta> <prezzo>` — aggiungi alla watchlist
- `/lista` — mostra watchlist con P/L
- `/storico <carta>` — storico prezzi
- `/aggiorna` — aggiorna tutti i prezzi ora
- `/rimuovi <carta>` — rimuovi dalla watchlist
- `/eventi` — prossimi tornei in Italia

## Setup Railway
Aggiungi queste variabili d'ambiente in Railway:
- `TELEGRAM_TOKEN` — token del bot da BotFather
- `ANTHROPIC_API_KEY` — API key di Anthropic

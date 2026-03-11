#!/usr/bin/env python3
"""
Monitor Ciência Viva no Laboratório — alerta Telegram quando abrem inscrições 2026.
"""

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import schedule
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

TARGET_URL = "https://www.cienciaviva.pt/ciencia-viva-no-laboratorio/"
STATE_FILE = Path(os.getenv("STATE_FILE", "/data/state.json"))
CHECK_TIME = os.getenv("CHECK_TIME", "09:00")  # hora local (Europe/Lisbon)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

TARGET_YEAR = "2026"
REGISTRATION_KEYWORDS = [
    "inscrições", "inscrever", "inscrição",
    "aberta", "abertas", "abrem",
    "candidatur", "registo", "programa",
]

FETCH_RETRIES = [(0, 2, 4)]  # delay in seconds before each attempt
WEEKLY_REMINDER_HOURS = 7 * 24

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "alerted_2026": False,
        "last_content_hash": "",
        "last_check": "",
        "last_error_alert": "",
        "last_reminder_alert": "",
        "last_sunday_alert": "",
        "content_change_count": 0,
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file, then rename to avoid corruption on crash
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN ou CHAT_ID não configurados.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Mensagem Telegram enviada com sucesso.")
        return True
    except requests.HTTPError as exc:
        # Log status code only — never log the URL (contains the bot token)
        log.error("Erro HTTP ao enviar mensagem Telegram: %s", exc.response.status_code)
        return False
    except requests.RequestException as exc:
        log.error("Erro ao enviar mensagem Telegram: %s", type(exc).__name__)
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hours_since(iso_timestamp: str) -> float | None:
    """Returns hours elapsed since iso_timestamp, or None if timestamp is empty/invalid."""
    if not iso_timestamp:
        return None
    try:
        past = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - past
        return delta.total_seconds() / 3600
    except ValueError:
        return None


# ── Page fetch & analysis ─────────────────────────────────────────────────────

def fetch_page_text() -> str:
    """Fetch page text with up to 3 attempts and exponential backoff."""
    delays = [0, 2, 4]
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "head"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True).lower()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < len(delays):
                log.warning("Tentativa %d falhou (%s), a tentar novamente...", attempt, type(exc).__name__)
    raise last_exc


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def detect_2026_registrations(text: str) -> bool:
    """True se a página mencionar inscrições abertas para 2026."""
    if TARGET_YEAR not in text:
        return False
    idx = text.find(TARGET_YEAR)
    while idx != -1:
        window = text[max(0, idx - 200) : idx + 200]
        if any(kw in window for kw in REGISTRATION_KEYWORDS):
            return True
        idx = text.find(TARGET_YEAR, idx + 1)
    return False


# ── Check logic ───────────────────────────────────────────────────────────────

def run_check() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("A verificar %s ...", TARGET_URL)

    state = load_state()

    try:
        text = fetch_page_text()
    except requests.RequestException as exc:
        log.error("Erro ao obter página após 3 tentativas: %s", type(exc).__name__)
        # Rate-limit error alerts to once every 24h to avoid Telegram spam
        hours_since = _hours_since(state.get("last_error_alert", ""))
        if hours_since is None or hours_since >= 24:
            send_telegram(
                f"⚠️ <b>Erro de monitorização</b>\n"
                f"Não foi possível aceder a <a href='{TARGET_URL}'>cienciaviva.pt</a> após 3 tentativas.\n"
                f"Tipo de erro: {type(exc).__name__}"
            )
            state["last_error_alert"] = now
            save_state(state)
        return

    current_hash = content_hash(text)
    registrations_open = detect_2026_registrations(text)

    # Primary detection: 2026 registrations explicitly mentioned
    if registrations_open and not state["alerted_2026"]:
        log.info("DETETADO: inscrições 2026 abertas!")
        sent = send_telegram(
            f"🎉 <b>Inscrições 2026 abertas!</b>\n\n"
            f"A página Ciência Viva no Laboratório foi atualizada e menciona inscrições para 2026.\n\n"
            f"👉 <a href='{TARGET_URL}'>Acede aqui para te inscrever</a>"
        )
        if sent:
            state["alerted_2026"] = True
            state["last_reminder_alert"] = now

    # Secondary detection: content changed, debounced — only alert after 2 consecutive
    # differing hashes to filter out transient dynamic content (timestamps, counters, etc.)
    elif current_hash != state["last_content_hash"] and state["last_content_hash"]:
        change_count = state.get("content_change_count", 0) + 1
        state["content_change_count"] = change_count
        log.info("Página alterada (hash mudou, count=%d).", change_count)
        if change_count >= 2:
            send_telegram(
                f"🔔 <b>Página Ciência Viva alterada</b>\n\n"
                f"O conteúdo da página foi modificado mas as inscrições 2026 ainda não foram detetadas automaticamente.\n\n"
                f"👉 <a href='{TARGET_URL}'>Verifica manualmente</a>"
            )
            state["content_change_count"] = 0
    else:
        state["content_change_count"] = 0
        log.info("Sem alterações. alerted_2026=%s", state["alerted_2026"])

    # Weekly reminder while registrations are open and user hasn't been reminded recently
    if state["alerted_2026"]:
        hours_since = _hours_since(state.get("last_reminder_alert", ""))
        if hours_since is None or hours_since >= WEEKLY_REMINDER_HOURS:
            log.info("A enviar lembrete semanal.")
            sent = send_telegram(
                f"🔔 <b>Lembrete — Inscrições 2026 abertas</b>\n\n"
                f"As inscrições para Ciência Viva no Laboratório 2026 ainda estão abertas.\n\n"
                f"👉 <a href='{TARGET_URL}'>Inscreve-te aqui</a>\n\n"
                f"<i>Para parar estes lembretes, faz reset ao monitor após te inscreveres.</i>"
            )
            if sent:
                state["last_reminder_alert"] = now

    # Sunday status report: "ainda não abertas" (only if registrations not open yet)
    # Uses local time (TZ=Europe/Lisbon) so Sunday is correct for PT
    today_local = datetime.now().strftime("%Y-%m-%d")
    is_sunday = datetime.now().weekday() == 6
    if is_sunday and not state["alerted_2026"] and state.get("last_sunday_alert") != today_local:
        log.info("Domingo — a enviar status 'ainda não abertas'.")
        sent = send_telegram(
            f"📅 <b>Ciência Viva 2026 — estado semanal</b>\n\n"
            f"As inscrições para Ciência Viva no Laboratório 2026 ainda não abriram.\n\n"
            f"👉 <a href='{TARGET_URL}'>Página oficial</a>"
        )
        if sent:
            state["last_sunday_alert"] = today_local

    state["last_content_hash"] = current_hash
    state["last_check"] = now
    save_state(state)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Clean shutdown on SIGTERM (docker stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    parser = argparse.ArgumentParser(description="Monitor Ciência Viva no Laboratório")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Envia mensagem de teste ao Telegram e sai.",
    )
    parser.add_argument(
        "--check-now",
        action="store_true",
        help="Faz uma verificação imediata e sai.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Limpa o estado de alerta (útil após falso positivo ou após inscrição).",
    )
    args = parser.parse_args()

    if args.test:
        log.info("Modo teste — a enviar mensagem de teste...")
        ok = send_telegram(
            f"✅ <b>Monitor Ciência Viva</b> — teste de conectividade\n\n"
            f"Bot configurado corretamente. Irás receber alertas quando as inscrições 2026 abrirem em:\n"
            f"👉 <a href='{TARGET_URL}'>cienciaviva.pt/ciencia-viva-no-laboratorio</a>"
        )
        sys.exit(0 if ok else 1)

    if args.reset:
        state = load_state()
        state["alerted_2026"] = False
        state["last_reminder_alert"] = ""
        state["content_change_count"] = 0
        save_state(state)
        log.info("Estado de alerta limpo. O monitor voltará a detetar inscrições 2026.")
        sys.exit(0)

    if args.check_now:
        run_check()
        sys.exit(0)

    # Validate CHECK_TIME format before scheduling
    try:
        schedule.every().day.at(CHECK_TIME).do(run_check)
    except schedule.ScheduleValueError as exc:
        log.error("CHECK_TIME inválido ('%s'): %s. Use formato HH:MM.", CHECK_TIME, exc)
        sys.exit(1)

    log.info("Monitor iniciado. Verificação diária às %s (hora Lisboa).", CHECK_TIME)
    # Run once on startup so we don't miss anything after a restart
    run_check()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()

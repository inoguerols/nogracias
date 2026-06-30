#!/usr/bin/env python3
"""Capa IA opcional y pluggable para filter.py.

Juzga por SENTIDO los correos que las reglas no marcaron (así caza fraseos nuevos
sin listas de términos). BYO (trae tu modelo): habla el formato estándar de OpenAI,
así que vale cualquier proveedor (Anthropic, OpenAI, Groq, OpenRouter…) o un modelo
LOCAL y gratis (Ollama / LM Studio). Sin SDKs: solo stdlib. Apagada si no hay clave.

Config por entorno (ver .env.example):
  LLM_API_KEY    tu clave. Vacío = IA apagada. Para Ollama local pon cualquier valor (p.ej. "ollama").
  LLM_API_BASE   por defecto la de Anthropic. Ollama: http://localhost:11434/v1
  LLM_MODEL      por defecto claude-haiku-4-5-20251001. Local: llama3.1, qwen2.5, etc.
  LLM_SEND_BODY  "1" (def) envía un extracto del cuerpo; "0" = solo asunto (más privado).
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request

DEFAULT_BASE = "https://api.anthropic.com/v1"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM = (
    "Eres un filtro de correo de una clínica dental. Decides si un email es una "
    "COMUNICACIÓN COMERCIAL NO SOLICITADA (cold sales): alguien que no es paciente, "
    "proveedor ni conocido intentando VENDER un servicio o producto — típicamente "
    "soluciones de IA, automatización, marketing digital, captación de pacientes, "
    "SEO/Google, software o agencias, con ganchos tipo 'agenda una llamada/demo'.\n"
    "Es LEGÍTIMO (NO marcar): pacientes (citas, dudas, presupuestos), proveedores y "
    "laboratorios (facturas, pedidos), banco y gestoría, administraciones públicas, "
    "colegios profesionales y formación a la que el destinatario está apuntado, "
    "boletines suscritos y el propio equipo. Newsletters de servicios ya contratados, "
    "pedidos y envíos (Amazon, AliExpress…) tampoco son cold sales.\n"
    "Ante la duda responde LEGIT (preferimos dejar pasar una venta antes que esconder "
    "un correo importante).\n"
    'Responde EXACTAMENTE una línea: "SPAM: <motivo breve>" o "LEGIT: <motivo breve>".'
)


def _env(name: str, default: str) -> str:
    """Como os.environ.get pero tratando "" (secret vacío en CI) como no definido."""
    return os.environ.get(name) or default


def enabled() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


def model_label() -> str:
    return _env("LLM_MODEL", DEFAULT_MODEL)


def _ssl_ctx():
    """TLS robusto: usa certifi si está instalado (arregla el Python de macOS que no
    encuentra el CA del sistema); si no, el contexto por defecto."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _parse_verdict(content: str):
    """Primera palabra SPAM/LEGIT; cualquier otra cosa -> LEGIT (fail-safe, no mover)."""
    text = (content or "").strip()
    head = text.upper().lstrip("\"'* -")
    reason = text.split(":", 1)[1].strip() if ":" in text else text
    return head.startswith("SPAM"), reason


def classify_email(subject: str, body: str, sender: str):
    """(is_spam, reason). Cualquier error -> (False, '') para no mover por un fallo de red."""
    if not enabled():
        return False, ""
    send_body = os.environ.get("LLM_SEND_BODY", "1") != "0"
    snippet = (body or "")[:1500] if send_body else "(omitido)"
    user = f"De: {sender}\nAsunto: {subject}\n\nCuerpo:\n{snippet}"
    payload = json.dumps({
        "model": model_label(),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "max_tokens": 120,
        "temperature": 0,
    }).encode("utf-8")
    base = _env("LLM_API_BASE", DEFAULT_BASE).rstrip("/")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            data = json.loads(r.read())
        return _parse_verdict(data["choices"][0]["message"]["content"])
    except Exception as e:  # red, auth, formato raro -> no mover (fail-safe)
        print(f"  (IA no disponible: {e}; se deja en bandeja)", file=sys.stderr)
        return False, ""


def _self_check():
    assert _parse_verdict("SPAM: oferta de IA no solicitada") == (True, "oferta de IA no solicitada")
    assert _parse_verdict("LEGIT: paciente pide cita")[0] is False
    assert _parse_verdict('"SPAM: agencia de marketing"')[0] is True
    assert _parse_verdict("legit, es la gestoria")[0] is False
    assert _parse_verdict("")[0] is False
    print("llm self-check OK")


if __name__ == "__main__":
    _self_check()

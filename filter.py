#!/usr/bin/env python3
"""nogracias — filtra correos de venta no solicitados (sobre todo "soluciones IA").

Conecta por IMAP, puntúa cada correo con reglas editables (rules.json) y mueve
los que superan el umbral a una carpeta aparte. Sin dependencias: solo stdlib.

Uso:
    python3 filter.py --self-check     # prueba el clasificador, NO toca el correo
    python3 filter.py                  # DRY-RUN: dice qué movería, no mueve nada
    python3 filter.py --apply          # mueve de verdad los correos marcados

Config por variables de entorno (ver .env.example): IMAP_HOST/USER/PASS/FOLDER.
"""
from __future__ import annotations

import argparse
import email
import email.message
import html
import imaplib
import json
import os
import re
import sys
import unicodedata
from email.header import decode_header, make_header
from pathlib import Path

import llm  # capa IA opcional (módulo hermano); apagada si no hay LLM_API_KEY

ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / "rules.json"


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #
def load_env(path: Path) -> None:
    """Mini cargador de .env (KEY=VALUE). ponytail: stdlib > añadir python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_rules(path) -> dict:
    """rules.json + (si existe) rules.local.json encima: allowlist/marca PERSONALES.
    rules.local.json está en .gitignore → tu config no se sube al repo público."""
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    local_path = ROOT / "rules.local.json"
    if local_path.exists():
        local = json.loads(local_path.read_text(encoding="utf-8"))
        for key in ("allowlist_domains", "allowlist_senders"):
            if local.get(key):
                rules[key] = sorted(set(rules.get(key, []) + local[key]))
        if local.get("brand"):
            for sig in rules.get("signals", []):
                if sig.get("name") == "brand":
                    sig["any"] = local["brand"]
        for key in ("threshold", "fake_reply_weight"):
            if key in local:
                rules[key] = local[key]
    return rules


def fold(s: str) -> str:
    """minúsculas + sin acentos: casa 'automatización' con 'automatizacion'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def matches(phrase: str, folded_text: str) -> bool:
    """Frase con espacio -> subcadena. Palabra suelta -> límite de palabra.

    El límite evita que 'ia' case dentro de 'gracias' o 'dia'; solo casa el
    token suelto 'IA' ('soluciones IA'), que es justo lo que queremos.
    """
    pf = fold(phrase)
    if " " in pf:
        return pf in folded_text
    return re.search(rf"\b{re.escape(pf)}\b", folded_text) is not None


_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    return html.unescape(s)


def decode_hdr(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw or ""


def get_body(msg: email.message.Message) -> str:
    """Texto del correo; prefiere text/plain, si solo hay HTML lo desnuda."""
    plain: list[str] = []
    htmltext: list[str] = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() != "text":
            continue
        if str(part.get("Content-Disposition", "")).startswith("attachment"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if part.get_content_subtype() == "plain" else htmltext).append(text)
    return "\n".join(plain) if plain else html_to_text("\n".join(htmltext))


# --------------------------------------------------------------------------- #
# Clasificador (núcleo)
# --------------------------------------------------------------------------- #
def classify(subject, body, sender, has_list_unsub, rules):
    """Devuelve (score, flagged, reasons). reasons = categorías que casaron."""
    sender_f = fold(sender)
    for dom in rules.get("allowlist_domains", []):
        if fold(dom) in sender_f:
            return 0.0, False, ["allowlist"]
    for snd in rules.get("allowlist_senders", []):
        if fold(snd) in sender_f:
            return 0.0, False, ["allowlist"]

    subject_f = fold(subject)
    all_f = subject_f + "\n" + fold(body)
    score = 0.0
    reasons: list[str] = []
    for sig in rules.get("signals", []):
        target = subject_f if sig.get("scope") == "subject" else all_f
        hits = [p for p in sig.get("any", []) if matches(p, target)]
        if hits:
            score += float(sig.get("weight", 1.0))
            reasons.append(f"{sig.get('name', '?')}({len(hits)})")

    # "Re:"/"Fwd:" sin hilo real = truco de cold-email para forzar la apertura.
    # Señal débil (solo suma en combinación): una respuesta legítima también lo lleva.
    if rules.get("fake_reply_weight") and re.match(r"\s*(re|rv|fwd|fw)\s*:", subject_f):
        score += float(rules["fake_reply_weight"])
        reasons.append("fake_reply")

    # Marketing masivo: bonus SOLO si el contenido ya es sospechoso (así un
    # boletín que sí quieres, con List-Unsubscribe pero sin señales, no cuela).
    if has_list_unsub and score >= float(rules.get("bulk_min_score", 1.0)):
        score += float(rules.get("bulk_bonus", 1.0))
        reasons.append("bulk")

    return score, score >= float(rules["threshold"]), reasons


# --------------------------------------------------------------------------- #
# IMAP
# --------------------------------------------------------------------------- #
def connect():
    host = os.environ["IMAP_HOST"]
    port = int(os.environ.get("IMAP_PORT", "993"))
    if os.environ.get("IMAP_SSL", "1") != "0":
        M = imaplib.IMAP4_SSL(host, port)
    else:
        M = imaplib.IMAP4(host, port)
    M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASS"])
    return M


def move(M, uid, folder, can_move):
    # ponytail: MOVE nativo si el server lo soporta (Dovecot/Plesk sí). Si no,
    # COPY + marcar \Deleted + EXPUNGE. Nunca se borra sin copia previa.
    if can_move:
        typ, _ = M.uid("MOVE", uid, folder)
        if typ == "OK":
            return
    M.uid("COPY", uid, folder)
    M.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    M.expunge()


def run(M, rules, apply, search, limit, verbose, use_llm=False):
    target = os.environ.get("IMAP_FOLDER", "INBOX.nogracias")
    M.select("INBOX")
    typ, data = M.uid("SEARCH", None, *search)
    if typ != "OK":
        print("Búsqueda IMAP falló:", data)
        return
    uids = data[0].split()
    if limit:
        uids = uids[-limit:]
    print(f"{len(uids)} correos a revisar ({'APLICAR' if apply else 'DRY-RUN'})"
          + (f" · capa IA: {llm.model_label()}" if use_llm else "") + "\n")

    if apply and uids:
        try:
            M.create(target)  # ignora error si ya existe
        except Exception:
            pass
    can_move = b"MOVE" in (M.capabilities or ())

    moved = 0
    for uid in uids:
        typ, msgdata = M.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: no marca leído
        if typ != "OK" or not msgdata or not isinstance(msgdata[0], tuple):
            continue
        msg = email.message_from_bytes(msgdata[0][1])
        subject = decode_hdr(msg.get("Subject", ""))
        sender = decode_hdr(msg.get("From", ""))
        body = get_body(msg)
        score, flagged, reasons = classify(
            subject, body, sender, bool(msg.get("List-Unsubscribe")), rules
        )
        # Capa IA: si las reglas NO lo marcaron y no es de la allowlist, que la IA
        # lo juzgue por sentido (caza fraseos nuevos). Fail-safe: ante error, no mueve.
        if use_llm and not flagged and "allowlist" not in reasons:
            is_spam, why = llm.classify_email(subject, body, sender)
            if is_spam:
                flagged = True
                reasons = reasons + [f"IA:{why[:45]}"]
        if flagged or verbose:
            mark = "FILTRA" if flagged else "  ok  "
            print(f"[{mark}] {score:4.1f}  {sender[:38]:38}  {subject[:48]:48}  {','.join(reasons)}")
        if flagged and apply:
            move(M, uid, target, can_move)
            moved += 1

    print()
    if apply:
        print(f"Movidos {moved} correos a «{target}».")
    else:
        print("DRY-RUN: no se movió nada. Lanza con --apply cuando estés conforme.")


# --------------------------------------------------------------------------- #
# Self-check (corre sin IMAP)
# --------------------------------------------------------------------------- #
SAMPLES = [
    ("Potencia tu clínica con Inteligencia Artificial",
     "Hola, vi tu clínica y me gustaría agendar una breve llamada de 15 minutos "
     "sin compromiso para mostrarte cómo nuestra IA automatiza la captación de pacientes.",
     "ventas@aigrowth.io", True),
    ("Demo de nuestro agente IA para tu negocio",
     "Our AI agent and chatbot can automate your customer service. "
     "Book a quick call, no strings attached.",
     "sales@coldoutreach.com", True),
    ("Transforma tu empresa con automatización y machine learning",
     "Generamos más leads con IA generativa. ¿Tienes unos minutos para una demo gratuita?",
     "growth@no-reply.marketing.com", True),
    ("Cita confirmada - Clínica Dental Ejemplo",
     "Le confirmamos su cita para la revisión el martes a las 10:00.",
     "recepcion@miclinica.example", False),
    ("Factura junio - material dental",
     "Adjuntamos la factura del pedido de material. Un saludo.",
     "facturacion@dentalsupplies.es", False),
    ("¿Comemos el viernes?",
     "Oye, que si te viene bien quedar el viernes para comer.",
     "amigo@gmail.com", False),
]


def self_check(rules) -> int:
    # Corpus en tests/samples.json (generado/ampliable con ejemplos reales);
    # si no existe, usa los SAMPLES de arriba como mínimo viable.
    samples_path = ROOT / "tests" / "samples.json"
    if samples_path.exists():
        cases = [(c["subject"], c.get("body", ""), c.get("from", ""), c["expect"])
                 for c in json.loads(samples_path.read_text(encoding="utf-8"))]
    else:
        cases = SAMPLES

    failures = 0
    for subject, body, sender, expect in cases:
        score, flagged, reasons = classify(subject, body, sender, False, rules)
        ok = flagged == expect
        if not ok:
            print(f"FAIL [{score:4.1f}] flag={flagged!s:5} esperado={expect!s:5} "
                  f":: {subject[:50]:50} :: {sender[:24]:24} :: {reasons}")
        failures += not ok
    total = len(cases)
    print(f"\n{total - failures}/{total} OK"
          + (f"  ({failures} fallos arriba)" if failures else "  — self-check OK"))
    assert failures == 0, f"{failures} casos fallan — ajusta rules.json"
    return 0


# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Filtra correos de venta IA no solicitados.")
    ap.add_argument("--apply", action="store_true", help="mover de verdad (por defecto: dry-run)")
    ap.add_argument("--self-check", action="store_true", help="probar el clasificador sin IMAP")
    ap.add_argument("--all", action="store_true", help="revisar TODO el buzón (no solo no leídos)")
    ap.add_argument("--since", metavar="DD-Mon-YYYY", help="solo correos desde esa fecha (ej. 01-Jun-2026)")
    ap.add_argument("--limit", type=int, default=0, help="revisar como mucho N correos")
    ap.add_argument("--verbose", action="store_true", help="mostrar también los que se quedan")
    ap.add_argument("--no-llm", action="store_true", help="no usar la capa IA aunque esté configurada")
    ap.add_argument("--rules", default=str(RULES_PATH), help="ruta a rules.json")
    args = ap.parse_args(argv)

    if args.self_check:  # valida el producto PÚBLICO (sin tu config local)
        return self_check(json.loads(Path(args.rules).read_text(encoding="utf-8")))

    rules = load_rules(args.rules)  # base + rules.local.json (tu allowlist/marca)

    load_env(ROOT / ".env")
    missing = [k for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASS") if not os.environ.get(k)]
    if missing:
        print("Faltan variables de entorno:", ", ".join(missing))
        print("Copia .env.example a .env y rellénalo (o usa --self-check).")
        return 2

    if args.since:
        search = ["SINCE", args.since]
    elif args.all:
        search = ["ALL"]
    else:
        search = ["UNSEEN"]  # ponytail: por defecto solo no leídos; --all para barrido completo

    use_llm = (not args.no_llm) and llm.enabled()
    M = connect()
    try:
        run(M, rules, args.apply, search, args.limit, args.verbose, use_llm)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

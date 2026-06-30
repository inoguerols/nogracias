#!/usr/bin/env python3
"""Genera los filtros nativos (Sieve y Gmail) desde rules.json.

Única fuente de verdad: rules.json. Edita ahí los términos y vuelve a correr:
    python3 build.py

Soportar un proveedor nuevo = añadir aquí un emisor (una función que devuelva texto).
Sin dependencias: solo stdlib.
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from filter import load_rules  # fusiona rules.local.json (tu allowlist) si existe

ROOT = Path(__file__).resolve().parent
RULES = load_rules(ROOT / "rules.json")
DIST = ROOT / "dist"
FOLDER = "IA-no-solicitado"

TERMS = RULES["native_subject_terms"]
ALLOW = RULES.get("allowlist_domains", [])


def sieve() -> str:
    quoted = ",\n".join(f'        "{t}"' for t in TERMS)
    allow_block = ""
    if ALLOW:
        doms = ", ".join(f'"{d}"' for d in ALLOW)
        allow_block = (
            "# 1) Allowlist: remitentes de confianza se quedan en la bandeja.\n"
            f'if address :domain :is "from" [{doms}] {{\n    stop;\n}}\n\n'
        )
    return (
        '# nogracias — filtro de correos de venta "IA" no solicitados.\n'
        "# GENERADO por build.py desde rules.json — no editar a mano (edita rules.json).\n"
        f'# Mueve los que casan a la carpeta "{FOLDER}". NUNCA borra.\n'
        '# Probado con Dovecot/Plesk/cPanel Roundcube. Si tu server no tiene la\n'
        '# extension "mailbox", crea la carpeta a mano y quita ":create".\n'
        'require ["fileinto", "mailbox"];\n\n'
        f"{allow_block}"
        "# 2) Si el ASUNTO contiene un termino IA-comercial -> a la carpeta.\n"
        'if header :contains "subject" [\n'
        f"{quoted}\n"
        "] {\n"
        f'    fileinto :create "{FOLDER}";\n'
        "    stop;\n"
        "}\n"
    )


def gmail() -> str:
    query = "subject:(" + " OR ".join(f'"{t}"' for t in TERMS) + ")"
    if ALLOW:
        query += " -from:(" + " OR ".join(ALLOW) + ")"
    val = escape(query, {'"': "&quot;", "'": "&apos;"})
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<feed xmlns='http://www.w3.org/2005/Atom' xmlns:apps='http://schemas.google.com/apps/2006'>\n"
        "  <title>nogracias — Mail Filters</title>\n"
        "  <entry>\n"
        "    <category term='filter'></category>\n"
        "    <title>nogracias: IA no solicitado</title>\n"
        "    <content></content>\n"
        f"    <apps:property name='hasTheWord' value='{val}'/>\n"
        f"    <apps:property name='label' value='{FOLDER}'/>\n"
        "    <apps:property name='shouldArchive' value='true'/>\n"
        "  </entry>\n"
        "</feed>\n"
    )


def main() -> int:
    DIST.mkdir(exist_ok=True)
    s, g = sieve(), gmail()
    (DIST / "nogracias.sieve").write_text(s, encoding="utf-8")
    (DIST / "gmail-filters.xml").write_text(g, encoding="utf-8")

    # self-check: sin drift — cada termino y cada allowlist debe acabar en ambos.
    for t in TERMS:
        assert f'"{t}"' in s and t in g, f"termino no propagado: {t}"
    for d in ALLOW:
        assert d in s and d in g, f"allowlist no propagada: {d}"
    print(f"OK -> dist/nogracias.sieve ({len(TERMS)} terminos), dist/gmail-filters.xml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

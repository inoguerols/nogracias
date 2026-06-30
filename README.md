# nogracias

**Filtra automáticamente esa avalancha de correos de venta que ofrecen "soluciones
IA" que nunca pediste.** Sin dependencias (solo Python de serie). Una sola lista de
términos, instalable en cualquier servicio de correo.

🌐 **Web explicativa:** https://inoguerols.github.io/nogracias

> **TL;DR (EN):** Open-source filter for unsolicited "AI solution" cold-sales emails.
> One keyword list (`rules.json`) → native filters for Sieve servers and Gmail, plus
> a universal IMAP runner (`filter.py`) for everything else. Moves/labels, never deletes.

Estos correos no son spam técnico (tienen SPF/DKIM bien, remitente real), así que el
antispam normal no los caza. `nogracias` los reconoce por **lo que dicen**: IA +
promesa comercial + "agenda una llamada de 15 min sin compromiso".

**Seguro por diseño: mueve o etiqueta, nunca borra.** Acaban en una carpeta
`IA-no-solicitado` que revisas cuando quieras.

---

## Instalación según tu servicio de correo

Lo importante es **dónde se entrega tu correo** (tu proveedor), no qué app usas para
leerlo. Si no lo sabes, mira el registro MX de tu dominio o pregunta a tu hosting.

| Tu correo está en… | Cómo instalarlo | Archivo |
|---|---|---|
| **Servidor con Sieve** (Dovecot/Cyrus: Plesk, cPanel, Fastmail, mailbox.org, Migadu, Mailcow, self-hosted) | Pegar en *Webmail → Filtros* | [`dist/nogracias.sieve`](dist/nogracias.sieve) |
| **Gmail / Google Workspace** | *Ajustes → Filtros → Importar filtros* | [`dist/gmail-filters.xml`](dist/gmail-filters.xml) |
| **Outlook / Microsoft 365** | Sin importador limpio → usa `filter.py` por IMAP (abajo) | `filter.py` |
| **Cualquier otro** / no quieres tocar el servidor | `filter.py` por IMAP (cron, GitHub Actions o Docker) | `filter.py` |

### A) Servidor con Sieve (lo más común en hosting con webmail)

1. Entra en tu **webmail** (Roundcube/Plesk/cPanel).
2. *Configuración → Filtros* → *Acciones* → **Editar conjunto de filtros** (modo
   texto / "Edit filter set").
3. Pega el contenido de `dist/nogracias.sieve` y guarda.
4. Si tu panel **no** deja pegar Sieve crudo, créalo en la GUI de filtros con esta
   receta: *Si el **Asunto** contiene cualquiera de los términos de
   `native_subject_terms` (en `rules.json`) → **mover a la carpeta**
   `IA-no-solicitado`.* Añade arriba la regla *Si el **De** es de tus dominios de
   confianza → parar* (allowlist).

### B) Gmail / Google Workspace

1. *Ajustes* (rueda dentada) → **Ver toda la configuración** → pestaña **Filtros y
   direcciones bloqueadas**.
2. Abajo: **Importar filtros** → sube `dist/gmail-filters.xml` → **Abrir archivo** →
   **Crear filtros**.
3. Crea una etiqueta `IA-no-solicitado` y los correos saltarán la Bandeja de entrada
   (no se borran; quedan etiquetados).

### C) Outlook / Microsoft 365 o cualquier IMAP — `filter.py`

Funciona contra **cualquier** buzón IMAP. Por defecto hace **dry-run** (te dice qué
movería sin tocar nada).

```bash
cp .env.example .env      # rellena IMAP_HOST/USER/PASS/FOLDER
python3 filter.py --verbose      # DRY-RUN: ves qué cazaría y por qué
python3 filter.py --apply        # mueve de verdad a la carpeta
```

Para que corra solo cada hora sin servidor propio: sube el repo a GitHub, añade
`IMAP_HOST/PORT/USER/PASS/FOLDER` como *Secrets* y ya está
(`.github/workflows/filter.yml`).

---

## Capa IA opcional (trae tu modelo)

Las reglas van un paso por detrás de cada fraseo nuevo. La capa IA (`llm.py`) cierra
ese hueco: **juzga por sentido** los correos que las reglas no marcaron, así caza
ganchos que nunca se han visto. Es **opcional y apagada por defecto** — sin clave,
`filter.py` funciona solo con reglas.

**Cómo encaja (mínimo coste):** allowlist → reglas (gratis) marcan lo obvio → la IA
solo mira lo que quedó sin marcar. Mueve/etiqueta, **nunca borra**, y guarda el motivo
que da la IA. Solo vive en `filter.py` (modo IMAP/cron); el filtro Sieve del webmail
no puede llamar a una IA.

**Trae tu modelo (BYO).** `llm.py` habla el formato estándar de OpenAI con stdlib (sin
SDKs, sigue sin dependencias), así que vale cualquier proveedor — o uno local:

| Cómo | Config (`.env`) | Coste | Privacidad |
|---|---|---|---|
| **Nube** (Anthropic/OpenAI/Groq/OpenRouter) | `LLM_API_KEY=...` | Céntimos | El correo va a tu proveedor |
| **Local** (Ollama/LM Studio) | `LLM_API_KEY=ollama`, `LLM_API_BASE=http://localhost:11434/v1`, `LLM_MODEL=llama3.1` | **0 €** | **No sale de tu máquina** |
| **Nada** | (vacío) | Gratis | Solo reglas |

```bash
python3 llm.py            # self-check del módulo (sin red)
python3 filter.py --verbose      # dry-run; si hay clave, la IA juzga los no marcados
python3 filter.py --no-llm       # forzar solo reglas
```

El repo aporta el *cerebro* (arquitectura de 2 capas + el prompt clasificador, en
`llm.py`, mejorable por PRs); el modelo lo pone cada quien.

## Previsualizar antes de activar (recomendado)

Aunque vayas a instalar el filtro nativo, puedes ver primero qué cazaría sin mover
nada, con `filter.py --verbose` (dry-run). Y sin tocar el correo siquiera:

```bash
python3 filter.py --self-check   # prueba el clasificador con ejemplos
```

## Afinar

Todo se edita en **`rules.json`** (la única fuente de verdad):

- `allowlist_domains` — dominios que **nunca** se filtran (tus proveedores, banco…).
- `signals` — categorías de términos con peso para `filter.py` (mira asunto **y**
  cuerpo). Cubre IA/automatización, captación, competencia, reactivación, reseñas,
  agenda/no-shows, fichajes, SEO/Google, marketing/Meta, loss-aversion, ganchos de
  pregunta, falsa referencia, seguimiento, demo/CTA, caso de éxito y aperturas
  formales. **Ninguna categoría suma el umbral sola: hace falta combinar ≥2 señales**
  (así un correo legítimo que roce una palabra no cae).
- `brand` — **tu nombre/marca y el de los socios**. Los cold-emails personalizan el
  asunto con tu nombre; cámbialo por el tuyo si reutilizas esto.
- `native_subject_terms` — términos de alta precisión para los filtros nativos
  (solo asunto; por eso conservadores).
- `threshold` / `fake_reply_weight` — umbral y peso del truco `Re:`/`Fwd:` falso.

Tras editar, regenera los filtros nativos:

```bash
python3 build.py    # reescribe dist/nogracias.sieve y dist/gmail-filters.xml
```

### Enséñale ejemplos reales

Cuando te llegue un correo que se cuela (o uno legítimo mal marcado), añádelo a
**`tests/samples.json`** (`{"subject","body","from","expect": true|false}`) y corre
`python3 filter.py --self-check`. Es la red de seguridad: ahora mismo trae **74
casos reales** (cold-sales que deben caer + correo legítimo de pacientes,
proveedores, banco, gestoría y Colegio que **no** debe caer). Si tocas pesos y
rompes algo, el self-check te lo dice antes de desplegar.

La primera semana revisa la carpeta `IA-no-solicitado`: si se cuela algo bueno, añade
su dominio a `allowlist_domains`; si se escapa venta, añade el término. Como nunca
borra, el riesgo es cero.

## Cómo funciona (resumen)

- Plega acentos (`automatización` = `automatizacion`) y casa por **límite de palabra**,
  para que `ia` no se confunda dentro de `gracias` o `día`.
- `filter.py` suma pesos de varias categorías (IA, apps-IA, promesa comercial,
  cold-outreach, demo); marca si supera `threshold`. La allowlist gana siempre.
- Bonus para marketing masivo (cabecera `List-Unsubscribe`) **solo** si el contenido
  ya es sospechoso → un boletín que sí quieres no cuela.

## Limitaciones

- **El límite real de las reglas:** cada cold-sales nuevo inventa un fraseo distinto
  ("¿Quién revisa los fichajes?", "Deja de perder leads en Meta", "Attn. Carlos
  Ejemplo"…). Las reglas cubren los arquetipos conocidos y se afinan con
  `tests/samples.json`, pero siempre van un paso por detrás del fraseo más creativo.
  Para cazar **todo** sin perseguir palabras, el siguiente escalón es una **fase LLM
  opcional** (un modelo barato que juzga "¿esto es venta no solicitada? sí/no", con
  las reglas como prefiltro): generaliza al tono comercial sin listas de términos. No
  incluida en v1 (para mantenerlo gratis y sin dependencias); fácil de añadir.
- Los filtros nativos miran solo el **asunto** (para no depender de extensiones que no
  todos los servidores tienen). `filter.py` mira asunto **y** cuerpo, así que caza más.

MIT. PRs bienvenidos: para soportar otro proveedor, añade un emisor en `build.py`.

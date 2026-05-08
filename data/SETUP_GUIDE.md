# Guía de Configuración — Pipeline de Entrenamiento
## Garmin → Intervals.icu → Google Drive → Claude AI Coach

**Versión:** 1.2 | **Fecha:** 2026-05-06
**Tiempo estimado de configuración:** 2–3 horas
**Nivel técnico requerido:** Intermedio (terminal básico, editar archivos de texto)

---

## ¿Qué hace este sistema?

```
Garmin Watch
    ↓  (sync automático)
Intervals.icu
    ↓  (script Python, 4x/día)
JSON locales en Mac
    ↓  (Google Drive Desktop)
Google Drive (nube)
    ↓  (MCP server o conector)
Claude AI → análisis de entrenamiento
```

El resultado: Claude lee tus datos de entrenamiento en tiempo real y actúa como coach de resistencia usando el protocolo Section 11 (evidence-based endurance coaching).

---

## Requisitos previos

### Cuentas necesarias
- [ ] **Garmin Connect** — gratuito (con cualquier reloj Garmin con GPS + HRM)
- [ ] **Intervals.icu** — gratuito para uso básico (tier gratuito es suficiente)
- [ ] **Google Drive** — 15 GB gratuito es suficiente
- [ ] **Claude Pro** — $20/mes (obligatorio — el tier gratuito no incluye Claude Code ni MCP)
- [ ] **GitHub** — gratuito (para clonar Section 11)

### Software en tu Mac
- macOS 12+ (Monterey o superior recomendado)
- Python 3.11+ (`python3 --version` para verificar)
- Git (`git --version`)
- Google Drive Desktop instalado y sincronizando

### Credenciales que necesitarás tener a mano
- API Key de Intervals.icu (Settings → API → Developer Settings)
- Tu Athlete ID de Intervals.icu (visible en la URL: `intervals.icu/athlete/XXXXXXX/...`)

---

## PASO 1 — Garmin → Intervals.icu

Esta integración es nativa y la más simple.

1. Ve a **Intervals.icu → Settings → Connections**
2. Conecta tu cuenta de **Garmin Connect**
3. Activa:
   - ✅ Sync activities
   - ✅ Upload planned workouts (necesario para que los planes aparezcan en Garmin)
4. Sincroniza manualmente la primera vez — las actividades históricas se importan automáticamente

**Verificación:** Tus actividades de Garmin deben aparecer en Intervals.icu en 5–10 minutos.

---

## PASO 2 — Estructura de carpetas

Crea esta estructura en tu Mac:

```bash
mkdir -p ~/training-data/logs
mkdir -p ~/Google\ Drive/My\ Drive/training-data
```

Resultado:
```
~/training-data/           ← carpeta de trabajo (NO sincronizada a Drive)
├── .venv/                 ← entorno Python (se crea en el siguiente paso)
├── .env                   ← credenciales (NUNCA en Drive)
├── logs/
│   ├── sync.out
│   └── sync.err
└── sync_wrapper.sh

~/Google Drive/My Drive/training-data/   ← carpeta de Drive (sincronizada)
├── latest.json
├── history.json
├── intervals.json
├── routes.json
├── ftp_history.json
└── DOSSIER.md
```

---

## PASO 3 — Instalar Section 11 (sync script)

Section 11 es el protocolo de coaching. Incluye el script que extrae datos de Intervals.icu.

```bash
cd ~/training-data

# Clonar el repositorio
git clone https://github.com/CrankAddict/section-11.git section11

# Crear entorno virtual
python3 -m venv .venv

# Activar e instalar dependencias
source .venv/bin/activate
pip install -r section11/requirements.txt
```

---

## PASO 4 — Credenciales (.env)

```bash
# Crear archivo de credenciales
nano ~/training-data/.env
```

Contenido del archivo:
```bash
INTERVALS_API_KEY=tu_api_key_aqui
INTERVALS_ATHLETE_ID=tu_athlete_id_aqui
DRIVE_OUTPUT_DIR=/Users/TU_USUARIO/Google Drive/My Drive/training-data
```

Guardar (Ctrl+X, Y, Enter) y asegurar permisos:

```bash
chmod 600 ~/training-data/.env
```

**¿Dónde encontrar tu API Key?**
Intervals.icu → Settings → Developer → API Key

**¿Dónde encontrar tu Athlete ID?**
En la URL cuando estás logueado: `https://intervals.icu/athlete/iXXXXXX/...` — el número es tu ID.

---

## PASO 5 — Sync wrapper script

```bash
nano ~/training-data/sync_wrapper.sh
```

Contenido:
```bash
#!/bin/bash
set -a
source ~/training-data/.env
set +a

LOGFILE=~/training-data/logs/sync.out
ERRFILE=~/training-data/logs/sync.err

echo "[$(date)] Starting sync..." >> "$LOGFILE"
~/training-data/.venv/bin/python ~/training-data/section11/examples/sync.py \
    >> "$LOGFILE" 2>> "$ERRFILE"
echo "[$(date)] Done." >> "$LOGFILE"
```

```bash
chmod +x ~/training-data/sync_wrapper.sh
```

**Prueba manual:**
```bash
bash ~/training-data/sync_wrapper.sh
```

Después de ~30 segundos, verifica que los archivos JSON aparecieron en Google Drive:
```bash
ls ~/Google\ Drive/My\ Drive/training-data/
# Debe mostrar: latest.json  history.json  intervals.json  routes.json  ftp_history.json
```

---

## PASO 6 — Automatización con launchd (Mac)

Para que el sync corra automáticamente 4 veces al día:

```bash
nano ~/Library/LaunchAgents/com.user.intervals-sync.plist
```

Contenido (reemplaza `TU_USUARIO` con tu nombre de usuario de Mac):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.intervals-sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/TU_USUARIO/training-data/sync_wrapper.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/TU_USUARIO/training-data/logs/sync.out</string>
    <key>StandardErrorPath</key>
    <string>/Users/TU_USUARIO/training-data/logs/sync.err</string>
</dict>
</plist>
```

Activar:
```bash
launchctl load -w ~/Library/LaunchAgents/com.user.intervals-sync.plist
```

Verificar:
```bash
launchctl list | grep intervals-sync
# Debe mostrar una línea con el nombre del agente
```

---

## PASO 7 — Claude Code + Section 11 skill

### Instalar Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Si no tienes npm:
```bash
# Instalar Node.js desde https://nodejs.org (LTS)
# O con Homebrew:
brew install node
```

### Configurar Claude Pro

1. Inicia sesión en claude.ai con tu cuenta Pro
2. Ejecuta `claude` en terminal — te pedirá autenticación la primera vez

### Instalar el skill Section 11

```bash
# Crear carpeta de skills
mkdir -p ~/.claude/commands/section-11/references

# Descargar el protocolo
curl -o ~/.claude/commands/section-11/SECTION_11.md \
  https://raw.githubusercontent.com/CrankAddict/section-11/main/SECTION_11.md

# Descargar DOSSIER template
curl -o ~/.claude/commands/section-11/DOSSIER_TEMPLATE.md \
  https://raw.githubusercontent.com/CrankAddict/section-11/main/DOSSIER_TEMPLATE.md

# Descargar workout reference
curl -o ~/.claude/commands/section-11/references/WORKOUT_REFERENCE.md \
  https://raw.githubusercontent.com/CrankAddict/section-11/main/references/WORKOUT_REFERENCE.md
```

Crear el archivo de skill `~/.claude/commands/section-11.md` con el siguiente encabezado mínimo:

```bash
nano ~/.claude/commands/section-11.md
```

Contenido base:
```markdown
# Section 11 — AI Endurance Coach

Read training data from:
~/Google Drive/My Drive/training-data/

Then read SECTION_11.md from ~/.claude/commands/section-11/SECTION_11.md
and DOSSIER.md from ~/Google Drive/My Drive/training-data/DOSSIER.md

Apply the Section 11 protocol strictly for all coaching analysis.
```

---

## PASO 8 — Google Drive MCP (opcional pero recomendado)

El MCP server permite que Claude Code lea tus archivos de Drive directamente desde el terminal. Sin esto, Claude Code aún puede leer los archivos locales (están en `~/Google Drive/...`), pero el MCP lo hace más robusto.

### Configurar el MCP de Google Drive

Agrega esto a tu configuración de Claude Code (`~/.claude.json` o Settings → MCP):

```json
{
  "mcpServers": {
    "google-drive": {
      "command": "node",
      "args": ["/ruta/al/google-drive-mcp/index.js"],
      "env": {
        "GOOGLE_DRIVE_CREDENTIALS": "~/.config/google-drive-mcp/credentials.json"
      }
    }
  }
}
```

> **Nota:** La configuración exacta del MCP depende del cliente MCP que uses. El más simple para empezar es omitir el MCP y dejar que Claude Code lea los archivos directamente desde la carpeta local de Google Drive.

---

## PASO 9 — DOSSIER.md (tu perfil de atleta)

Este es el archivo más importante para personalizar el coaching. Sin él, Claude no tiene contexto sobre ti.

```bash
cp ~/.claude/commands/section-11/DOSSIER_TEMPLATE.md \
   ~/Google\ Drive/My\ Drive/training-data/DOSSIER.md
```

Abre el archivo y completa **al menos**:
- Nombre, edad, peso, ubicación
- Reloj Garmin + configuración HRM
- Zonas de FC (del modelo de umbral láctico de Garmin)
- LTHR y FC máxima
- Historial de carreras y PRs
- Objetivo principal (carrera, fecha, tiempo meta)
- Ambiente (ciudad, temperatura, terreno)
- Cualquier condición especial (lesiones, insomnio, load oculto)

---

## PASO 10 — Verificación final

```bash
# 1. Sync manual
bash ~/training-data/sync_wrapper.sh

# 2. Verificar archivos en Drive
ls ~/Google\ Drive/My\ Drive/training-data/
# latest.json  history.json  intervals.json  routes.json  ftp_history.json  DOSSIER.md

# 3. Abrir Claude Code
claude

# 4. Invocar el coach
/section-11
```

Claude debe:
1. Leer los JSON automáticamente
2. Cargar el protocolo Section 11
3. Leer tu DOSSIER
4. Responder con análisis basado en tus datos reales

---

## Comandos de referencia rápida

```bash
# Sync manual (antes de una sesión de coaching)
bash ~/training-data/sync_wrapper.sh

# Ver logs en tiempo real
tail -f ~/training-data/logs/sync.out

# Ver errores
tail -f ~/training-data/logs/sync.err

# Estado del agente launchd
launchctl list | grep intervals-sync

# Reiniciar agente (si cambias el .plist)
launchctl unload ~/Library/LaunchAgents/com.user.intervals-sync.plist
launchctl load -w ~/Library/LaunchAgents/com.user.intervals-sync.plist
```

---

## Preguntas frecuentes

**¿Funciona en Windows o Linux?**
El sync script (Python) funciona en cualquier plataforma. El launchd (automatización) es específico de macOS — en Linux usa `cron`, en Windows usa el Programador de tareas. Claude Code funciona en los tres.

**¿Necesito Runna también?**
No. Runna es opcional. Claude puede generar el plan de entrenamiento completo basado en tu perfil y métricas de Intervals.icu, y subirlo directamente a Intervals.icu (y desde ahí a Garmin Connect).

**¿Los datos de salud son privados?**
Sí. Los JSON van de Intervals.icu a tu Mac local y a tu propio Google Drive. No pasan por servidores de terceros salvo el API de Claude para el análisis. Intervals.icu ya tiene tu data — esto solo la hace legible para Claude.

**¿Cuánto cuesta en total?**
| Servicio | Costo |
|----------|-------|
| Garmin Connect | Gratuito |
| Intervals.icu | Gratuito (tier básico suficiente) |
| Google Drive | Gratuito (15 GB) |
| Claude Pro | $20/mes |
| **Total** | **$20/mes** |

**¿El sync consume muchos recursos?**
No. Corre 4 veces al día, dura ~20–30 segundos, y usa ~50 MB de RAM durante la ejecución.

**¿Qué métricas de running dynamics están disponibles para Claude?**
Depende de lo que expone el API de Intervals.icu — no de lo que tu reloj registra. Aunque relojes como el Forerunner 265 graben vertical oscillation, ground contact time y vertical ratio en el archivo .fit, **Intervals.icu no expone esos campos en su API**. Lo que sí está disponible:

| Métrica | Disponible | Notas |
|---------|-----------|-------|
| Cadencia promedio | ✅ | steps/min (ambos pies — igual que Garmin Connect) |
| Longitud de zancada | ✅ | metros |
| Grade Adjusted Pace | ✅ | pace equivalente en terreno plano — útil en rutas con desnivel |
| Balance L/R | ✅ | requiere HRM-Pro o pod de running dynamics |
| TRIMP | ✅ | carga de entrenamiento basada en HR |
| Vertical oscillation | ❌ | en .fit file de Garmin pero no en API de Intervals.icu |
| Ground contact time | ❌ | ídem |
| Vertical ratio | ❌ | ídem |

Si en el futuro necesitas esas métricas, la única alternativa sería leer directamente el archivo .fit de Garmin (requiere librería adicional como `fitparse`).

**Nota sobre cadencia:** Intervals.icu devuelve la cadencia en **strides/min** (zancadas — un solo pie), mientras que Garmin Connect la muestra en **steps/min** (pasos — ambos pies). Son la misma métrica: strides × 2 = steps. El script sync.py aplica esta conversión automáticamente, por lo que el campo `avg_cadence_spm` en el JSON siempre refleja el valor que verías en Garmin (ej: 163 spm, no 81).

---

## Soporte

- **Section 11 protocol:** https://github.com/CrankAddict/section-11
- **Intervals.icu API docs:** https://intervals.icu/api-docs
- **Claude Code docs:** https://docs.anthropic.com/claude-code

---

*Guía creada con Claude Code usando el pipeline que describe.*

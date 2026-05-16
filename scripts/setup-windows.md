# Section 11 — Guía de Instalación en Windows
### Garmin → Intervals.icu → Google Drive → Claude Code

---

## Cómo usar este documento

Este archivo es DOS cosas al mismo tiempo:

**Para el usuario:** Seguí los pasos de la PARTE 1 (manual) antes de abrir Claude.

**Para Claude:** Una vez instalado Claude Code, pegá TODO el contenido de la PARTE 2
en el chat. Claude va a leer las instrucciones y hacer el resto automáticamente,
pidiéndote los datos que necesita.

---

## PARTE 1 — Pasos manuales (hacelos antes de abrir Claude)

### 1.1 Cuentas necesarias

- [ ] **Intervals.icu** — creá cuenta en https://intervals.icu (gratis)
  - Conectá tu Garmin: Settings → Connections → Garmin Connect → Authorize
  - Obtenemos tu API key: Settings → API Settings → copialá (la vas a necesitar)
  - Anotá tu **Athlete ID** — está en la URL de tu perfil (ej: `i123456`)

- [ ] **GitHub** — cuenta en https://github.com (gratis)
  - Creá un repositorio privado llamado `training`
  - Generá un token de acceso: Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token → permisos: `repo` completo → copialo

- [ ] **Google Drive for Desktop** — descargalo e instalalo: https://www.google.com/drive/download
  - Una vez instalado, verificá que existe la carpeta `Google Drive\My Drive` en tu explorador de archivos

### 1.2 Software a instalar

- [ ] **Git for Windows** — https://git-scm.com/download/win
  - Durante la instalación, elegí "Git from the command line and also from 3rd-party software"

- [ ] **Python 3.11+** — https://www.python.org/downloads/windows/
  - **IMPORTANTE:** Durante la instalación, marcá "Add Python to PATH"

- [ ] **Node.js (LTS)** — https://nodejs.org/en/download
  - Necesario para instalar Claude Code

- [ ] **Claude Code** — abrí PowerShell y ejecutá:
  ```
  npm install -g @anthropic-ai/claude-code
  ```
  Luego autenticalo con `claude` y seguí las instrucciones.

### 1.3 Datos que vas a necesitar tener a mano

Cuando abras Claude Code y pegues el prompt de la Parte 2, Claude te va a preguntar:

| Dato | Dónde encontrarlo |
|------|-------------------|
| `INTERVALS_ATHLETE_ID` | URL de tu perfil en Intervals.icu (ej: `i123456`) |
| `INTERVALS_API_KEY` | Settings → API Settings en Intervals.icu |
| GitHub username | Tu usuario de GitHub |
| GitHub repo URL | La URL HTTPS de tu repo `training` |
| GitHub token | El token que generaste en el paso 1.1 |
| Tu nombre / edad / peso / altura | Para el perfil de atleta |
| Tu LTHR y Max HR | Garmin los muestra en Settings → User Profile |
| Tu objetivo de carrera | Ej: Sub-1:50 HM, Granada, 26 julio 2026 |

---

## PARTE 2 — Prompt para Claude Code

> **Instrucciones:** Abrí Claude Code (`claude` en PowerShell o terminal),
> y pegá TODO el texto que está a continuación.

---

```
Voy a configurar el pipeline de entrenamiento Section 11 en Windows.
Este sistema conecta Garmin → Intervals.icu → Google Drive → GitHub → Claude.

Tu trabajo es guiarme paso a paso y ejecutar todo lo que puedas
automáticamente. Cuando necesites datos míos, pedímelos de a uno.

Seguí estos pasos en orden:

---

PASO 1 — RECOPILAR CREDENCIALES

Pedime los siguientes datos uno por uno:
1. Mi INTERVALS_ATHLETE_ID (ej: i123456)
2. Mi INTERVALS_API_KEY (desde Settings → API Settings en Intervals.icu)
3. Mi GitHub username
4. La URL HTTPS de mi repo `training` (ej: https://github.com/usuario/training)
5. Mi GitHub personal access token (permisos: repo)

Guardá estos datos en memoria para usarlos en los pasos siguientes.

---

PASO 2 — CREAR ESTRUCTURA DE DIRECTORIOS

Usando la terminal (PowerShell), creá los siguientes directorios si no existen:

  %USERPROFILE%\training-data\
  %USERPROFILE%\training-data\section11\examples\
  "%USERPROFILE%\Google Drive\My Drive\training-data\"
  %USERPROFILE%\personal\training\data\

---

PASO 3 — DESCARGAR sync.py

Descargá el script de sincronización oficial desde el repositorio Section 11:

  Destino: %USERPROFILE%\training-data\section11\examples\sync.py
  URL: https://raw.githubusercontent.com/CrankAddict/section-11/main/examples/sync.py

Si la URL falla, avisame para buscar la alternativa.

---

PASO 4 — CREAR ENTORNO PYTHON

En %USERPROFILE%\training-data\, ejecutá:

  python -m venv .venv
  .venv\Scripts\pip install requests

---

PASO 5 — CREAR ARCHIVO .env

Creá el archivo %USERPROFILE%\training-data\.env con el contenido:

  INTERVALS_ATHLETE_ID=<el que me diste>
  INTERVALS_API_KEY=<el que me diste>

Asegurate de que este archivo NO se suba a ningún repositorio.
Creá también %USERPROFILE%\training-data\.gitignore con:

  .env
  .venv/
  __pycache__/

---

PASO 6 — CREAR sync_wrapper.ps1

Creá el archivo %USERPROFILE%\training-data\sync_wrapper.ps1 con el siguiente contenido:

  $WORKING_DIR = "$env:USERPROFILE\training-data"
  $DRIVE_DIR = "$env:USERPROFILE\Google Drive\My Drive\training-data"
  $PYTHON = "$WORKING_DIR\.venv\Scripts\python.exe"
  $SYNC = "$WORKING_DIR\section11\examples\sync.py"
  $REPO_DIR = "$env:USERPROFILE\personal\training"

  # Cargar credenciales desde .env
  Get-Content "$WORKING_DIR\.env" | ForEach-Object {
      if ($_ -match '^([^#][^=\s]+)\s*=\s*(.+)$') {
          [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), 'Process')
      }
  }

  # Ejecutar sync desde carpeta de Drive
  Set-Location $DRIVE_DIR
  & $PYTHON $SYNC `
      --athlete-id $env:INTERVALS_ATHLETE_ID `
      --intervals-key $env:INTERVALS_API_KEY `
      --output "$DRIVE_DIR\latest.json" `
      --lockfile

  # Auto-push DOSSIER.md y TRAINING_PLAN.md a GitHub si cambiaron
  if (Test-Path $REPO_DIR) {
      $changed = $false
      foreach ($file in @("DOSSIER.md", "TRAINING_PLAN.md")) {
          $src = "$DRIVE_DIR\$file"
          $dst = "$REPO_DIR\data\$file"
          if ((Test-Path $src)) {
              $srcHash = (Get-FileHash $src).Hash
              $dstHash = if (Test-Path $dst) { (Get-FileHash $dst).Hash } else { "" }
              if ($srcHash -ne $dstHash) {
                  Copy-Item $src $dst -Force
                  $changed = $true
              }
          }
      }
      if ($changed) {
          Set-Location $REPO_DIR
          git add data\DOSSIER.md data\TRAINING_PLAN.md
          $date = Get-Date -Format "yyyy-MM-dd HH:mm"
          git commit -m "Auto-sync: DOSSIER/TRAINING_PLAN update $date"
          git push origin main
          Write-Host "GitHub: DOSSIER/TRAINING_PLAN pushed"
      }
  }

---

PASO 7 — CONFIGURAR REPOSITORIO GITHUB

En %USERPROFILE%\personal\training\, ejecutá:

  git init
  git remote add origin <URL del repo que me diste>
  git config credential.helper manager

Creá la carpeta data\ y los archivos iniciales vacíos:
  %USERPROFILE%\personal\training\data\DOSSIER.md
  %USERPROFILE%\personal\training\data\TRAINING_PLAN.md

Hacé el primer commit:
  git add .
  git commit -m "init: training repo"
  git push -u origin main

Si pide autenticación, el credential manager de Windows va a abrir una ventana del navegador.

---

PASO 8 — PROGRAMAR SYNC AUTOMÁTICO (Task Scheduler)

Ejecutá los siguientes comandos en PowerShell como Administrador para programar
el sync automático 5 veces al día (5:00, 8:00, 12:00, 18:00, 22:00):

  $script = "$env:USERPROFILE\training-data\sync_wrapper.ps1"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

  foreach ($hour in @(5, 8, 12, 18, 22)) {
      $time = "{0:D2}:00" -f $hour
      $trigger = New-ScheduledTaskTrigger -Daily -At $time
      Register-ScheduledTask `
          -TaskName "TrainingSync_$hour" `
          -Action $action `
          -Trigger $trigger `
          -Settings $settings `
          -RunLevel Highest `
          -Force
  }

---

PASO 9 — INSTALAR SKILL SECTION 11 EN CLAUDE CODE

Creá el directorio de skills:
  %USERPROFILE%\.claude\commands\section-11\
  %USERPROFILE%\.claude\commands\section-11\references\

Descargá los archivos del skill desde el repo oficial:
  https://raw.githubusercontent.com/CrankAddict/section-11/main/section-11.md
    → guardar como: %USERPROFILE%\.claude\commands\section-11\section-11.md
  https://raw.githubusercontent.com/CrankAddict/section-11/main/SECTION_11.md
    → guardar como: %USERPROFILE%\.claude\commands\section-11\SECTION_11.md
  https://raw.githubusercontent.com/CrankAddict/section-11/main/references/WORKOUT_REFERENCE.md
    → guardar como: %USERPROFILE%\.claude\commands\section-11\references\WORKOUT_REFERENCE.md

---

PASO 10 — EJECUTAR SYNC DE PRUEBA

Ejecutá manualmente el sync por primera vez:

  Set-Location "$env:USERPROFILE\training-data"
  .\.venv\Scripts\python.exe section11\examples\sync.py `
      --athlete-id <INTERVALS_ATHLETE_ID> `
      --intervals-key <INTERVALS_API_KEY> `
      --output "$env:USERPROFILE\Google Drive\My Drive\training-data\latest.json"

Si el archivo latest.json aparece en la carpeta de Drive → éxito.
Si hay error, mostrámelo para diagnosticar.

---

PASO 11 — CREAR DOSSIER.md

Pedime los siguientes datos del atleta para construir el DOSSIER:
1. Nombre
2. Edad
3. Peso actual (kg) y altura (cm)
4. Ciudad/país de entrenamiento
5. LTHR (umbral lactato en bpm) — está en Garmin: Perfil → Zonas de FC
6. Max HR
7. Objetivo A (distancia, tiempo, fecha, lugar)
8. Objetivo B contingencia (si existe)
9. Carrera reciente más relevante (fecha, distancia, tiempo)
10. ¿Tenés chest strap (HRM) o usás solo el reloj?
11. Apps que usás (Runna, Strava, etc.)
12. ¿Tenés insomnio crónico u otras condiciones que afecten el sueño?

Con esos datos voy a crear el DOSSIER.md completo y copiarlo a Drive y al repo.

---

PASO 12 — VERIFICACIÓN FINAL

Verificá que todo esté en su lugar:
- [ ] latest.json existe en Google Drive\My Drive\training-data\
- [ ] DOSSIER.md existe en Drive y en el repo
- [ ] El repo tiene commits en GitHub
- [ ] Task Scheduler tiene 5 tareas "TrainingSync_X"
- [ ] La carpeta .claude\commands\section-11\ tiene SECTION_11.md

Si todo está OK, decile al usuario que escriba `/section-11` en Claude Code
para hacer el primer chequeo de readiness con sus datos reales.

---

FIN DEL PROMPT DE CONFIGURACIÓN
```

---

## Notas adicionales

**¿Qué hace el sistema una vez configurado?**
- Cada día a las 5h, 8h, 12h, 18h y 22h, el script descarga tus métricas
  de Intervals.icu (HRV, CTL, ATL, TSB, actividades, zonas) y las guarda
  en Google Drive como `latest.json`
- Claude Code lee ese archivo cuando usás `/section-11` y te da análisis
  de readiness, post-workout, o lo que necesités, sin que vos tengás que
  exportar nada manualmente
- DOSSIER.md y TRAINING_PLAN.md se sincronizan automáticamente a GitHub

**Estructura final de carpetas:**
```
%USERPROFILE%\
├── training-data\
│   ├── .env                    ← credenciales (nunca en GitHub)
│   ├── .venv\                  ← Python virtual environment
│   ├── sync_wrapper.ps1        ← script de sync para Windows
│   └── section11\examples\
│       └── sync.py             ← script principal de Intervals.icu
│
├── Google Drive\My Drive\training-data\
│   ├── latest.json             ← datos frescos (actualización automática)
│   ├── history.json
│   ├── intervals.json
│   ├── DOSSIER.md              ← perfil del atleta (fuente de verdad)
│   └── TRAINING_PLAN.md        ← plan de entrenamiento
│
└── personal\training\
    └── data\
        ├── DOSSIER.md          ← mirror en GitHub
        └── TRAINING_PLAN.md    ← mirror en GitHub
```

**Soporte:**
Si algo falla durante la instalación, mandá el mensaje de error exacto a Claude
y él puede diagnosticar y corregir el problema en el momento.

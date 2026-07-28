# alx-whisper

Dictado por voz estilo Wispr Flow para Linux. Graba desde el micrófono, envía el audio a un endpoint OpenAI-compatible (puede ser tu propio VPS con Whisper) e inyecta el texto transcrito en cualquier campo activo vía portapapeles + Ctrl+V.

## Requisitos

- Python 3.10+
- `portaudio` (para `sounddevice`)
- `ydotool` + `ydotoold` corriendo (inyección de teclado en Wayland)
- `wl-copy` / `wl-paste` (portapapeles Wayland)
- Un endpoint OpenAI-compatible con `/v1/audio/transcriptions`

## Instalación

```bash
./install.sh
```

Esto:
1. Crea un venv en `~/.local/share/alx-whisper/.venv`
2. Instala las dependencias Python
3. Copia `.env.example` a `~/.config/alx-whisper/.env` (editalo con tu API key)
4. Instala y activa el servicio systemd `--user`
5. Configura el hotkey en GNOME

## Configuración

Editar `~/.config/alx-whisper/.env`:

```env
ALX_WHISPER_API_KEY=sk-tu-key
ALX_WHISPER_API_URL=https://tu-vps.com/v1/audio/transcriptions
ALX_WHISPER_MODEL=whisper-1
ALX_WHISPER_LANGUAGE=es
ALX_WHISPER_HOTKEY=f8
ALX_WHISPER_SAMPLE_RATE=16000
ALX_WHISPER_MAX_RECORD_SECONDS=120
ALX_WHISPER_RESTORE_CLIPBOARD_DELAY=2.0
```

## Uso

- Pulsá **F8** para empezar a grabar
- Pulsá **F8** de nuevo para detener
- El texto se pega automáticamente donde tengas el cursor

### Comandos manuales

```bash
alx-whisper.sh start    # iniciar daemon
alx-whisper.sh stop     # detener
alx-whisper.sh status   # ver estado
alx-whisper.sh logs     # ver logs en vivo
```

## Arquitectura

```
Hotkey (GNOME) → SIGUSR1 → Daemon Python
                               ├── sounddevice (grabación)
                               ├── POST /v1/audio/transcriptions (Whisper)
                               ├── wl-copy (texto al portapapeles)
                               └── ydotool key Ctrl+V (inyección)
```

## Licencia

MIT

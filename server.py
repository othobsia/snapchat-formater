#!/usr/bin/env python3
"""
Snapchat Bild-Formatierer Server

Ein Python-Server mit serverseitiger Bildverarbeitung mittels Pillow.
Schneidet Bilder auf 9:16 zu und skaliert auf 1080x1920 für Snapchat.

Verwendung:
    python server.py

Dann im Browser öffnen: http://localhost:8000

Abhängigkeiten:
    pip install Pillow
"""

import http.server
import socketserver
import webbrowser
import json
import base64
import io
from pathlib import Path
from urllib.parse import parse_qs

# Pillow für Bildverarbeitung
try:
    from PIL import Image
except ImportError:
    print("Fehler: Pillow nicht installiert!")
    print("Bitte installieren mit: pip install Pillow")
    exit(1)

# ===== Konfiguration =====
PORT = 8000
HOST = "localhost"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_RATIO = 9 / 16  # 0.5625
JPEG_QUALITY = 92

# Verzeichnis der HTML-Datei
DIRECTORY = Path(__file__).parent


def process_image(image_data: bytes, crop_mode: str = "center") -> tuple[bytes, dict]:
    """
    Verarbeitet ein Bild: Zuschnitt auf 9:16 und Skalierung auf 1080x1920.

    Args:
        image_data: Rohe Bilddaten als Bytes
        crop_mode: Zuschnitt-Modus ('center', 'top', 'bottom')

    Returns:
        Tuple aus (verarbeitete JPEG-Bytes, Info-Dictionary)
    """
    # Bild öffnen
    img = Image.open(io.BytesIO(image_data))

    # EXIF-Rotation korrigieren (falls vorhanden)
    try:
        from PIL import ExifTags
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break
        exif = img._getexif()
        if exif is not None:
            orientation_value = exif.get(orientation)
            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
    except (AttributeError, KeyError, IndexError):
        pass

    # In RGB konvertieren (falls RGBA oder anderes Format)
    if img.mode in ('RGBA', 'P', 'LA'):
        # Weißer Hintergrund für transparente Bilder
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # Original-Dimensionen speichern
    orig_width, orig_height = img.size
    img_ratio = orig_width / orig_height

    # Zuschnitt-Bereich berechnen
    if img_ratio > TARGET_RATIO:
        # Bild ist breiter als 9:16 → Seiten abschneiden
        crop_height = orig_height
        crop_width = int(orig_height * TARGET_RATIO)
        crop_y = 0
        crop_x = (orig_width - crop_width) // 2
    else:
        # Bild ist höher als 9:16 → Oben/unten abschneiden
        crop_width = orig_width
        crop_height = int(orig_width / TARGET_RATIO)
        crop_x = 0

        # Y-Position basierend auf Modus
        if crop_mode == "top":
            crop_y = 0
        elif crop_mode == "bottom":
            crop_y = orig_height - crop_height
        else:  # center
            crop_y = (orig_height - crop_height) // 2

    # Bild zuschneiden
    cropped = img.crop((crop_x, crop_y, crop_x + crop_width, crop_y + crop_height))

    # Auf Zielgröße skalieren (hochwertige Lanczos-Interpolation)
    result = cropped.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)

    # Als JPEG speichern
    output = io.BytesIO()
    result.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True)
    jpeg_data = output.getvalue()

    # Info zurückgeben
    info = {
        "original_width": orig_width,
        "original_height": orig_height,
        "crop_mode": crop_mode,
        "result_width": TARGET_WIDTH,
        "result_height": TARGET_HEIGHT,
        "file_size": len(jpeg_data)
    }

    return jpeg_data, info


class SnapchatFormatterHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP-Handler für den Snapchat-Formatierer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def do_POST(self):
        """Verarbeitet POST-Requests für Bildverarbeitung."""

        if self.path == "/api/process":
            self._handle_process()
        else:
            self.send_error(404, "Endpoint nicht gefunden")

    def _handle_process(self):
        """Verarbeitet ein hochgeladenes Bild."""

        try:
            # Content-Type prüfen
            content_type = self.headers.get('Content-Type', '')

            if 'multipart/form-data' in content_type:
                # Multipart-Daten parsen
                boundary = content_type.split('boundary=')[1].encode()
                content_length = int(self.headers['Content-Length'])
                body = self.rfile.read(content_length)

                # Einfaches Multipart-Parsing
                parts = body.split(b'--' + boundary)
                image_data = None
                crop_mode = "center"

                for part in parts:
                    if b'name="image"' in part:
                        # Bilddaten extrahieren (nach doppeltem CRLF)
                        data_start = part.find(b'\r\n\r\n') + 4
                        data_end = part.rfind(b'\r\n')
                        if data_start > 4 and data_end > data_start:
                            image_data = part[data_start:data_end]

                    elif b'name="mode"' in part:
                        data_start = part.find(b'\r\n\r\n') + 4
                        data_end = part.rfind(b'\r\n')
                        if data_start > 4 and data_end > data_start:
                            crop_mode = part[data_start:data_end].decode().strip()

                if image_data is None:
                    self._send_json_error("Kein Bild gefunden", 400)
                    return

            elif 'application/json' in content_type:
                # JSON mit Base64-Bild
                content_length = int(self.headers['Content-Length'])
                body = self.rfile.read(content_length)
                data = json.loads(body)

                # Base64-Daten extrahieren
                image_b64 = data.get('image', '')
                if ',' in image_b64:
                    image_b64 = image_b64.split(',')[1]

                image_data = base64.b64decode(image_b64)
                crop_mode = data.get('mode', 'center')

            else:
                self._send_json_error("Ungültiger Content-Type", 400)
                return

            # Bild verarbeiten
            result_data, info = process_image(image_data, crop_mode)

            # Ergebnis als Base64 zurückgeben
            result_b64 = base64.b64encode(result_data).decode()

            response = {
                "success": True,
                "image": f"data:image/jpeg;base64,{result_b64}",
                "info": info
            }

            self._send_json_response(response)

        except Exception as e:
            self._send_json_error(f"Fehler bei der Verarbeitung: {str(e)}", 500)

    def _send_json_response(self, data: dict, status: int = 200):
        """Sendet eine JSON-Antwort."""
        response = json.dumps(data).encode()

        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(response))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(response)

    def _send_json_error(self, message: str, status: int):
        """Sendet eine JSON-Fehlermeldung."""
        self._send_json_response({"success": False, "error": message}, status)

    def do_OPTIONS(self):
        """CORS Preflight-Handler."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def end_headers(self):
        """Fügt Cache-Control Header hinzu."""
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        """Formatierte Log-Ausgabe."""
        print(f"[{self.log_date_time_string()}] {args[0]}")


def main():
    """Startet den Server."""

    # Prüfen ob index.html existiert
    index_path = DIRECTORY / "index.html"
    if not index_path.exists():
        print(f"Fehler: {index_path} nicht gefunden!")
        return

    # Server starten
    with socketserver.TCPServer((HOST, PORT), SnapchatFormatterHandler) as httpd:
        url = f"http://{HOST}:{PORT}"

        print("=" * 50)
        print("  Snapchat Bild-Formatierer")
        print("  (Serverseitige Bildverarbeitung mit Pillow)")
        print("=" * 50)
        print(f"\n  Server läuft auf: {url}")
        print("  Drücke Strg+C zum Beenden\n")
        print("=" * 50)

        # Browser automatisch öffnen
        try:
            webbrowser.open(url)
        except Exception:
            print(f"\n  Browser konnte nicht geöffnet werden.")
            print(f"  Bitte manuell öffnen: {url}\n")

        # Server laufen lassen
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\nServer beendet.")


if __name__ == "__main__":
    main()

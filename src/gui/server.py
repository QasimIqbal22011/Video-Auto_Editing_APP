import os
import sys
import mimetypes
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO
import bottle
from bottle import Bottle, request, response, static_file, abort
from PIL import Image, ImageOps
from ..core.ffmpeg_utils import get_audio_info
from ..core.logger import log_client_event, log_info, log_error
from .media_access import MediaPathRegistry

# Bound multipart buffering. The frontend batches small files below this
# aggregate threshold; oversized individual files are spooled to temp storage.
bottle.BaseRequest.MEMFILE_MAX = 64 * 1024 * 1024

_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma'}
_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
_VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.mkv', '.avi'}
_THUMBNAIL_WORKERS = threading.BoundedSemaphore(4)


@lru_cache(maxsize=384)
def _cached_thumbnail(path: str, file_size: int, modified_ns: int, width: int):
    """Return a size-bounded preview; source metadata is part of the cache key."""
    del file_size, modified_ns  # Cache-key inputs invalidate previews after edits.
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        output = BytesIO()
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            mime = "image/png"
        else:
            image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
            mime = "image/jpeg"
        return output.getvalue(), mime


def _safe_upload_name(filename: str, allowed_extensions: set[str]) -> str:
    """Keep uploads inside their import directory and accept media only."""
    name = os.path.basename((filename or '').replace('\x00', ''))
    if not name or name in {'.', '..'}:
        raise ValueError("Invalid upload filename")
    if os.path.splitext(name)[1].lower() not in allowed_extensions:
        raise ValueError("Unsupported file type")
    return name


def _inspect_uploaded_audio(file_path: str):
    try:
        return get_audio_info(file_path)
    except Exception as exc:
        file_size = os.path.getsize(file_path)
        size_formatted = f"{file_size / (1024 * 1024):.2f} MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.1f} KB"
        extension = os.path.splitext(file_path)[1].lower()
        return {
            'path': file_path,
            'filename': os.path.basename(file_path),
            'duration': 0,
            'duration_formatted': '00:00',
            'sample_rate': 44100,
            'channels': 2,
            'bitrate_kbps': 0,
            'format_name': extension.replace('.', '').upper(),
            'size_bytes': file_size,
            'size_formatted': size_formatted,
            'error': str(exc)
        }

def create_app(web_dir: str, media_registry: MediaPathRegistry | None = None):
    app = Bottle()
    media_registry = media_registry or MediaPathRegistry()

    @app.route('/client_log', method=['POST', 'OPTIONS'])
    def client_log():
        response.content_type = 'application/json'
        response.set_header("Access-Control-Allow-Origin", "*")
        if request.method == 'OPTIONS':
            return "{}"
        try:
            data = request.json or {}
            level = data.get('level', 'info')
            msg = data.get('message', '')
            log_client_event(level, msg)
            return json.dumps({"status": "ok"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    # Directory for temporary drag-and-drop imported files
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    import_dir = os.path.join(base_dir, 'imported_audio')
    os.makedirs(import_dir, exist_ok=True)

    @app.route('/')
    def index():
        return static_file('index.html', root=web_dir)

    @app.route('/static/<filepath:path>')
    def serve_static(filepath):
        return static_file(filepath, root=web_dir)

    @app.route('/stream_audio')
    def stream_audio():
        file_path = request.query.get('path')
        if not file_path:
            abort(400, "Missing path parameter")
        
        file_path = media_registry.resolve(file_path, _AUDIO_EXTENSIONS)
        if not file_path:
            abort(404, "Audio file not found")

        directory, filename = os.path.split(os.path.abspath(file_path))
        mime, _ = mimetypes.guess_type(filename)
        if not mime:
            mime = 'audio/mpeg'

        response.set_header("Access-Control-Allow-Origin", "*")
        response.set_header("Accept-Ranges", "bytes")
        return static_file(filename, root=directory, mimetype=mime)

    @app.route('/upload_audio', method=['POST', 'OPTIONS'])
    def upload_audio():
        """
        Receives audio files via FormData (from Drag & Drop or HTML File Input).
        Saves them to imported_audio/ and returns metadata.
        """
        response.content_type = 'application/json'
        response.set_header("Access-Control-Allow-Origin", "*")
        
        if request.method == 'OPTIONS':
            return "{}"

        try:
            uploaded_files = request.files.getall('audio_files')
            
            if not uploaded_files:
                return json.dumps({"success": False, "clips": [], "error": "No audio files received in request."})

            saved_paths = []
            for f in uploaded_files:
                raw_filename = _safe_upload_name(f.filename, _AUDIO_EXTENSIONS)
                if not raw_filename:
                    continue

                save_path = os.path.join(import_dir, raw_filename)
                # Avoid overwriting by appending counter if name exists with different size
                base_name, ext = os.path.splitext(raw_filename)
                counter = 1
                while os.path.exists(save_path):
                    save_path = os.path.join(import_dir, f"{base_name}_{counter}{ext}")
                    counter += 1

                f.save(save_path, overwrite=True)
                media_registry.register(save_path)
                saved_paths.append(save_path)

            if len(saved_paths) < 2:
                clips = [_inspect_uploaded_audio(path) for path in saved_paths]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(4, len(saved_paths)),
                    thread_name_prefix="UploadedAudioProbe"
                ) as pool:
                    clips = list(pool.map(_inspect_uploaded_audio, saved_paths))

            return json.dumps({"success": True, "clips": clips})

        except Exception as e:
            return json.dumps({"success": False, "clips": [], "error": str(e)})

    # Directory for imported images
    image_import_dir = os.path.join(base_dir, 'imported_images')
    os.makedirs(image_import_dir, exist_ok=True)

    @app.route('/stream_image')
    def stream_image():
        file_path = request.query.get('path')
        if not file_path:
            abort(400, "Missing path parameter")
        file_path = media_registry.resolve(file_path, _IMAGE_EXTENSIONS)
        if not file_path:
            abort(404, "Image file not found")

        directory, filename = os.path.split(os.path.abspath(file_path))
        mime, _ = mimetypes.guess_type(filename)
        if not mime or not mime.startswith('image/'):
            mime = 'image/png'

        response.set_header("Access-Control-Allow-Origin", "*")
        response.set_header("Cache-Control", "public, max-age=3600")
        return static_file(filename, root=directory, mimetype=mime)

    @app.route('/thumbnail_image')
    def thumbnail_image():
        file_path = request.query.get('path')
        if not file_path:
            abort(400, "Missing path parameter")
        file_path = media_registry.resolve(file_path, _IMAGE_EXTENSIONS)
        if not file_path:
            abort(404, "Image not found")

        try:
            requested_width = int(request.query.get('width') or 320)
        except (TypeError, ValueError):
            requested_width = 320
        width = min(640, max(96, requested_width))
        file_stat = os.stat(file_path)
        etag = f'"{file_stat.st_size:x}-{file_stat.st_mtime_ns:x}-{width:x}"'
        response.set_header("ETag", etag)
        response.set_header("Cache-Control", "private, max-age=0, must-revalidate")
        if request.headers.get('If-None-Match') == etag:
            response.status = 304
            return ""

        try:
            with _THUMBNAIL_WORKERS:
                thumbnail, mime = _cached_thumbnail(
                    file_path,
                    file_stat.st_size,
                    file_stat.st_mtime_ns,
                    width
                )
            response.content_type = mime
            return thumbnail
        except Exception as exc:
            # Preserve display support for formats Pillow cannot decode rather
            # than making a thumbnail optimization break the image workflow.
            log_error(f"Could not create thumbnail for {file_path}: {exc}")
            directory, filename = os.path.split(file_path)
            mime, _ = mimetypes.guess_type(filename)
            response.set_header("Cache-Control", "public, max-age=3600")
            return static_file(filename, root=directory, mimetype=mime or 'image/png')

    @app.route('/stream_video')
    def stream_video():
        file_path = request.query.get('path')
        if not file_path:
            abort(400, "Missing path parameter")
        file_path = media_registry.resolve(file_path, _VIDEO_EXTENSIONS)
        if not file_path:
            abort(404, "Video file not found")

        directory, filename = os.path.split(os.path.abspath(file_path))
        mime, _ = mimetypes.guess_type(filename)
        if not mime or not mime.startswith('video/'):
            mime = 'video/mp4'

        response.set_header("Access-Control-Allow-Origin", "*")
        response.set_header("Accept-Ranges", "bytes")
        return static_file(filename, root=directory, mimetype=mime)

    @app.route('/upload_images', method=['POST', 'OPTIONS'])
    def upload_images():
        """
        Receives still image files via FormData (from Drag & Drop or HTML File Input).
        Saves them to imported_images/ and returns file paths.
        """
        response.content_type = 'application/json'
        response.set_header("Access-Control-Allow-Origin", "*")
        if request.method == 'OPTIONS':
            return "{}"

        try:
            uploaded_files = request.files.getall('image_files')
            if not uploaded_files:
                return json.dumps({"success": False, "images": [], "error": "No image files received."})

            images = []
            for f in uploaded_files:
                raw_filename = _safe_upload_name(f.filename, _IMAGE_EXTENSIONS)
                if not raw_filename:
                    continue

                save_path = os.path.join(image_import_dir, raw_filename)
                base_name, ext = os.path.splitext(raw_filename)
                counter = 1
                while os.path.exists(save_path):
                    save_path = os.path.join(image_import_dir, f"{base_name}_{counter}{ext}")
                    counter += 1

                f.save(save_path, overwrite=True)
                media_registry.register(save_path)
                file_size = os.path.getsize(save_path)
                file_stat = os.stat(save_path)
                size_str = f"{file_size / (1024 * 1024):.2f} MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.1f} KB"

                images.append({
                    'path': save_path,
                    'filename': os.path.basename(save_path),
                    'size_bytes': file_size,
                    'size_formatted': size_str,
                    'thumbnail_version': f"{file_stat.st_size:x}-{file_stat.st_mtime_ns:x}"
                })

            return json.dumps({"success": True, "images": images})
        except Exception as e:
            return json.dumps({"success": False, "images": [], "error": str(e)})

    return app

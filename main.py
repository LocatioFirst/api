import os
import json
import time
import uuid
import threading
import requests
import base64
from io import BytesIO
from PIL import Image
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- Configuration & Constants ---
ACCOUNTS_FILE = 'accounts.txt'
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.ewogICJyb2xlIjogImFub24iLAogICJpc3MiOiAic3VwYWJhc2UiLAogICJpYXQiOiAxNzM0OTY5NjAwLAogICJleHAiOiAxODkyNzM2MDAwCn0.4NnK23LGYvKPGuKI5rwQn2KbLMzzdE4jXpHwbGCqPqY"
SERVER_API_KEY = "sk_live_9f9a2b4e6c8d1a3f5e9b7c2d4a6f8e1b3c5d7f9a2b4e6c8d1a3f5e9b7c2d4a6f"

# Maximum concurrent tasks
MAX_CONCURRENT_TASKS = 10

# Deevid URLs
URL_AUTH = "https://sp.deevid.ai/auth/v1/token?grant_type=password"
URL_UPLOAD = "https://api.deevid.ai/file-upload/image"
URL_SUBMIT_IMG = "https://api.deevid.ai/text-to-image/task/submit"
URL_SUBMIT_VIDEO = "https://api.deevid.ai/image-to-video/task/submit"
URL_SUBMIT_TXT_VIDEO = "https://api.deevid.ai/text-to-video/task/submit"
URL_ASSETS = "https://api.deevid.ai/my-assets?limit=50&assetType=All&filter=CREATION"
URL_VIDEO_TASKS = "https://api.deevid.ai/video/tasks?page=1&size=20"
URL_QUOTA = "https://api.deevid.ai/subscription/plan"

# ElevenLabs Configuration
ELEVENLABS_API_KEY = "sk_6c017bbed12d6ad43ff5b469d03a532f5c8c3714b9f70602"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

DEVICE_HEADERS = {
    "x-device": "TABLET",
    "x-device-id": "3401879229",
    "x-os": "WINDOWS",
    "x-platform": "WEB",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Global State ---
STATE = {
    "accounts": [],
    "tasks": {},  # task_id -> {status, result_url, logs, mode}
    "running_tasks": 0  # Counter for currently running tasks
}
lock = threading.Lock()

# --- Helper Functions ---

def verify_api_key():
    """Verifies the API key from request headers."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return False
    
    # Support both "Bearer <key>" and direct key
    if auth_header.startswith('Bearer '):
        provided_key = auth_header[7:]
    else:
        provided_key = auth_header
    
    return provided_key == SERVER_API_KEY

def load_accounts():
    """Loads accounts from accounts.txt."""
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    accs = []
    try:
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and ':' in line:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        email = parts[0].strip()
                        pw = parts[1].strip()
                        accs.append({'email': email, 'password': pw})
    except Exception as e:
        print(f"Error loading accounts: {e}")
    return accs

STATE['accounts'] = load_accounts()

def remove_account_from_disk(email):
    """Removes an account from disk only."""
    try:
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
                for line in lines:
                    if not line.strip().startswith(email + ":"):
                        f.write(line)
        print(f"Account removed from disk: {email}")
    except Exception as e:
        print(f"Error removing account from file: {e}")

def get_next_account():
    """Gets the first available account and removes it from the pool (locking)."""
    global STATE
    with lock:
        if not STATE['accounts']:
            return None
        return STATE['accounts'].pop(0)

def can_start_new_task():
    """Checks if a new task can be started (max concurrent limit)."""
    with lock:
        return STATE['running_tasks'] < MAX_CONCURRENT_TASKS

def increment_running_tasks():
    """Increments the running tasks counter."""
    with lock:
        STATE['running_tasks'] += 1
        print(f"Task started. Running tasks: {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS}")

def decrement_running_tasks():
    """Decrements the running tasks counter."""
    with lock:
        STATE['running_tasks'] = max(0, STATE['running_tasks'] - 1)
        print(f"Task finished. Running tasks: {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS}")

def refresh_quota(token):
    """Optional but might be required to activate session."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    try:
        requests.get(URL_QUOTA, headers=headers)
    except:
        pass

def login_with_retry():
    """Tries logging in with available accounts until one succeeds."""
    if not STATE['accounts']:
        print("No accounts loaded!")
        return None, None
        
    tried_count = 0
    max_tries = len(STATE['accounts'])
    
    while tried_count < max_tries:
        account = get_next_account()
        if not account:
            break
        
        tried_count += 1
        headers = {
            "apikey": API_KEY,
        }
        payload = {
            "email": account['email'].strip(),
            "password": account['password'].strip(),
            "gotrue_meta_security": {}
        }
        try:
            resp = requests.post(URL_AUTH, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                token = resp.json().get('access_token')
                if token:
                    refresh_quota(token)
                    return token, account
            print(f"Login failed for {account['email']}: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Login error for {account['email']}: {e}")
            
    return None, None

def resize_image(image_bytes):
    """Resizes image if it exceeds 3000px on any side."""
    try:
        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        max_dim = max(width, height)
        if max_dim > 3000:
            scale = 3000 / max_dim
            img = img.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
        
        out = BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        return out
    except Exception as e:
        print(f"Resize error: {e}")
        return None

def upload_image(token, image_bytes):
    """Uploads image to API and returns image ID."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    resized = resize_image(image_bytes)
    if not resized: return None
    
    files = {"file": ("image.png", resized, "image/png")}
    data = {"width": "1024", "height": "1536"} 
    try:
        resp = requests.post(URL_UPLOAD, headers=headers, files=files, data=data)
        if resp.status_code in [200, 201]:
            return resp.json()['data']['data']['id']
    except Exception as e:
        print(f"Upload error: {e}")
    return None

def process_image_task(task_id, params):
    """Worker for image generation."""
    increment_running_tasks()
    try:
        STATE['tasks'][task_id]['status'] = 'running'
        try:
            token, account = login_with_retry()
            if not token:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append("All accounts failed to login.")
                return

            headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
            
            user_image_ids = []
            if params.get('image'):
                img_data = base64.b64decode(params['image'])
                img_id = upload_image(token, img_data)
                if img_id:
                    user_image_ids.append(img_id)
                else:
                    STATE['tasks'][task_id]['status'] = 'failed'
                    STATE['tasks'][task_id]['logs'].append("Image upload failed.")
                    return

            model_version = params.get('model', 'MODEL_FOUR_NANO_BANANA_PRO')
            payload = {
                "prompt": params.get('prompt', ''),
                "imageSize": params.get('imageSize', 'SIXTEEN_BY_NINE'),
                "count": 1,
                "modelType": "MODEL_FOUR",
                "modelVersion": model_version
            }
            
            if model_version == 'MODEL_FOUR_NANO_BANANA_PRO':
                payload["resolution"] = params.get('resolution', '2K')
                
            if user_image_ids:
                payload["userImageIds"] = user_image_ids

            resp = requests.post(URL_SUBMIT_IMG, headers=headers, json=payload)
            resp_json = resp.json()
            
            error = resp_json.get('error')
            if error and error.get('code') != 0:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append(f"Submit error: {resp_json}")
                return

            remove_account_from_disk(account['email'])

            api_task_id = str(resp_json['data']['data']['taskId'])
            STATE['tasks'][task_id]['logs'].append(f"API Task ID: {api_task_id}")

            for _ in range(300):
                time.sleep(2)
                try:
                    poll = requests.get(URL_ASSETS, headers=headers).json()
                    groups = poll.get('data', {}).get('data', {}).get('groups', [])
                    for group in groups:
                        for item in group.get('items', []):
                            creation = item.get('detail', {}).get('creation', {})
                            if str(creation.get('taskId')) == api_task_id:
                                if creation.get('taskState') == 'SUCCESS':
                                    urls = creation.get('noWaterMarkImageUrl', [])
                                    if urls:
                                        STATE['tasks'][task_id]['status'] = 'completed'
                                        STATE['tasks'][task_id]['result_url'] = urls[0]
                                        return
                                elif creation.get('taskState') == 'FAIL':
                                    STATE['tasks'][task_id]['status'] = 'failed'
                                    return
                except:
                    pass
            STATE['tasks'][task_id]['status'] = 'timeout'
        except Exception as e:
            STATE['tasks'][task_id]['status'] = 'error'
            STATE['tasks'][task_id]['logs'].append(str(e))
    finally:
        decrement_running_tasks()

def process_video_task(task_id, params):
    """Worker for video generation."""
    increment_running_tasks()
    try:
        STATE['tasks'][task_id]['status'] = 'running'
        try:
            token, account = login_with_retry()
            if not token:
                STATE['tasks'][task_id]['status'] = 'failed'
                return

            headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
            
            is_i2v = params.get('image') is not None
            payload = {
                "prompt": params.get('prompt', ''),
                "resolution": "720p",
                "lengthOfSecond": 10,
                "aiPromptEnhance": True,
                "size": params.get('size', 'SIXTEEN_BY_NINE'),
                "addEndFrame": False
            }

            if is_i2v:
                img_data = base64.b64decode(params['image'])
                img_id = upload_image(token, img_data)
                if not img_id:
                    STATE['tasks'][task_id]['status'] = 'failed'
                    return
                payload["userImageId"] = int(str(img_id).strip())
                payload["modelVersion"] = "MODEL_ELEVEN_IMAGE_TO_VIDEO_V2"
                url_submit = URL_SUBMIT_VIDEO
            else:
                payload["modelType"] = "MODEL_ELEVEN"
                payload["modelVersion"] = "MODEL_ELEVEN_TEXT_TO_VIDEO_V2"
                url_submit = URL_SUBMIT_TXT_VIDEO

            resp = requests.post(url_submit, headers=headers, json=payload)
            resp_json = resp.json()
            
            error = resp_json.get('error')
            if error and error.get('code') != 0:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append(f"Submit error: {resp_json}")
                return

            remove_account_from_disk(account['email'])

            api_task_id = str(resp_json['data']['data']['taskId'])
            
            for _ in range(600):
                time.sleep(5)
                try:
                    poll = requests.get(URL_VIDEO_TASKS, headers=headers).json()
                    video_list = poll.get('data', {}).get('data', {}).get('data', [])
                    if not video_list and isinstance(poll.get('data', {}).get('data'), list):
                        video_list = poll['data']['data']
                        
                    for v in video_list:
                        if str(v.get('taskId')) == api_task_id:
                            if v.get('taskState') == 'SUCCESS':
                                url = v.get('noWaterMarkVideoUrl') or v.get('noWatermarkVideoUrl')
                                if isinstance(url, list) and url: url = url[0]
                                if url:
                                    STATE['tasks'][task_id]['status'] = 'completed'
                                    STATE['tasks'][task_id]['result_url'] = url
                                    return
                            elif v.get('taskState') == 'FAIL':
                                STATE['tasks'][task_id]['status'] = 'failed'
                                return
                except:
                    pass
            STATE['tasks'][task_id]['status'] = 'timeout'
        except Exception as e:
            STATE['tasks'][task_id]['status'] = 'error'
            STATE['tasks'][task_id]['logs'].append(str(e))
    finally:
        decrement_running_tasks()

def process_tts_task(task_id, params):
    """Worker for ElevenLabs TTS generation."""
    increment_running_tasks()
    try:
        STATE['tasks'][task_id]['status'] = 'running'
        try:
            if not ELEVENLABS_API_KEY:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append("ElevenLabs API key not configured.")
                return

            voice_id = params.get('voice_id', 'EXAVITQu4vr4xnSDxMaL')  # Default: Bella
            text = params.get('text', '')
            
            if not text:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append("Text is required.")
                return

            # Voice settings
            stability = params.get('stability', 0.5)
            similarity_boost = params.get('similarity_boost', 0.75)
            style = params.get('style', 0.0)
            speed = params.get('speed', 1.0)
            
            url = f"{ELEVENLABS_TTS_URL}/{voice_id}"
            
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": ELEVENLABS_API_KEY
            }
            
            payload = {
                "text": text,
                "model_id": params.get('model_id', 'eleven_multilingual_v2'),
                "voice_settings": {
                    "stability": stability,
                    "similarity_boost": similarity_boost,
                    "style": style,
                    "use_speaker_boost": params.get('use_speaker_boost', True)
                }
            }
            
            if speed != 1.0:
                payload["voice_settings"]["speed"] = speed

            STATE['tasks'][task_id]['logs'].append(f"Generating TTS with voice: {voice_id}")
            
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            
            if response.status_code == 200:
                audio_base64 = base64.b64encode(response.content).decode('utf-8')
                
                STATE['tasks'][task_id]['status'] = 'completed'
                STATE['tasks'][task_id]['result_url'] = f"data:audio/mpeg;base64,{audio_base64}"
                STATE['tasks'][task_id]['logs'].append("TTS generation successful.")
            else:
                STATE['tasks'][task_id]['status'] = 'failed'
                STATE['tasks'][task_id]['logs'].append(f"ElevenLabs API error: {response.status_code} - {response.text}")
                
        except Exception as e:
            STATE['tasks'][task_id]['status'] = 'error'
            STATE['tasks'][task_id]['logs'].append(str(e))
    finally:
        decrement_running_tasks()

# --- API Routes ---

@app.route('/api/generate/image', methods=['POST'])
def generate_image():
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    if not STATE['accounts']:
        return jsonify({"error": "No accounts available"}), 503
    
    if not can_start_new_task():
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    STATE['tasks'][task_id] = {
        'status': 'pending', 'result_url': None, 'logs': [], 'mode': 'image'
    }
    
    threading.Thread(target=process_image_task, args=(task_id, data)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/video', methods=['POST'])
def generate_video():
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    if not STATE['accounts']:
        return jsonify({"error": "No accounts available"}), 503
    
    if not can_start_new_task():
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    STATE['tasks'][task_id] = {
        'status': 'pending', 'result_url': None, 'logs': [], 'mode': 'video'
    }
    
    threading.Thread(target=process_video_task, args=(task_id, data)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/tts', methods=['POST'])
def generate_tts():
    """
    ElevenLabs Text-to-Speech endpoint
    
    Required:
    - text: Metni ses dosyasına çevirir
    
    Optional:
    - voice_id: Ses ID'si (default: 'EXAVITQu4vr4xnSDxMaL' - Bella)
    - speed: Ses hızı (0.25 - 4.0, default: 1.0)
    - stability: Ses stabilitesi (0.0 - 1.0, default: 0.5)
    - similarity_boost: Benzerlik artırma (0.0 - 1.0, default: 0.75)
    - style: Ses stili (0.0 - 1.0, default: 0.0)
    - model_id: Model ID (default: 'eleven_multilingual_v2')
    - use_speaker_boost: Konuşmacı güçlendirme (default: True)
    """
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'text' not in data:
        return jsonify({"error": "Text required"}), 400
    
    if not ELEVENLABS_API_KEY:
        return jsonify({"error": "ElevenLabs API key not configured"}), 500
    
    if not can_start_new_task():
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    STATE['tasks'][task_id] = {
        'status': 'pending', 'result_url': None, 'logs': [], 'mode': 'tts'
    }
    
    threading.Thread(target=process_tts_task, args=(task_id, data)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/elevenlabs/voices', methods=['GET'])
def get_elevenlabs_voices():
    """ElevenLabs'daki mevcut sesleri listeler"""
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    if not ELEVENLABS_API_KEY:
        return jsonify({"error": "ElevenLabs API key not configured"}), 500
    
    try:
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        response = requests.get(ELEVENLABS_VOICES_URL, headers=headers)
        
        if response.status_code == 200:
            voices_data = response.json()
            simplified_voices = [
                {
                    "name": voice.get("name"),
                    "voice_id": voice.get("voice_id")
                }
                for voice in voices_data.get("voices", [])
            ]
            return jsonify({"voices": simplified_voices})
        else:
            return jsonify({"error": f"Failed to fetch voices: {response.text}"}), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    task = STATE['tasks'].get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)

@app.route('/api/status', methods=['GET'])
def get_all_tasks_status():
    """Returns all tasks with their current status"""
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "tasks": STATE['tasks'],
        "running_tasks": STATE['running_tasks'],
        "max_concurrent": MAX_CONCURRENT_TASKS
    })

@app.route('/api/quota', methods=['GET'])
def get_quota():
    if not verify_api_key():
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "quota": len(STATE['accounts']),
        "running_tasks": STATE['running_tasks'],
        "max_concurrent": MAX_CONCURRENT_TASKS,
        "available_slots": MAX_CONCURRENT_TASKS - STATE['running_tasks']
    })

if __name__ == '__main__':
    print(f"Loaded {len(STATE['accounts'])} accounts.")
    print(f"Server API Key: {SERVER_API_KEY}")
    print(f"Maximum concurrent tasks: {MAX_CONCURRENT_TASKS}")
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)

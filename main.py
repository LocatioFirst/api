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
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

app = Flask(__name__)

# --- Configuration & Constants ---
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.ewogICJyb2xlIjogImFub24iLAogICJpc3MiOiAic3VwYWJhc2UiLAogICJpYXQiOiAxNzM0OTY5NjAwLAogICJleHAiOiAxODkyNzM2MDAwCn0.4NnK23LGYvKPGuKI5rwQn2KbLMzzdE4jXpHwbGCqPqY"

# Maximum concurrent tasks
MAX_CONCURRENT_TASKS = 10

# PostgreSQL Database URL
DATABASE_URL = "postgresql://db_ztvp_user:2GTqbMWIXYs6uMlbMytfUrxEhrMTb83I@dpg-d62upkhr0fns73dpmu60-a/db_ztvp"

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
ELEVENLABS_API_KEY = "sk_d7cd9c0991b928ab3a7b9f04b0dedfcd7d56d790f2cca302"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

DEVICE_HEADERS = {
    "x-device": "TABLET",
    "x-device-id": "3401879229",
    "x-os": "WINDOWS",
    "x-platform": "WEB",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Global State (sadece cache için) ---
STATE = {
    "running_tasks": 0,
}
lock = threading.Lock()

# --- DATABASE FUNCTIONS ---

@contextmanager
def get_db_connection():
    """Database connection context manager"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Database error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def init_database():
    """Initialize database tables"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # API Keys table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    api_key TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Accounts table (her user için account'lar)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key) REFERENCES api_keys(api_key) ON DELETE CASCADE
                )
            """)
            
            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_url TEXT,
                    logs TEXT,
                    mode TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key) REFERENCES api_keys(api_key) ON DELETE CASCADE
                )
            """)
            
            # App state table (running_tasks counter)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Initialize running_tasks if not exists
            cursor.execute("""
                INSERT INTO app_state (key, value)
                VALUES ('running_tasks', 0)
                ON CONFLICT (key) DO NOTHING
            """)
            
            cursor.close()
            print("✅ Database tables initialized")
            return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

def verify_api_key():
    """Verifies the API key from request headers against database."""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None
    
    # Support both "Bearer <key>" and direct key
    if auth_header.startswith('Bearer '):
        provided_key = auth_header[7:]
    else:
        provided_key = auth_header
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT api_key FROM api_keys WHERE api_key = %s", (provided_key,))
            result = cursor.fetchone()
            cursor.close()
            
            if result:
                return provided_key
    except Exception as e:
        print(f"Error verifying API key: {e}")
    
    return None

def get_user_accounts(api_key):
    """Gets unused accounts for a specific user from database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT email, password 
                FROM accounts 
                WHERE api_key = %s AND is_used = FALSE
                ORDER BY created_at ASC
            """, (api_key,))
            rows = cursor.fetchall()
            cursor.close()
            
            return [{'email': row['email'], 'password': row['password']} for row in rows]
    except Exception as e:
        print(f"Error getting user accounts: {e}")
        return []

def get_next_account(api_key):
    """Gets the first available account and marks it as used."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get first unused account
            cursor.execute("""
                SELECT id, email, password 
                FROM accounts 
                WHERE api_key = %s AND is_used = FALSE
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, (api_key,))
            
            row = cursor.fetchone()
            if not row:
                cursor.close()
                return None
            
            # Mark as used
            cursor.execute("""
                UPDATE accounts 
                SET is_used = TRUE 
                WHERE id = %s
            """, (row['id'],))
            
            cursor.close()
            return {'email': row['email'], 'password': row['password']}
    except Exception as e:
        print(f"Error getting next account: {e}")
        return None

def remove_account_from_db(email, api_key):
    """Removes an account from database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM accounts 
                WHERE api_key = %s AND email = %s
            """, (api_key, email))
            cursor.close()
            print(f"✅ Account removed: {email}")
    except Exception as e:
        print(f"Error removing account: {e}")

def get_user_tasks(api_key):
    """Gets all tasks for a specific user from database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT task_id, status, result_url, logs, mode
                FROM tasks
                WHERE api_key = %s
                ORDER BY created_at DESC
            """, (api_key,))
            rows = cursor.fetchall()
            cursor.close()
            
            tasks = {}
            for row in rows:
                tasks[row['task_id']] = {
                    'status': row['status'],
                    'result_url': row['result_url'],
                    'logs': json.loads(row['logs']) if row['logs'] else [],
                    'mode': row['mode']
                }
            return tasks
    except Exception as e:
        print(f"Error getting user tasks: {e}")
        return {}

def save_task_to_db(task_id, api_key, task_data):
    """Save or update task in database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tasks (task_id, api_key, status, result_url, logs, mode, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (task_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    result_url = EXCLUDED.result_url,
                    logs = EXCLUDED.logs,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                task_id,
                api_key,
                task_data.get('status'),
                task_data.get('result_url'),
                json.dumps(task_data.get('logs', [])),
                task_data.get('mode')
            ))
            cursor.close()
    except Exception as e:
        print(f"Error saving task {task_id}: {e}")

def get_running_tasks_count():
    """Get running tasks count from database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_state WHERE key = 'running_tasks'")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
    except Exception as e:
        print(f"Error getting running tasks: {e}")
        return 0

def update_running_tasks_count(value):
    """Update running tasks count in database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE app_state 
                SET value = %s, updated_at = CURRENT_TIMESTAMP
                WHERE key = 'running_tasks'
            """, (value,))
            cursor.close()
    except Exception as e:
        print(f"Error updating running tasks: {e}")

# --- Helper Functions ---

def can_start_new_task():
    """Checks if a new task can be started (max concurrent limit)."""
    with lock:
        return STATE['running_tasks'] < MAX_CONCURRENT_TASKS

def increment_running_tasks():
    """Increments the running tasks counter."""
    with lock:
        STATE['running_tasks'] += 1
        update_running_tasks_count(STATE['running_tasks'])
        print(f"✅ Task started. Running: {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS}")

def decrement_running_tasks():
    """Decrements the running tasks counter."""
    with lock:
        STATE['running_tasks'] = max(0, STATE['running_tasks'] - 1)
        update_running_tasks_count(STATE['running_tasks'])
        print(f"✅ Task finished. Running: {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS}")

def refresh_quota(token):
    """Optional but might be required to activate session."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    try:
        requests.get(URL_QUOTA, headers=headers)
    except:
        pass

def login_with_retry(api_key):
    """Tries logging in with available accounts until one succeeds."""
    max_tries = 5
    tried_count = 0
    
    while tried_count < max_tries:
        account = get_next_account(api_key)
        if not account:
            print(f"❌ No more accounts available for API key")
            break
        
        tried_count += 1
        headers = {"apikey": API_KEY}
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
            print(f"❌ Login failed for {account['email']}: {resp.status_code}")
        except Exception as e:
            print(f"❌ Login error for {account['email']}: {e}")
            
    return None, None

def resize_image(image_bytes):
    """Resizes image if it exceeds 3000px on any side."""
    img = Image.open(BytesIO(image_bytes))
    w, h = img.size
    if w > 3000 or h > 3000:
        ratio = min(3000 / w, 3000 / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    return image_bytes

def upload_image(token, image_bytes, logs):
    """Uploads image and returns asset URL."""
    resized = resize_image(image_bytes)
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    files = {'file': ('image.png', BytesIO(resized), 'image/png')}
    resp = requests.post(URL_UPLOAD, files=files, headers=headers)
    if resp.status_code != 200:
        logs.append(f"Upload failed: {resp.status_code}")
        raise Exception("Image upload failed.")
    asset_url = resp.json().get('assetUrl')
    if not asset_url:
        raise Exception("No assetUrl in upload response.")
    return asset_url

def submit_text_to_image(token, prompt, aspect_ratio, logs):
    """Submits text-to-image task."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    payload = {"prompt": prompt, "aspectRatio": aspect_ratio}
    resp = requests.post(URL_SUBMIT_IMG, json=payload, headers=headers)
    if resp.status_code != 200:
        logs.append(f"Submit image failed: {resp.status_code}")
        raise Exception("Text-to-image submit failed.")
    return resp.json().get('taskId')

def submit_image_to_video(token, image_url, prompt, logs):
    """Submits image-to-video task."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    payload = {"imageUrl": image_url, "prompt": prompt, "aspectRatio": "16:9"}
    resp = requests.post(URL_SUBMIT_VIDEO, json=payload, headers=headers)
    if resp.status_code != 200:
        logs.append(f"Submit video failed: {resp.status_code}")
        raise Exception("Image-to-video submit failed.")
    return resp.json().get('taskId')

def submit_text_to_video(token, prompt, aspect_ratio, logs):
    """Submits text-to-video task."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    payload = {"prompt": prompt, "aspectRatio": aspect_ratio}
    resp = requests.post(URL_SUBMIT_TXT_VIDEO, json=payload, headers=headers)
    if resp.status_code != 200:
        logs.append(f"Submit text-to-video failed: {resp.status_code}")
        raise Exception("Text-to-video submit failed.")
    return resp.json().get('taskId')

def wait_for_completion(token, deevid_task_id, logs):
    """Waits for task completion by polling assets."""
    headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
    max_attempts = 120
    for i in range(max_attempts):
        try:
            resp = requests.get(URL_ASSETS, headers=headers)
            if resp.status_code == 200:
                assets = resp.json()
                for asset in assets:
                    if asset.get('taskId') == deevid_task_id:
                        status = asset.get('status')
                        logs.append(f"Status: {status}")
                        if status == 'COMPLETED':
                            media_url = asset.get('medias', [{}])[0].get('url')
                            if media_url:
                                logs.append(f"Video ready: {media_url}")
                                return media_url
                        if status in ['FAILED', 'CANCELLED']:
                            raise Exception(f"Task failed with status {status}")
        except Exception as e:
            logs.append(f"Poll error: {e}")
        time.sleep(5)
    raise Exception("Timeout waiting for video.")

# --- Task Processing Functions ---

def process_image_task(task_id, data, api_key):
    """Process image generation task."""
    increment_running_tasks()
    
    task_data = {
        'status': 'processing',
        'result_url': None,
        'logs': ['Starting image generation...'],
        'mode': 'image'
    }
    save_task_to_db(task_id, api_key, task_data)
    
    try:
        token, account = login_with_retry(api_key)
        if not token:
            task_data['status'] = 'failed'
            task_data['logs'].append("All login attempts failed")
            save_task_to_db(task_id, api_key, task_data)
            return
        
        task_data['logs'].append(f"Logged in as {account['email']}")
        save_task_to_db(task_id, api_key, task_data)
        
        prompt = data['prompt']
        aspect_ratio = data.get('aspect_ratio', '1:1')
        
        deevid_task_id = submit_text_to_image(token, prompt, aspect_ratio, task_data['logs'])
        task_data['logs'].append(f"Image task submitted: {deevid_task_id}")
        save_task_to_db(task_id, api_key, task_data)
        
        result_url = wait_for_completion(token, deevid_task_id, task_data['logs'])
        task_data['status'] = 'completed'
        task_data['result_url'] = result_url
        task_data['logs'].append("Image generation completed.")
        save_task_to_db(task_id, api_key, task_data)
        
        remove_account_from_db(account['email'], api_key)
        
    except Exception as e:
        task_data['status'] = 'error'
        task_data['logs'].append(str(e))
        save_task_to_db(task_id, api_key, task_data)
    finally:
        decrement_running_tasks()

def process_video_task(task_id, data, api_key):
    """Process video generation task."""
    increment_running_tasks()
    
    task_data = {
        'status': 'processing',
        'result_url': None,
        'logs': ['Starting video generation...'],
        'mode': 'video'
    }
    save_task_to_db(task_id, api_key, task_data)
    
    try:
        token, account = login_with_retry(api_key)
        if not token:
            task_data['status'] = 'failed'
            task_data['logs'].append("All login attempts failed")
            save_task_to_db(task_id, api_key, task_data)
            return
        
        task_data['logs'].append(f"Logged in as {account['email']}")
        save_task_to_db(task_id, api_key, task_data)
        
        prompt = data['prompt']
        image_base64 = data.get('image')
        aspect_ratio = data.get('aspect_ratio', '16:9')
        
        if image_base64:
            try:
                img_data = base64.b64decode(image_base64.split(',')[1] if ',' in image_base64 else image_base64)
            except Exception as e:
                task_data['status'] = 'error'
                task_data['logs'].append(f"Invalid base64 image: {e}")
                save_task_to_db(task_id, api_key, task_data)
                return
            
            image_url = upload_image(token, img_data, task_data['logs'])
            task_data['logs'].append(f"Image uploaded: {image_url}")
            save_task_to_db(task_id, api_key, task_data)
            
            deevid_task_id = submit_image_to_video(token, image_url, prompt, task_data['logs'])
            task_data['logs'].append(f"Image-to-video task: {deevid_task_id}")
        else:
            deevid_task_id = submit_text_to_video(token, prompt, aspect_ratio, task_data['logs'])
            task_data['logs'].append(f"Text-to-video task: {deevid_task_id}")
        
        save_task_to_db(task_id, api_key, task_data)
        
        result_url = wait_for_completion(token, deevid_task_id, task_data['logs'])
        task_data['status'] = 'completed'
        task_data['result_url'] = result_url
        task_data['logs'].append("Video generation completed.")
        save_task_to_db(task_id, api_key, task_data)
        
        remove_account_from_db(account['email'], api_key)
        
    except Exception as e:
        task_data['status'] = 'error'
        task_data['logs'].append(str(e))
        save_task_to_db(task_id, api_key, task_data)
    finally:
        decrement_running_tasks()

def process_tts_task(task_id, data, api_key):
    """Process TTS generation task."""
    increment_running_tasks()
    
    task_data = {
        'status': 'processing',
        'result_url': None,
        'logs': ['Starting TTS generation...'],
        'mode': 'tts'
    }
    save_task_to_db(task_id, api_key, task_data)
    
    try:
        text = data['text']
        voice_id = data.get('voice_id', 'EXAVITQu4vr4xnSDxMaL')
        
        url = f"{ELEVENLABS_TTS_URL}/{voice_id}"
        
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "text": text,
            "model_id": data.get('model_id', 'eleven_multilingual_v2'),
            "voice_settings": {
                "stability": data.get('stability', 0.5),
                "similarity_boost": data.get('similarity_boost', 0.75),
                "style": data.get('style', 0.0),
                "use_speaker_boost": data.get('use_speaker_boost', True)
            }
        }
        
        if 'speed' in data:
            payload['voice_settings']['speed'] = data['speed']

        task_data['logs'].append(f"Generating TTS with voice: {voice_id}")
        save_task_to_db(task_id, api_key, task_data)
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        
        if response.status_code == 200:
            audio_base64 = base64.b64encode(response.content).decode('utf-8')
            
            task_data['status'] = 'completed'
            task_data['result_url'] = f"data:audio/mpeg;base64,{audio_base64}"
            task_data['logs'].append("TTS generation successful.")
        else:
            task_data['status'] = 'failed'
            task_data['logs'].append(f"ElevenLabs API error: {response.status_code} - {response.text}")
            
        save_task_to_db(task_id, api_key, task_data)
            
    except Exception as e:
        task_data['status'] = 'error'
        task_data['logs'].append(str(e))
        save_task_to_db(task_id, api_key, task_data)
    finally:
        decrement_running_tasks()

# --- API Routes ---

@app.route('/api/generate/image', methods=['POST'])
def generate_image():
    user_key = verify_api_key()
    if not user_key:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    user_accounts = get_user_accounts(user_key)
    if not user_accounts:
        return jsonify({"error": "No accounts available for this user"}), 503
    
    if not can_start_new_task():
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    
    threading.Thread(target=process_image_task, args=(task_id, data, user_key)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/video', methods=['POST'])
def generate_video():
    user_key = verify_api_key()
    if not user_key:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    user_accounts = get_user_accounts(user_key)
    if not user_accounts:
        return jsonify({"error": "No accounts available for this user"}), 503
    
    if not can_start_new_task():
        return jsonify({
            "error": "Maximum concurrent tasks reached",
            "message": f"Currently {STATE['running_tasks']}/{MAX_CONCURRENT_TASKS} tasks running. Please wait."
        }), 429
    
    task_id = str(uuid.uuid4())
    
    threading.Thread(target=process_video_task, args=(task_id, data, user_key)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/tts', methods=['POST'])
def generate_tts():
    """ElevenLabs Text-to-Speech endpoint"""
    user_key = verify_api_key()
    if not user_key:
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
    
    threading.Thread(target=process_tts_task, args=(task_id, data, user_key)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/elevenlabs/voices', methods=['GET'])
def get_elevenlabs_voices():
    """ElevenLabs'daki mevcut sesleri listeler"""
    user_key = verify_api_key()
    if not user_key:
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
    user_key = verify_api_key()
    if not user_key:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_tasks = get_user_tasks(user_key)
    task = user_tasks.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)

@app.route('/api/status', methods=['GET'])
def get_all_tasks_status():
    """Returns all tasks for the authenticated user"""
    user_key = verify_api_key()
    if not user_key:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_tasks = get_user_tasks(user_key)
    return jsonify({
        "tasks": user_tasks,
        "running_tasks": STATE['running_tasks'],
        "max_concurrent": MAX_CONCURRENT_TASKS
    })

@app.route('/api/quota', methods=['GET'])
def get_quota():
    user_key = verify_api_key()
    if not user_key:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_accounts = get_user_accounts(user_key)
    return jsonify({
        "quota": len(user_accounts),
        "running_tasks": STATE['running_tasks'],
        "max_concurrent": MAX_CONCURRENT_TASKS,
        "available_slots": MAX_CONCURRENT_TASKS - STATE['running_tasks']
    })

if __name__ == '__main__':
    # Database'i initialize et
    print("🔧 Initializing database...")
    if not init_database():
        print("❌ Failed to initialize database. Exiting.")
        exit(1)
    
    # Running tasks sayısını yükle
    STATE['running_tasks'] = get_running_tasks_count()
    print(f"✅ Running tasks restored: {STATE['running_tasks']}")
    print(f"🚀 Maximum concurrent tasks: {MAX_CONCURRENT_TASKS}")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

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

URL_AUTH = "https://sp.deevid.ai/auth/v1/token?grant_type=password"
URL_UPLOAD = "https://api.deevid.ai/file-upload/image"
URL_SUBMIT_IMG = "https://api.deevid.ai/text-to-image/task/submit"
URL_SUBMIT_VIDEO = "https://api.deevid.ai/image-to-video/task/submit"
URL_SUBMIT_TXT_VIDEO = "https://api.deevid.ai/text-to-video/task/submit"
URL_ASSETS = "https://api.deevid.ai/my-assets?limit=50&assetType=All&filter=CREATION"
URL_VIDEO_TASKS = "https://api.deevid.ai/video/tasks?page=1&size=20"
URL_QUOTA = "https://api.deevid.ai/subscription/plan"

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
    "tasks": {}  # task_id -> {status, result_url, logs, mode}
}
lock = threading.Lock()

# --- Helper Functions ---

def load_accounts():
    """Loads accounts from hardcoded list."""
    return [
        {'email': 'mqix9v7v53bjc82@spamok.com', 'password': 'windows700'},
        {'email': 'v0oo59qdcd1atml@spamok.com', 'password': 'windows700'},
        {'email': '372z6yqtv6ou4k2@spamok.com', 'password': 'windows700'},
        {'email': 'nkhelrx442imqp4@spamok.com', 'password': 'windows700'},
        {'email': '6sfjxbgiz3jvq1t@spamok.com', 'password': 'windows700'},
        {'email': '5juy0vkjr9kn9iv@spamok.com', 'password': 'windows700'},
        {'email': 'fm6ic3ozbspl3fb@spamok.com', 'password': 'windows700'},
        {'email': '5b05bkynafvto6k@spamok.com', 'password': 'windows700'},
        {'email': '1giona6l8colq4k@spamok.com', 'password': 'windows700'},
        {'email': '63fnko3neyobf0w@spamok.com', 'password': 'windows700'},
        {'email': '86m12843kd54rip@spamok.com', 'password': 'windows700'},
        {'email': 'un8wxezpzii6ja1@spamok.com', 'password': 'windows700'},
        {'email': 'jgovnzx8co3biti@spamok.com', 'password': 'windows700'},
        {'email': '8722cin3p7i5owa@spamok.com', 'password': 'windows700'},
        {'email': 'vrqsuvol263wc23@spamok.com', 'password': 'windows700'},
        {'email': 'qcbyqnucvv9957h@spamok.com', 'password': 'windows700'},
        {'email': 'gj4nm3lbuztjw47@spamok.com', 'password': 'windows700'},
        {'email': 'mm054a55odwlfk5@spamok.com', 'password': 'windows700'},
        {'email': '5iw2uoni28ikumq@spamok.com', 'password': 'windows700'},
        {'email': 'hsdkgm97d7brynv@spamok.com', 'password': 'windows700'},
        {'email': 'x3rodjkjz4pihfu@spamok.com', 'password': 'windows700'},
        {'email': '6o0w57u9r4rrahf@spamok.com', 'password': 'windows700'},
        {'email': '440ravi1w7b8dmx@spamok.com', 'password': 'windows700'}
    ]

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
        # pop(0) ensures sequential processing (first in, first out)
        # and "locking" because it's no longer in the available pool.
        return STATE['accounts'].pop(0)

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
        # Minimal headers exactly as in original script
        headers = {
            "apikey": API_KEY,
        }
        payload = {
            "email": account['email'].strip(),
            "password": account['password'].strip(),
            "gotrue_meta_security": {}
        }
        try:
            # Note: json= automatically sets Content-Type to application/json
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
    STATE['tasks'][task_id]['status'] = 'running'
    try:
        token, account = login_with_retry()
        if not token:
            STATE['tasks'][task_id]['status'] = 'failed'
            STATE['tasks'][task_id]['logs'].append("All accounts failed to login.")
            return

        headers = {"authorization": f"Bearer {token}", **DEVICE_HEADERS}
        
        # Handle Base64 image for img2img
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

        # Prepare payload
        model_version = params.get('model', 'MODEL_FOUR_NANO_BANANA_PRO')
        payload = {
            "prompt": params.get('prompt', ''),
            "imageSize": params.get('imageSize', 'SIXTEEN_BY_NINE'),
            "count": 1,
            "modelType": "MODEL_FOUR",
            "modelVersion": model_version
        }
        
        # Only add resolution if model is Nano Banana PRO
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

        # SUCCESS: Remove account from file
        remove_account_from_disk(account['email'])

        api_task_id = str(resp_json['data']['data']['taskId']) # Store as string
        STATE['tasks'][task_id]['logs'].append(f"API Task ID: {api_task_id}")

        # Polling
        for _ in range(300): # 10 mins timeout
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

def process_video_task(task_id, params):
    """Worker for video generation."""
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
            "lengthOfSecond": int(params.get('duration', 10)),
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

        # SUCCESS: Remove account from file and memory
        #remove_account(account['email'])

        api_task_id = str(resp_json['data']['data']['taskId']) # Store as string
        
        # Polling
        for _ in range(600): # 20 mins timeout
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

# --- API Routes ---

@app.route('/api/generate/image', methods=['POST'])
def generate_image():
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    if not STATE['accounts']:
        return jsonify({"error": "No accounts available"}), 503
    
    task_id = str(uuid.uuid4())
    STATE['tasks'][task_id] = {
        'status': 'pending', 'result_url': None, 'logs': [], 'mode': 'image'
    }
    
    threading.Thread(target=process_image_task, args=(task_id, data)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/generate/video', methods=['POST'])
def generate_video():
    data = request.json
    if not data or 'prompt' not in data:
        return jsonify({"error": "Prompt required"}), 400
    
    if not STATE['accounts']:
        return jsonify({"error": "No accounts available"}), 503
    
    task_id = str(uuid.uuid4())
    STATE['tasks'][task_id] = {
        'status': 'pending', 'result_url': None, 'logs': [], 'mode': 'video'
    }
    
    threading.Thread(target=process_video_task, args=(task_id, data)).start()
    return jsonify({"task_id": task_id})

@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    task = STATE['tasks'].get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)

@app.route('/api/debug/accounts', methods=['GET'])
def debug_accounts():
    return jsonify({
        "loaded_count": len(STATE['accounts'])
    })

if __name__ == '__main__':
    STATE['accounts'] = load_accounts()
    print(f"Loaded {len(STATE['accounts'])} accounts.")
    # Render.com için host ve port ayarları
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

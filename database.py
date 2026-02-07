"""
Database Module for API
Supports both SQLite (local) and PostgreSQL (production).
Set DATABASE_URL environment variable for PostgreSQL.
"""
import os
import json
import threading
from datetime import datetime

# PostgreSQL Configuration
DATABASE_URL = "postgresql://db_t2ps_user:zUarIXgso178onjh2FzNLSBxV4zB31gV@dpg-d63gidhr0fns73bl2tbg-a/db_t2ps"

import psycopg2
from psycopg2.extras import RealDictCursor
DB_TYPE = 'postgresql'
print(f"Using PostgreSQL database")

db_lock = threading.Lock()


def get_connection():
    """Returns a database connection."""
    if DB_TYPE == 'postgresql':
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def init_db():
    """Initializes the database with required tables."""
    with db_lock:
        conn = get_connection()
        cursor = conn.cursor()
        
        if DB_TYPE == 'postgresql':
            # PostgreSQL syntax
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS api_keys (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    api_key_id INTEGER NOT NULL REFERENCES api_keys(id),
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(api_key_id, email)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    api_key_id INTEGER NOT NULL REFERENCES api_keys(id),
                    task_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'pending',
                    result_url TEXT,
                    logs TEXT DEFAULT '[]',
                    mode TEXT,
                    external_task_id TEXT,
                    token TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Safely add columns to existing PostgreSQL table
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='external_task_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE tasks ADD COLUMN external_task_id TEXT")
            
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name='token'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE tasks ADD COLUMN token TEXT")
                
        else:
            # SQLite syntax
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
                    UNIQUE(api_key_id, email)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER NOT NULL,
                    task_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'pending',
                    result_url TEXT,
                    logs TEXT DEFAULT '[]',
                    mode TEXT,
                    external_task_id TEXT,
                    token TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            ''')
            
            # Safely add columns to existing SQLite table
            cursor.execute("PRAGMA table_info(tasks)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'external_task_id' not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN external_task_id TEXT")
            if 'token' not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN token TEXT")
        
        conn.commit()
        conn.close()
        print("Database tables initialized.")


def _get_param_placeholder(index=None):
    """Returns the parameter placeholder for the current DB type."""
    if DB_TYPE == 'postgresql':
        return '%s'
    return '?'


def _execute_query(query, params=None, fetch_one=False, fetch_all=False):
    """Execute a query and optionally fetch results."""
    # Convert SQLite-style ? placeholders to PostgreSQL %s if needed
    if DB_TYPE == 'postgresql' and '?' in query:
        query = query.replace('?', '%s')
    
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor()
        
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            result = None
            if fetch_one:
                row = cursor.fetchone()
                if row:
                    result = dict(row) if DB_TYPE == 'postgresql' else dict(row)
            elif fetch_all:
                rows = cursor.fetchall()
                result = [dict(row) for row in rows]
            else:
                conn.commit()
                if cursor.lastrowid:
                    result = cursor.lastrowid
                elif cursor.rowcount is not None:
                    result = cursor.rowcount
            
            return result
        finally:
            conn.close()


# --- API Key Functions ---

def get_api_key_id(key):
    """Returns the ID for a given API key, or None if not found."""
    result = _execute_query(
        'SELECT id FROM api_keys WHERE key = ?',
        (key,),
        fetch_one=True
    )
    return result['id'] if result else None


def create_api_key(key):
    """Creates a new API key and returns its ID."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                cursor.execute('INSERT INTO api_keys (key) VALUES (%s) RETURNING id', (key,))
                result = cursor.fetchone()
                conn.commit()
                conn.close()
                return result['id']
            except psycopg2.IntegrityError:
                conn.rollback()
                conn.close()
                return get_api_key_id(key)
        else:
            cursor = conn.cursor()
            try:
                cursor.execute('INSERT INTO api_keys (key) VALUES (?)', (key,))
                conn.commit()
                api_key_id = cursor.lastrowid
                conn.close()
                return api_key_id
            except sqlite3.IntegrityError:
                conn.close()
                return get_api_key_id(key)


def get_or_create_api_key(key):
    """Gets existing API key ID or creates new one."""
    api_key_id = get_api_key_id(key)
    if api_key_id is None:
        api_key_id = create_api_key(key)
    return api_key_id


# --- Account Functions ---

def add_account(api_key_id, email, password):
    """Adds an account for a specific API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT INTO accounts (api_key_id, email, password) VALUES (%s, %s, %s)',
                    (api_key_id, email, password)
                )
                conn.commit()
                conn.close()
                return True
            except psycopg2.IntegrityError:
                conn.rollback()
                conn.close()
                return False
        else:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT INTO accounts (api_key_id, email, password) VALUES (?, ?, ?)',
                    (api_key_id, email, password)
                )
                conn.commit()
                conn.close()
                return True
            except sqlite3.IntegrityError:
                conn.close()
                return False


def get_next_account(api_key_id):
    """Gets the next unused account for an API key and marks it as used."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                'SELECT id, email, password FROM accounts WHERE api_key_id = %s AND used = 0 LIMIT 1',
                (api_key_id,)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, email, password FROM accounts WHERE api_key_id = ? AND used = 0 LIMIT 1',
                (api_key_id,)
            )
        
        row = cursor.fetchone()
        if row:
            row_dict = dict(row)
            if DB_TYPE == 'postgresql':
                cursor.execute('UPDATE accounts SET used = 1 WHERE id = %s', (row_dict['id'],))
            else:
                cursor.execute('UPDATE accounts SET used = 1 WHERE id = ?', (row_dict['id'],))
            conn.commit()
            conn.close()
            return {'email': row_dict['email'], 'password': row_dict['password']}
        conn.close()
        return None


def get_account_count(api_key_id):
    """Returns the number of unused accounts for an API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                'SELECT COUNT(*) as count FROM accounts WHERE api_key_id = %s AND used = 0',
                (api_key_id,)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) as count FROM accounts WHERE api_key_id = ? AND used = 0',
                (api_key_id,)
            )
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)['count']
        return 0


def delete_account(api_key_id, email):
    """Deletes an account by email for a specific API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM accounts WHERE api_key_id = %s AND email = %s',
                (api_key_id, email)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM accounts WHERE api_key_id = ? AND email = ?',
                (api_key_id, email)
            )
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted


def get_all_accounts(api_key_id):
    """Returns all accounts for an API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                'SELECT email, used FROM accounts WHERE api_key_id = %s',
                (api_key_id,)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT email, used FROM accounts WHERE api_key_id = ?',
                (api_key_id,)
            )
        rows = cursor.fetchall()
        conn.close()
        return [{'email': dict(r)['email'], 'used': bool(dict(r)['used'])} for r in rows]


# --- Task Functions ---

def create_task(api_key_id, task_id, mode):
    """Creates a new task in the database."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO tasks (api_key_id, task_id, status, mode, logs) VALUES (%s, %s, %s, %s, %s)',
                (api_key_id, task_id, 'pending', mode, '[]')
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO tasks (api_key_id, task_id, status, mode, logs) VALUES (?, ?, ?, ?, ?)',
                (api_key_id, task_id, 'pending', mode, '[]')
            )
        conn.commit()
        conn.close()


def get_task(api_key_id, task_id):
    """Gets a task by ID for a specific API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                'SELECT status, result_url, logs, mode FROM tasks WHERE api_key_id = %s AND task_id = %s',
                (api_key_id, task_id)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT status, result_url, logs, mode FROM tasks WHERE api_key_id = ? AND task_id = ?',
                (api_key_id, task_id)
            )
        row = cursor.fetchone()
        conn.close()
        if row:
            row_dict = dict(row)
            return {
                'status': row_dict['status'],
                'result_url': row_dict['result_url'],
                'logs': json.loads(row_dict['logs']),
                'mode': row_dict['mode']
            }
        return None


def update_task_status(task_id, status, result_url=None):
    """Updates the status of a task."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor()
            if result_url:
                cursor.execute(
                    'UPDATE tasks SET status = %s, result_url = %s WHERE task_id = %s',
                    (status, result_url, task_id)
                )
            else:
                cursor.execute(
                    'UPDATE tasks SET status = %s WHERE task_id = %s',
                    (status, task_id)
                )
        else:
            cursor = conn.cursor()
            if result_url:
                cursor.execute(
                    'UPDATE tasks SET status = ?, result_url = ? WHERE task_id = ?',
                    (status, result_url, task_id)
                )
            else:
                cursor.execute(
                    'UPDATE tasks SET status = ? WHERE task_id = ?',
                    (status, task_id)
                )
        conn.commit()
        conn.close()


def add_task_log(task_id, log_message):
    """Adds a log message to a task."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT logs FROM tasks WHERE task_id = %s', (task_id,))
        else:
            cursor = conn.cursor()
            cursor.execute('SELECT logs FROM tasks WHERE task_id = ?', (task_id,))
        
        row = cursor.fetchone()
        if row:
            logs = json.loads(dict(row)['logs'])
            logs.append(log_message)
            if DB_TYPE == 'postgresql':
                cursor.execute(
                    'UPDATE tasks SET logs = %s WHERE task_id = %s',
                    (json.dumps(logs), task_id)
                )
            else:
                cursor.execute(
                    'UPDATE tasks SET logs = ? WHERE task_id = ?',
                    (json.dumps(logs), task_id)
                )
            conn.commit()
        conn.close()


def get_all_tasks(api_key_id):
    """Returns all tasks for an API key."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                'SELECT task_id, status, result_url, logs, mode FROM tasks WHERE api_key_id = %s',
                (api_key_id,)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT task_id, status, result_url, logs, mode FROM tasks WHERE api_key_id = ?',
                (api_key_id,)
            )
        rows = cursor.fetchall()
        conn.close()
        return {
            dict(row)['task_id']: {
                'status': dict(row)['status'],
                'result_url': dict(row)['result_url'],
                'logs': json.loads(dict(row)['logs']),
                'mode': dict(row)['mode']
            }
            for row in rows
        }


def get_running_task_count():
    """Returns the count of currently running tasks (across all API keys)."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT COUNT(*) as count FROM tasks WHERE status = 'running'")
        else:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM tasks WHERE status = 'running'")
        row = cursor.fetchone()
        conn.close()
        return dict(row)['count'] if row else 0


def update_task_external_data(task_id, external_task_id, token):
    """Updates external API task ID and token for recovery."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE tasks SET external_task_id = %s, token = %s WHERE task_id = %s',
                (external_task_id, token, task_id)
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE tasks SET external_task_id = ?, token = ? WHERE task_id = ?',
                (external_task_id, token, task_id)
            )
        conn.commit()
        conn.close()


def get_incomplete_tasks():
    """Returns all running/pending tasks that have external IDs to recover."""
    with db_lock:
        conn = get_connection()
        if DB_TYPE == 'postgresql':
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT task_id, mode, external_task_id, token FROM tasks WHERE (status = 'running' OR status = 'pending') AND external_task_id IS NOT NULL"
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, mode, external_task_id, token FROM tasks WHERE (status = 'running' OR status = 'pending') AND external_task_id IS NOT NULL"
            )
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

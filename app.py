from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import subprocess
import os
import re
import json
import psutil
import threading
import time
from datetime import datetime
import secrets
from pathlib import Path

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# إعداد المجلدات
BASE_DIR = Path('/app')
UPLOAD_FOLDER = BASE_DIR / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)

# تخزين حالة المهام
tasks = {}
hashcat_process = None
current_task_id = None

def get_gpu_info():
    """الحصول على معلومات GPU"""
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu', '--format=csv,noheader,nounits'], 
                              capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            gpus = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = [x.strip() for x in line.split(',')]
                    gpus.append({
                        'name': parts[0],
                        'gpu_util': int(parts[1]) if parts[1].isdigit() else 0,
                        'memory_used': int(parts[2]) if parts[2].isdigit() else 0,
                        'memory_total': int(parts[3]) if parts[3].isdigit() else 0,
                        'temperature': int(parts[4]) if parts[4].isdigit() else 0
                    })
            return gpus
    except:
        pass
    return []

def get_system_stats():
    """الحصول على إحصائيات النظام"""
    return {
        'cpu_percent': psutil.cpu_percent(interval=0.5),
        'memory_percent': psutil.virtual_memory().percent,
        'memory_used': psutil.virtual_memory().used // (1024**2),
        'memory_total': psutil.virtual_memory().total // (1024**2),
        'disk_percent': psutil.disk_usage('/').percent,
        'gpus': get_gpu_info()
    }

def parse_hashcat_output(line, task_id):
    """تحليل مخرجات hashcat"""
    task = tasks.get(task_id, {})
    
    # البحث عن كلمة المرور المكتشفة
    password_match = re.search(r'(\w+\.hc22000):(\S+)', line)
    if password_match:
        task['password'] = password_match.group(2)
        task['status'] = 'completed'
        task['completed_at'] = datetime.now().isoformat()
        socketio.emit('password_found', {'password': task['password']}, room=task_id)
    
    # البحث عن التقدم
    progress_match = re.search(r'Progress\.+:\s*(\d+)/(\d+)', line)
    if progress_match:
        current = int(progress_match.group(1))
        total = int(progress_match.group(2))
        task['progress'] = (current / total) * 100
        task['current_attempts'] = current
        task['total_attempts'] = total
        socketio.emit('progress_update', {
            'progress': task['progress'],
            'current': current,
            'total': total
        }, room=task_id)
    
    # البحث عن السرعة
    speed_match = re.search(r'Speed\.#\d+\.*:\s*([\d.]+)\s*([kMG]?H/s)', line)
    if speed_match:
        task['speed'] = f"{speed_match.group(1)} {speed_match.group(2)}"
        socketio.emit('speed_update', {'speed': task['speed']}, room=task_id)
    
    # البحث عن الوقت المتبقي
    time_match = re.search(r'Time\.Estimated\.*:\s*(.+)', line)
    if time_match:
        task['time_remaining'] = time_match.group(1).strip()
        socketio.emit('time_update', {'time': task['time_remaining']}, room=task_id)
    
    # تحديث حالة GPU
    gpu_match = re.search(r'Hardware\.Mon\.#\d+\.*:\s*Temp:\s*(\d+)c\s*Util:\s*(\d+)%', line)
    if gpu_match:
        task['gpu_temp'] = int(gpu_match.group(1))
        task['gpu_util'] = int(gpu_match.group(2))
        socketio.emit('gpu_update', {
            'temp': task['gpu_temp'],
            'util': task['gpu_util']
        }, room=task_id)
    
    tasks[task_id] = task

def run_hashcat(hashcat_file, task_id):
    """تشغيل hashcat في thread منفصل"""
    global hashcat_process, current_task_id
    
    current_task_id = task_id
    tasks[task_id] = {
        'id': task_id,
        'status': 'running',
        'progress': 0,
        'started_at': datetime.now().isoformat(),
        'password': None,
        'speed': '0 H/s',
        'time_remaining': 'calculating...'
    }
    
    try:
        # أمر hashcat
        cmd = [
            'hashcat', '-m', '22000', '-w', '4', '-a', '3',
            str(hashcat_file), '?d?d?d?d?d?d?d?d',
            '--force'  # force for non-OpenCL devices
        ]
        
        # تشغيل العملية
        hashcat_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        # قراءة المخرجات سطر بسطر
        for line in iter(hashcat_process.stdout.readline, ''):
            if line:
                parse_hashcat_output(line, task_id)
                if tasks[task_id].get('password'):
                    break
        
        hashcat_process.wait()
        
        # إذا لم يتم العثور على كلمة مرور
        if not tasks[task_id].get('password'):
            tasks[task_id]['status'] = 'failed'
            tasks[task_id]['error'] = 'Password not found in the mask range'
            
    except Exception as e:
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['error'] = str(e)
    
    finally:
        if hashcat_process:
            hashcat_process = None
        current_task_id = None
        
        # محاولة قراءة ملف .pot file إذا وجد
        pot_file = Path(f"{hashcat_file}.pot")
        if pot_file.exists():
            try:
                with open(pot_file, 'r') as f:
                    content = f.read().strip()
                    if ':' in content:
                        password = content.split(':')[-1]
                        tasks[task_id]['password'] = password
                        tasks[task_id]['status'] = 'completed'
                        socketio.emit('password_found', {'password': password}, room=task_id)
            except:
                pass
        
        socketio.emit('task_complete', tasks[task_id], room=task_id)

def save_uploaded_file(content, filename):
    """حفظ الملف المرفوع بشكل آمن"""
    # التحقق من صيغة الملف
    if not filename.endswith('.hc22000'):
        filename += '.hc22000'
    
    filepath = UPLOAD_FOLDER / filename
    
    # كتابة المحتوى
    if isinstance(content, str):
        with open(filepath, 'w') as f:
            f.write(content)
    else:
        with open(filepath, 'wb') as f:
            f.write(content)
    
    return filepath

@app.route('/')
def index():
    """الصفحة الرئيسية"""
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    """لوحة التحكم"""
    return render_template('dashboard.html')

@app.route('/logs')
def logs():
    """صفحة السجلات"""
    return render_template('logs.html')

@app.route('/api/upload', methods=['POST'])
def upload_hash():
    """رفع ملف الهاش"""
    task_id = secrets.token_hex(8)
    
    if 'file' in request.files:
        file = request.files['file']
        if file.filename:
            filepath = save_uploaded_file(file.read(), file.filename)
        else:
            return jsonify({'error': 'No file selected'}), 400
    elif 'hash_content' in request.form:
        hash_content = request.form['hash_content']
        if hash_content:
            filepath = save_uploaded_file(hash_content, f"hash_{task_id}.hc22000")
        else:
            return jsonify({'error': 'No hash content provided'}), 400
    else:
        return jsonify({'error': 'No file or hash provided'}), 400
    
    # بدء تشغيل hashcat في thread جديد
    thread = threading.Thread(target=run_hashcat, args=(filepath, task_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({'task_id': task_id, 'status': 'started'})

@app.route('/api/task/<task_id>')
def get_task_status(task_id):
    """الحصول على حالة المهمة"""
    task = tasks.get(task_id, {})
    return jsonify(task)

@app.route('/api/stats')
def get_stats():
    """الحصول على إحصائيات النظام"""
    return jsonify(get_system_stats())

@app.route('/api/stop', methods=['POST'])
def stop_task():
    """إيقاف المهمة الحالية"""
    global hashcat_process
    if hashcat_process:
        hashcat_process.terminate()
        return jsonify({'status': 'stopped'})
    return jsonify({'status': 'no_task'})

@socketio.on('connect')
def handle_connect():
    emit('connected', {'data': 'Connected'})

@socketio.on('subscribe')
def handle_subscribe(task_id):
    join_room(task_id)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

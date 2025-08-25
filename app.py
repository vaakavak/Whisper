import os
from flask import Flask, request, jsonify, render_template
from celery import Celery, Task
from celery.result import AsyncResult
import uuid

# --- App and Celery Configuration ---

# Assume Redis is running on localhost
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


class FlaskTask(Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)

celery = Celery(
    app.name,
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    task_cls=FlaskTask
)
celery.conf.update(
    task_track_started=True,
    result_extended=True # To store extra metadata
)

# Import the processing task
# We will create this file next.
from processing import process_audio_task

# --- Routes ---

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and start the processing task."""
    if 'audio_file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['audio_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        # Securely save the uploaded file
        filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Get form data
        model_name = request.form.get('model', 'small')
        start_words_str = request.form.get('start_words', '')
        stop_words_str = request.form.get('stop_words', '')

        # Start the background task
        task = process_audio_task.delay(
            filepath=filepath,
            model_name=model_name,
            start_words_str=start_words_str,
            stop_words_str=stop_words_str
        )

        return jsonify({'task_id': task.id}), 202


@app.route('/status/<task_id>')
def task_status(task_id):
    """Return the status of a background task."""
    task = AsyncResult(task_id, app=celery)

    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'progress': 0,
            'status': 'Ожидание в очереди...'
        }
    elif task.state == 'PROGRESS':
        response = {
            'state': task.state,
            'progress': task.info.get('progress', 0),
            'status': task.info.get('status', '')
        }
    elif task.state == 'SUCCESS':
        response = {
            'state': task.state,
            'progress': 100,
            'status': 'Завершено'
        }
    else: # FAILURE, REVOKED etc.
        response = {
            'state': task.state,
            'progress': 0,
            'status': str(task.info) # Exception info
        }
    return jsonify(response)


@app.route('/result/<task_id>')
def task_result(task_id):
    """Return the result of a completed task."""
    task = AsyncResult(task_id, app=celery)
    if task.state == 'SUCCESS':
        return jsonify(task.result)
    else:
        return jsonify({'error': 'Задача не завершена или не найдена'}), 404


@app.route('/cancel/<task_id>', methods=['POST'])
def cancel_task(task_id):
    """Cancel a running task."""
    celery.control.revoke(task_id, terminate=True)
    return jsonify({'message': 'Задача отменена'}), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')

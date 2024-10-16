import os
import sqlite3
import time
import uuid
import pytesseract
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pdf2image import convert_from_path
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for
import threading
from queue import Queue

# Flask app setup
app = Flask(__name__)

# Set the path to the Tesseract executable
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Path to the Poppler bin folder
poppler_path = r'C:\poppler\Library\bin'

# Database setup
db_name = 'files_data.db'
monitor_directory_path = "./file_indexer"

# Configure logging
logging.basicConfig(
    filename="file_monitor.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Cache to prevent duplicate file processing
recent_files_cache = {}
CACHE_TIMEOUT = 2  # seconds

# Queue for file processing
file_queue = Queue()

def create_db():
    try:
        conn = sqlite3.connect(db_name)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS files_data (
                id TEXT PRIMARY KEY,
                file TEXT,
                filecreationdate TEXT,
                filecontent TEXT,
                subject TEXT,
                filephysicallocation TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logging.error(f"Error initializing database: {e}")

# OCR extraction function
def extract_text_from_image(image):
    try:
        return pytesseract.image_to_string(image)
    except Exception as e:
        logging.error(f"OCR extraction failed: {e}")
        return ""

def process_file(file_path):
    file_name = os.path.basename(file_path)
    file_type = file_name.split('.')[-1].lower()
    file_creation_date = time.ctime(os.path.getctime(file_path))

    try:
        text_content = ""
        if file_type == "pdf":
            logging.info(f"Processing PDF: {file_name}")
            pages = convert_from_path(file_path, poppler_path=poppler_path)
            for page in pages:
                text_content += extract_text_from_image(page)
        elif file_type in ['jpg', 'jpeg', 'png', 'bmp', 'tiff']:
            logging.info(f"Processing image: {file_name}")
            image = Image.open(file_path)
            text_content = extract_text_from_image(image)
        else:
            logging.warning(f"Unsupported file type: {file_name}")
            return

        # Check if the file path and content already exist in the database
        conn = sqlite3.connect(db_name)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM files_data WHERE file = ? AND filecontent = ?", (os.path.abspath(file_path), text_content))
        exists = c.fetchone()[0]

        if exists > 0:
            logging.info(f"File '{file_name}' already exists in the database. Skipping insertion.")
            conn.close()
            return

        # Insert the file data into the database
        c.execute('''
            INSERT INTO files_data (id, file, filecreationdate, filecontent, subject, filephysicallocation)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4())[:8], os.path.abspath(file_path), file_creation_date, text_content, '', ''))
        conn.commit()
        conn.close()
        logging.info(f"File '{file_name}' successfully inserted into the database.")

    except sqlite3.Error as e:
        logging.error(f"Database insertion error for file '{file_name}': {e}")
    except Exception as e:
        logging.error(f"Error processing file '{file_name}': {e}")

def worker():
    while True:
        file_path = file_queue.get()
        if file_path is None:  # Exit condition for the worker
            break
        process_file(file_path)
        file_queue.task_done()

# Watchdog event handler
class WatcherHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            if file_path.lower().endswith(('.pdf', '.jpg', 'jpeg', 'png', 'bmp', 'tiff')):
                if file_path not in recent_files_cache or (time.time() - recent_files_cache[file_path] > CACHE_TIMEOUT):
                    recent_files_cache[file_path] = time.time()
                    logging.info(f"New file detected: {file_path}")
                    file_queue.put(file_path)  # Add the file path to the queue

# Directory monitoring
def monitor_directory():
    event_handler = WatcherHandler()
    observer = Observer()
    observer.schedule(event_handler, monitor_directory_path, recursive=False)
    observer.start()
    logging.info(f"Monitoring directory: {monitor_directory_path}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("Directory monitoring stopped.")
    observer.join()

# Flask route to display files
@app.route('/')
def index():
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute("SELECT * FROM files_data")
    files = c.fetchall()
    conn.close()
    return render_template('index.html', files=files)

# Route to handle updating the record
@app.route('/update/<id>', methods=['POST'])
def update(id):
    subject = request.form['subject']
    filephysicallocation = request.form['filephysicallocation']
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('''
        UPDATE files_data
        SET subject = ?, filephysicallocation = ?
        WHERE id = ?
    ''', (subject, filephysicallocation, id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

if __name__ == "__main__":
    create_db()

    # Start the worker thread
    threading.Thread(target=worker, daemon=True).start()

    # Start the monitor_directory function in a separate thread
    monitor_thread = threading.Thread(target=monitor_directory)
    monitor_thread.daemon = True
    monitor_thread.start()

    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)

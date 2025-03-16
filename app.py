from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, Response, stream_with_context, send_from_directory
import os
import subprocess
import tempfile
import uuid
import shutil
import time
import threading
import queue
import glob
import urllib.parse
import re
import zipfile
import io
import json
import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Create a downloads directory if it doesn't exist
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Store download tasks
download_tasks = {}
# Store output streams for each task
task_outputs = {}

# Function to extract album/playlist name from spotdl output
def extract_collection_name(output_lines):
    # First try to find the "Found X songs in Y (Type)" pattern
    for line in output_lines:
        found_match = re.search(r"Found \d+ songs? in (.+) \((\w+)\)", line)
        if found_match:
            name = found_match.group(1).strip()
            type_name = found_match.group(2).strip().lower()
            return {"type": type_name, "name": name}
    
    # If not found, try other patterns
    for line in output_lines:
        # Check for album pattern
        album_match = re.search(r"Album: (.+)", line)
        if album_match:
            return {"type": "album", "name": album_match.group(1).strip()}
        
        # Check for playlist pattern
        playlist_match = re.search(r"Playlist: (.+)", line)
        if playlist_match:
            return {"type": "playlist", "name": playlist_match.group(1).strip()}
        
        # Check for artist pattern
        artist_match = re.search(r"Artist: (.+)", line)
        if artist_match:
            return {"type": "artist", "name": artist_match.group(1).strip()}
    
    return None

# Function to save collection metadata to a JSON file
def save_collection_metadata(task_id, collection_info):
    task_dir = os.path.join(DOWNLOAD_FOLDER, task_id)
    metadata_file = os.path.join(task_dir, 'metadata.json')
    
    # Add timestamp if not already present
    if 'timestamp' not in collection_info:
        collection_info['timestamp'] = datetime.datetime.now().isoformat()
    
    with open(metadata_file, 'w') as f:
        json.dump(collection_info, f)

# Function to load collection metadata from a JSON file
def load_collection_metadata(task_dir):
    metadata_file = os.path.join(task_dir, 'metadata.json')
    if os.path.exists(metadata_file):
        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
                # Ensure timestamp exists
                if 'timestamp' not in metadata:
                    metadata['timestamp'] = datetime.datetime.now().isoformat()
                    # Save the updated metadata
                    with open(metadata_file, 'w') as f2:
                        json.dump(metadata, f2)
                return metadata
        except json.JSONDecodeError:
            return None
    return None

# Function to scan for all MP3 files in the downloads folder and its subdirectories
def get_all_mp3_files():
    mp3_files = []
    collections = {}
    
    # First, get MP3 files directly in the downloads folder
    for file in glob.glob(os.path.join(DOWNLOAD_FOLDER, "*.mp3")):
        mp3_files.append({
            'name': os.path.basename(file),
            'path': file,
            'direct_download': True,
            'timestamp': datetime.datetime.fromtimestamp(os.path.getmtime(file)).isoformat()
        })
    
    # Then, get MP3 files in task subdirectories
    for task_dir in glob.glob(os.path.join(DOWNLOAD_FOLDER, "*")):
        if os.path.isdir(task_dir):
            task_id = os.path.basename(task_dir)
            # Check if it's a UUID format directory (created by our app)
            try:
                uuid_obj = uuid.UUID(task_id)
                mp3_files_in_dir = []
                
                # Get all MP3 files in this directory
                for file in glob.glob(os.path.join(task_dir, "*.mp3")):
                    mp3_files_in_dir.append({
                        'name': os.path.basename(file),
                        'path': file,
                        'task_id': task_id,
                        'direct_download': False
                    })
                
                # If we have files in this directory, check for collection metadata
                if mp3_files_in_dir:
                    # First check if we have metadata in memory
                    collection_info = None
                    if task_id in download_tasks and 'collection_info' in download_tasks[task_id]:
                        collection_info = download_tasks[task_id]['collection_info']
                    else:
                        # Try to load metadata from file
                        collection_info = load_collection_metadata(task_dir)
                    
                    # Only create a collection if we have more than 1 file or explicit metadata
                    if collection_info or len(mp3_files_in_dir) > 1:
                        # If we don't have metadata but have multiple files, create a default collection
                        if not collection_info and len(mp3_files_in_dir) > 1:
                            collection_info = {
                                "type": "collection",
                                "name": f"{task_id[:8]}",
                                "timestamp": datetime.datetime.now().isoformat()
                            }
                        
                        # Add collection info to each file
                        for file_info in mp3_files_in_dir:
                            file_info['collection_info'] = collection_info
                            # Add timestamp from collection to file
                            if 'timestamp' in collection_info:
                                file_info['timestamp'] = collection_info['timestamp']
                            else:
                                file_info['timestamp'] = datetime.datetime.fromtimestamp(os.path.getmtime(file_info['path'])).isoformat()
                    else:
                        # Add timestamp to individual files
                        for file_info in mp3_files_in_dir:
                            file_info['timestamp'] = datetime.datetime.fromtimestamp(os.path.getmtime(file_info['path'])).isoformat()
                
                mp3_files.extend(mp3_files_in_dir)
            except ValueError:
                # Not a UUID, might be some other directory
                pass
    
    return mp3_files

@app.route('/')
def index():
    # Get all MP3 files in the downloads folder and its subdirectories
    mp3_files = get_all_mp3_files()
    
    # Group files by collection (album/playlist)
    collections = {}
    standalone_files = []
    
    for file in mp3_files:
        if 'collection_info' in file:
            collection_key = f"{file['collection_info']['type']}:{file['collection_info']['name']}"
            if collection_key not in collections:
                collections[collection_key] = {
                    'type': file['collection_info']['type'],
                    'name': file['collection_info']['name'],
                    'files': [],
                    'task_id': file['task_id'] if 'task_id' in file else None,
                    'timestamp': file['timestamp'] if 'timestamp' in file else datetime.datetime.now().isoformat()
                }
            collections[collection_key]['files'].append(file)
        else:
            standalone_files.append(file)
    
    # Sort collections by timestamp (newest first)
    sorted_collections = sorted(
        collections.values(), 
        key=lambda x: x['timestamp'] if 'timestamp' in x else '0', 
        reverse=True
    )
    
    # Sort standalone files by timestamp (newest first)
    sorted_standalone_files = sorted(
        standalone_files, 
        key=lambda x: x['timestamp'] if 'timestamp' in x else '0', 
        reverse=True
    )
    
    return render_template('index.html', 
                          collections=sorted_collections, 
                          standalone_files=sorted_standalone_files)

def process_download(task_id, spotify_url, task_dir, output_queue):
    """Process download in a separate thread and capture output"""
    try:
        # Run spotdl to download the content and capture output
        process = subprocess.Popen(
            ['spotdl', 'download', spotify_url, '--output', task_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Store all output lines to analyze later
        all_output_lines = []
        
        # Read output line by line
        for line in iter(process.stdout.readline, ''):
            output_queue.put(line)
            all_output_lines.append(line)
            
        # Wait for process to complete
        process.wait()
        
        # Check if process completed successfully
        if process.returncode == 0:
            # Update task with downloaded files
            files = [f for f in os.listdir(task_dir) if f.endswith('.mp3')]
            download_tasks[task_id]['files'] = files
            download_tasks[task_id]['status'] = 'completed'
            
            # Try to extract collection info from output
            collection_info = extract_collection_name(all_output_lines)
            
            # Only save collection info if we have multiple files or explicit metadata
            if collection_info or len(files) > 1:
                # If we don't have collection info but have multiple files, create a default one
                if not collection_info and len(files) > 1:
                    collection_info = {
                        "type": "collection",
                        "name": f"{task_id[:8]}",
                        "timestamp": datetime.datetime.now().isoformat()
                    }
                else:
                    # Add timestamp to collection info
                    if collection_info:
                        collection_info['timestamp'] = datetime.datetime.now().isoformat()
                
                # Save to task and to disk
                if collection_info:
                    download_tasks[task_id]['collection_info'] = collection_info
                    save_collection_metadata(task_id, collection_info)
            
            output_queue.put("DOWNLOAD_COMPLETED")
        else:
            download_tasks[task_id]['status'] = 'failed'
            download_tasks[task_id]['error'] = f"Process exited with code {process.returncode}"
            output_queue.put("DOWNLOAD_FAILED")
    except Exception as e:
        download_tasks[task_id]['status'] = 'failed'
        download_tasks[task_id]['error'] = str(e)
        output_queue.put(f"ERROR: {str(e)}")
        output_queue.put("DOWNLOAD_FAILED")

@app.route('/download', methods=['POST'])
def download():
    spotify_url = request.form.get('spotify_url')
    
    if not spotify_url:
        return jsonify({'status': 'error', 'message': 'No Spotify URL provided'}), 400
    
    # Create a unique ID for this download task
    task_id = str(uuid.uuid4())
    
    # Create a temporary directory for this download
    task_dir = os.path.join(DOWNLOAD_FOLDER, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    # Create a queue for the task output
    output_queue = queue.Queue()
    task_outputs[task_id] = output_queue
    
    # Store task info
    download_tasks[task_id] = {
        'url': spotify_url,
        'status': 'processing',
        'directory': task_dir,
        'files': [],
        'timestamp': datetime.datetime.now().isoformat()
    }
    
    # Start the download process in a separate thread
    download_thread = threading.Thread(
        target=process_download,
        args=(task_id, spotify_url, task_dir, output_queue)
    )
    download_thread.daemon = True
    download_thread.start()
    
    return jsonify({
        'status': 'processing', 
        'task_id': task_id,
        'message': 'Download started'
    })

@app.route('/stream/<task_id>')
def stream(task_id):
    """Stream the output of the download process"""
    if task_id not in download_tasks or task_id not in task_outputs:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    def generate():
        output_queue = task_outputs[task_id]
        
        # Send any existing output
        while True:
            try:
                # Non-blocking get with timeout
                line = output_queue.get(timeout=0.1)
                
                # Check for completion markers
                if line == "DOWNLOAD_COMPLETED":
                    files = download_tasks[task_id]['files']
                    collection_info = download_tasks[task_id].get('collection_info', {})
                    collection_data = ""
                    if collection_info:
                        collection_data = f",{collection_info['type']},{collection_info['name']}"
                    yield f"data: COMPLETED:{','.join(files)}{collection_data}\n\n"
                    break
                elif line == "DOWNLOAD_FAILED":
                    error = download_tasks[task_id].get('error', 'Unknown error')
                    yield f"data: FAILED:{error}\n\n"
                    break
                else:
                    yield f"data: {line}\n\n"
                    
            except queue.Empty:
                # If the queue is empty but the task is completed or failed, exit
                status = download_tasks[task_id]['status']
                if status in ['completed', 'failed']:
                    if status == 'completed':
                        files = download_tasks[task_id]['files']
                        collection_info = download_tasks[task_id].get('collection_info', {})
                        collection_data = ""
                        if collection_info:
                            collection_data = f",{collection_info['type']},{collection_info['name']}"
                        yield f"data: COMPLETED:{','.join(files)}{collection_data}\n\n"
                    else:
                        error = download_tasks[task_id].get('error', 'Unknown error')
                        yield f"data: FAILED:{error}\n\n"
                    break
                    
                # Otherwise, just continue waiting
                yield f"data: WAITING\n\n"
                time.sleep(0.5)
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/status/<task_id>')
def status(task_id):
    if task_id not in download_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    return jsonify(download_tasks[task_id])

@app.route('/files/<task_id>')
def list_files(task_id):
    if task_id not in download_tasks:
        return jsonify({'status': 'error', 'message': 'Task not found'}), 404
    
    task = download_tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({'status': 'error', 'message': 'Download not completed yet'}), 400
    
    return jsonify({'files': task['files']})

@app.route('/download/<task_id>/<path:filename>')
def download_file(task_id, filename):
    # Decode the URL-encoded filename
    filename = urllib.parse.unquote(filename)
    
    # Check if it's a direct download from the downloads folder
    if task_id == 'direct':
        file_path = os.path.join(DOWNLOAD_FOLDER, filename)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path, as_attachment=True)
        return jsonify({'status': 'error', 'message': 'File not found'}), 404
    
    # Check if it's a task-specific download
    try:
        uuid_obj = uuid.UUID(task_id)
        task_dir = os.path.join(DOWNLOAD_FOLDER, task_id)
        
        # Check if the directory exists
        if not os.path.exists(task_dir) or not os.path.isdir(task_dir):
            return jsonify({'status': 'error', 'message': 'Task directory not found'}), 404
        
        # Try direct file path first
        file_path = os.path.join(task_dir, filename)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path, as_attachment=True)
        
        # If not found, try to find a matching file
        for file in os.listdir(task_dir):
            if file.endswith('.mp3'):
                # Try different matching strategies
                if (file == filename or 
                    filename in file or 
                    file in filename or 
                    file.lower() == filename.lower()):
                    file_path = os.path.join(task_dir, file)
                    return send_file(file_path, as_attachment=True)
        
        # If we get here, no matching file was found
        return jsonify({
            'status': 'error', 
            'message': 'File not found in task directory',
            'requested_file': filename,
            'available_files': os.listdir(task_dir)
        }), 404
        
    except ValueError:
        # Not a valid UUID, could be some other format
        return jsonify({'status': 'error', 'message': 'Invalid task ID format'}), 400

@app.route('/download-all/<task_id>')
def download_all_files(task_id):
    try:
        uuid_obj = uuid.UUID(task_id)
        task_dir = os.path.join(DOWNLOAD_FOLDER, task_id)
        
        # Check if the directory exists
        if not os.path.exists(task_dir) or not os.path.isdir(task_dir):
            return jsonify({'status': 'error', 'message': 'Task directory not found'}), 404
        
        # Get all MP3 files in the directory
        mp3_files = [f for f in os.listdir(task_dir) if f.endswith('.mp3')]
        if not mp3_files:
            return jsonify({'status': 'error', 'message': 'No MP3 files found in task directory'}), 404
        
        # Get collection info if available
        collection_name = "collection"
        
        # First check if we have metadata in memory
        if task_id in download_tasks and 'collection_info' in download_tasks[task_id]:
            collection_info = download_tasks[task_id]['collection_info']
            collection_name = f"{collection_info['type']}_{collection_info['name']}"
        else:
            # Try to load metadata from file
            collection_info = load_collection_metadata(task_dir)
            if collection_info:
                collection_name = f"{collection_info['type']}_{collection_info['name']}"
        
        # Replace spaces and special characters for a valid filename
        collection_name = re.sub(r'[^\w\-_]', '_', collection_name)
        
        # Create a ZIP file in memory
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in mp3_files:
                file_path = os.path.join(task_dir, file)
                zipf.write(file_path, arcname=file)
        
        # Reset file pointer to the beginning
        memory_file.seek(0)
        
        # Send the ZIP file
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{collection_name}.zip"
        )
        
    except ValueError:
        # Not a valid UUID, could be some other format
        return jsonify({'status': 'error', 'message': 'Invalid task ID format'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error creating ZIP file: {str(e)}'}), 500

@app.route('/cleanup/<task_id>', methods=['POST'])
def cleanup(task_id):
    # Check if it's a direct file in the downloads folder
    if task_id == 'direct':
        filename = request.form.get('filename')
        if not filename:
            return jsonify({'status': 'error', 'message': 'No filename provided'}), 400
        
        file_path = os.path.join(DOWNLOAD_FOLDER, secure_filename(filename))
        if os.path.exists(file_path) and os.path.isfile(file_path):
            try:
                os.remove(file_path)
                return jsonify({'status': 'success', 'message': 'File removed successfully'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': f'Failed to remove file: {str(e)}'}), 500
        
        return jsonify({'status': 'error', 'message': 'File not found'}), 404
    
    # Otherwise it's a task directory
    try:
        uuid_obj = uuid.UUID(task_id)
        task_dir = os.path.join(DOWNLOAD_FOLDER, task_id)
        
        if not os.path.exists(task_dir) or not os.path.isdir(task_dir):
            return jsonify({'status': 'error', 'message': 'Task directory not found'}), 404
        
        try:
            shutil.rmtree(task_dir)
            if task_id in download_tasks:
                del download_tasks[task_id]
            if task_id in task_outputs:
                del task_outputs[task_id]
            return jsonify({'status': 'success', 'message': 'Files cleaned up successfully'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Cleanup failed: {str(e)}'}), 500
            
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid task ID format'}), 400

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)

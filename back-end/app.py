from flask import Flask, request, jsonify
from flask_cors import CORS # Used to handle Cross-Origin Resource Sharing
import openpyxl # Library for reading .xlsx Excel files
import PyPDF2 # Library for basic PDF text extraction (note: complex PDF parsing is harder)
import json # For handling JSON data
import os # For path manipulation

# Firebase Admin SDK imports
import firebase_admin
from firebase_admin import credentials, firestore
# In your backend/app.py or a separate script (after Firebase Admin SDK is initialized)
from firebase_admin import auth

def make_user_admin(uid):
    auth.set_custom_user_claims(uid, {'admin': True})
    print(f"User {uid} is now an admin.")

# To find the UID: Go to Firebase Console -> Authentication -> Users tab, copy the UID next to the admin email.
# Example usage (run this once for your admin user's UID):
# make_user_admin("THE_UID_OF_YOUR_ADMIN_USER_FROM_FIREBASE_CONSOLE")

# --- Firebase Admin SDK Initialization ---
# IMPORTANT: Replace 'serviceAccountKey.json' with the actual path to your downloaded Firebase Service Account Key.
# This file should be kept secure and NOT committed to public version control.
SERVICE_ACCOUNT_KEY_PATH = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')

try:
    # Initialize Firebase Admin SDK
    # This credential allows your backend to securely interact with Firebase services
    cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
    firebase_admin.initialize_app(cred)
    print("Firebase Admin SDK initialized successfully.")
except FileNotFoundError:
    print(f"Error: serviceAccountKey.json not found at {SERVICE_ACCOUNT_KEY_PATH}")
    print("Please download your Firebase Service Account Key and place it in the backend folder.")
    exit(1) # Exit if the key is not found, as the app cannot function
except ValueError as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    print("Ensure your serviceAccountKey.json is valid and the app is not already initialized.")
    exit(1)

db = firestore.client() # Get a Firestore client instance

app = Flask(__name__)
# Enable CORS for your frontend.
# IMPORTANT: In production, replace "*" with your actual frontend domain (e.g., "http://localhost:5500" or "https://your-frontend-domain.com")
CORS(app)

# --- Configuration ---
# IMPORTANT: Replace 'jvskresults' with your actual Firebase Project ID.
# This is used to construct the Firestore collection path.
app.config['APP_ID'] = 'jvskresults' # Your Firebase Project ID

# --- Helper function to parse Excel files ---
def parse_excel_file(file_stream):
    """
    Parses an Excel file stream and extracts student data.
    Assumes a specific column structure.
    Returns a list of dictionaries, each representing a student's result.
    """
    workbook = openpyxl.load_workbook(file_stream)
    sheet = workbook.active
    
    # Assuming the first row contains headers
    headers = [cell.value for cell in sheet[1]]
    
    # Define expected headers for core student data
    expected_headers = {
        'Student ID': 'studentId',
        'Name': 'name',
        'Academic Year': 'academicYear',
        'Semester': 'semester',
        'Exam Type': 'examType',
        # You might have specific columns for subject results, e.g., 'Math Marks', 'Physics Grade'
        # For simplicity, we'll look for a 'Results JSON' column as a fallback for complex results
    }
    
    student_data = []
    for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True)):
        student_entry = {}
        
        # Map Excel columns to our desired JSON keys
        for header_excel, header_json in expected_headers.items():
            try:
                col_idx = headers.index(header_excel)
                student_entry[header_json] = row[col_idx]
            except ValueError:
                print(f"Warning: Missing header '{header_excel}' in Excel file. Row {row_idx + 2} might be incomplete.")
                student_entry[header_json] = None # Or handle as error
        
        # Handle the 'results' array (subjects, marks, grades)
        # This is the most flexible way: expect a column named 'Results JSON'
        # which contains a JSON string of the results array.
        results_json_col_idx = -1
        try:
            results_json_col_idx = headers.index('Results JSON')
            results_json_str = row[results_json_col_idx]
            if results_json_str:
                try:
                    student_entry['results'] = json.loads(results_json_str)
                except json.JSONDecodeError:
                    print(f"Error: Invalid JSON in 'Results JSON' column for row {row_idx + 2}. Skipping results for this student.")
                    student_entry['results'] = []
            else:
                student_entry['results'] = []
        except ValueError:
            print(f"Warning: 'Results JSON' column not found. Assuming no detailed subject results for row {row_idx + 2}.")
            student_entry['results'] = [] # Default to empty array if column not found
        
        # Basic validation (can be expanded)
        if student_entry.get('studentId') and student_entry.get('name'):
            student_data.append(student_entry)
        else:
            print(f"Skipping row {row_idx + 2} due to missing Student ID or Name.")

    return student_data

# --- Helper function for basic PDF text extraction ---
def parse_pdf_file(file_stream):
    """
    Extracts text from a PDF file.
    NOTE: Extracting structured data from PDFs is highly complex and often requires
    advanced parsing, regex, or even AI/ML techniques. This function provides
    only basic text extraction and is NOT suitable for robust data parsing.
    """
    reader = PyPDF2.PdfReader(file_stream)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

# --- API Endpoint for File Upload ---
@app.route('/upload-results', methods=['POST'])
def upload_results():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    if file:
        filename = file.filename
        student_data = []
        
        try:
            if filename.endswith(('.xlsx', '.xls')):
                student_data = parse_excel_file(file.stream)
            elif filename.endswith('.pdf'):
                # For PDF, we only do basic text extraction as robust parsing is complex.
                # You'd need to manually process this text or use a more advanced library.
                extracted_text = parse_pdf_file(file.stream)
                return jsonify({
                    "message": "PDF uploaded. Text extracted, but structured data parsing from PDF is highly complex and not fully automated here.",
                    "extracted_text": extracted_text,
                    "note": "You would typically process this text on the backend to extract structured data before saving to Firestore."
                }), 200
            else:
                return jsonify({"error": "Unsupported file type. Please upload an Excel (.xlsx, .xls) or PDF (.pdf) file."}), 400

            if not student_data:
                return jsonify({"message": "No valid student data found in the uploaded file."}), 200

            # Save to Firestore
            collection_ref = db.collection(f"artifacts/{app.config['APP_ID']}/public/data/studentResults")
            
            # For each student entry, add it as a new document to Firestore
            for data_entry in student_data:
                # Add a timestamp for when the data was uploaded
                data_entry['uploadedAt'] = firestore.SERVER_TIMESTAMP
                collection_ref.add(data_entry) # Adds a new document with an auto-generated ID

            return jsonify({"message": f"Successfully processed {len(student_data)} student records and saved to database.", "count": len(student_data)}), 200

        except Exception as e:
            print(f"Error during file processing or Firestore write: {e}")
            return jsonify({"error": f"An error occurred during processing: {str(e)}"}), 500

# --- Root endpoint for testing backend connectivity ---
@app.route('/')
def index():
    return "Flask Backend is running! Send POST requests to /upload-results to upload files."

# --- Run the Flask app ---
if __name__ == '__main__':
    # When running locally, Flask will run on http://127.0.0.1:5000 by default.
    # Ensure this port is not blocked by other applications.
    app.run(debug=True, port=5000)

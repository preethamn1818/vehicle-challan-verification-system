import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from mangum import Mangum  # Use Mangum for AWS Lambda
from PIL import Image
from io import BytesIO
from google import genai  # Updated import
import asyncio
from typing import Dict
import uuid
from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import os
from dotenv import load_dotenv # Import dotenv

# Load environment variables from .env file
load_dotenv()

# Configure Google's Generative AI API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "YOUR_FALLBACK_API_KEY") # Load from environment variable
if GOOGLE_API_KEY == "YOUR_FALLBACK_API_KEY":
    print("Warning: GOOGLE_API_KEY environment variable not set. Using fallback or relying on implicit env var detection by the library.")

# Initialize Google Generative AI client
client = genai.Client(api_key=GOOGLE_API_KEY)

# Target website
CHALLAN_URL = "https://echallan.tspolice.gov.in/publicview/"

# --- Session Management ---
# Dictionary to store active sessions with their drivers and data
active_sessions: Dict[str, Dict] = {}

# --- Helper Functions ---

# Selenium setup needs to be adapted for Lambda environment
# Using headless mode is essential
def create_driver():
    """Creates and returns a configured Chrome WebDriver for Lambda."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") # Use new headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-extensions")
    # chrome_options.add_argument("--remote-debugging-port=9222") # May or may not be needed - Removed for testing
    # If webdriver-manager struggles in Lambda, specify the path directly
    # Example: service = Service('/opt/chromedriver') # Path in Lambda layer
    # Example: chrome_options.binary_location = '/opt/chrome/chrome' # Path in Lambda layer

    # For local testing with webdriver-manager:
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f"Failed to init driver with webdriver-manager: {e}")
        # Add fallback or specific paths for Lambda if needed
        # For Lambda, you'll likely need a layer with ChromeDriver and Headless Chromium
        # service = Service('/path/to/chromedriver/in/lambda')
        # chrome_options.binary_location = '/path/to/chrome/in/lambda'
        # driver = webdriver.Chrome(service=service, options=chrome_options)
        raise RuntimeError("Failed to initialize Selenium WebDriver.") from e

    driver.set_page_load_timeout(30) # Increase timeout slightly
    return driver


# --- Session Cleanup ---
# Optional: Implement a mechanism if long-running sessions need cleanup,
# but Lambda's ephemeral nature makes it less critical than a persistent server.
# A scheduled CloudWatch event could trigger a cleanup Lambda if necessary.

# Create FastAPI app
app = FastAPI(title="Vehicle Challan API - Lambda")

# --- Template Engine Setup ---
templates = Jinja2Templates(directory="templates")

# --- Add Root Endpoint to serve index.html ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the index.html page."""
    return templates.TemplateResponse("index.html", {"request": request})

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development - replace with specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper Functions --- (Keep existing helpers like extract_text_from_image, etc.)

def extract_text_from_image(image_bytes: bytes) -> str:
    """Uses Google's Generative AI to extract text from an image."""
    try:
        # Create a temporary file and convert to PIL Image
        image = Image.open(BytesIO(image_bytes))
        
        # Create the prompt
        prompt = """
        Extract any vehicle registration number from this image.
        The number should be in the format of Indian vehicle registration (e.g., AB12CD3456).
        Only provide the vehicle registration number, nothing else with out space.
        """

        # Generate content directly with the image
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(image_bytes).decode('utf-8')
                        }}
                    ]
                }
            ]
        )

        # Extract the text from the response
        extracted_text = response.text.strip()

        return extracted_text
    except Exception as e:
        print(f"Error during Google Generative AI processing: {e}")
        # Consider more specific error handling or logging
        return ""


async def get_captcha(session_id: str):
    """Initializes driver, navigates to URL, and extracts captcha for a session."""
    if session_id not in active_sessions or 'driver' not in active_sessions[session_id]:
         raise HTTPException(status_code=404, detail="Session not found or driver not initialized")

    driver = active_sessions[session_id]['driver']
    session_data = active_sessions[session_id]

    try:
        # Navigate to the challan URL
        driver.get(CHALLAN_URL)
        # driver.save_screenshot("1.png")

        # Wait for the page to load
        WebDriverWait(driver, 15).until( # Slightly longer wait
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Store the cookies from Selenium session
        selenium_cookies = driver.get_cookies()
        session_data['cookies'] = {cookie['name']: cookie['value'] for cookie in selenium_cookies}

        # Store base URL
        session_data['base_url'] = driver.current_url

        # Look for the captcha image
        captcha_element = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "captchaDivtab1"))
        )

        # Take a screenshot of the captcha element
        captcha_screenshot = captcha_element.screenshot_as_png
        captcha_base64 = base64.b64encode(captcha_screenshot).decode('utf-8')

        # Simplified frame handling - assume no frame initially
        session_data['frame_index'] = None

        # Extract form fields
        session_data['hidden_fields'] = {}
        hidden_inputs = driver.find_elements(By.XPATH, "//input[@type='hidden']")
        for hidden in hidden_inputs:
            name = hidden.get_attribute('name')
            value = hidden.get_attribute('value')
            if name:
                session_data['hidden_fields'][name] = value if value else ""

        # Get form action
        try:
            form_tag = driver.find_element(By.TAG_NAME, 'form') # Assuming one form relevant
            action = form_tag.get_attribute('action')
            if action:
                session_data['form_action'] = action
        except Exception:
            session_data['form_action'] = None # Handle cases where form/action isn't found


        return captcha_base64
    except Exception as e:
        print(f"Error getting captcha for session {session_id}: {e}")
        # Clean up driver on error? Depends on desired retry logic
        # close_session_sync(session_id) # Example cleanup
        raise HTTPException(status_code=500, detail=f"Failed to retrieve captcha: {e}")


async def process_challan_submission(session_id: str, vehicle_number: str, captcha_solution: str):
    """Process challan submission and extract results for a session."""
    if session_id not in active_sessions or 'driver' not in active_sessions[session_id]:
         raise HTTPException(status_code=404, detail="Session not found or driver not initialized")

    driver = active_sessions[session_id]['driver']
    session_data = active_sessions[session_id]

    try:
        # Store vehicle number in session data if needed later
        session_data['vehicle_number'] = vehicle_number

        # Check if in frame and switch if necessary (basic check)
        if session_data.get('frame_index') is not None:
            try:
                driver.switch_to.frame(session_data['frame_index'])
            except Exception as e:
                print(f"Warning: Failed to switch back to frame {session_data['frame_index']}: {e}")
                driver.switch_to.default_content() # Try to recover

        # Fill in the vehicle registration number
        vehicle_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "REG_NO"))
        )
        vehicle_input.clear()
        vehicle_input.send_keys(vehicle_number)

        # Fill in the captcha solution
        captcha_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "captchatab1"))
        )
        captcha_input.clear()
        captcha_input.send_keys(captcha_solution)

        # Submit the form
        submit_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "tab1btn"))
        )
        driver.execute_script("arguments[0].click();", submit_button)

        # Wait for any loading indicators to disappear
        try:
            WebDriverWait(driver, 10).until_not(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".loading, .spinner, #loading"))
            )
        except:
            pass

        results = {"status": "unknown", "message": "", "data": None}

        # First check for captcha error
        try:
            error_element_captcha = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Please Enter Correct Captcha')]"))
            )
            results["status"] = "error"
            results["message"] = "Invalid captcha entered. Please try again."
            return results
        except:
            pass  # No captcha error, continue

        # Then check for no pending challans
        try:
            no_challan_text = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'No Pending') and contains(text(), 'Challans')]"))
            )
            results["status"] = "success"
            results["message"] = "No pending challans found for this vehicle."
            results["data"] = {
                "vehicle_info": {"vehicle_number": vehicle_number},
                "challans": []
            }
            return results
        except:
            pass  # No "No Pending Challans" message, continue to check for challan table

        # Finally check for challan table
        try:
            table = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "rtable"))
            )
            
            # Extract vehicle info from the first row
            vehicle_info = {}
            try:
                vehicle_rows = table.find_elements(By.XPATH, ".//tr[position()=1]")
                if vehicle_rows:
                    vehicle_row = vehicle_rows[0]
                    vehicle_info = {
                        "vehicle_number": vehicle_row.find_element(By.XPATH, ".//div[contains(text(), 'TS')]").text.strip(),
                        "owner_name": vehicle_row.find_element(By.XPATH, ".//div[contains(text(), 'CHITKULA') or contains(text(), 'REDDY')]").text.strip()
                    }
            except Exception as e:
                print(f"Warning: Could not extract vehicle/owner info: {e}")
                vehicle_info = {"vehicle_number": vehicle_number}

            # Find all challan rows
            rows = table.find_elements(By.XPATH, ".//tr[.//input[@type='checkbox' and @id='manualErr']]")
            challans = []
            
            for row in rows:
                try:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 16:
                        violation_cell = cells[8]
                        violation_table = violation_cell.find_element(By.TAG_NAME, "table")
                        violation_text = violation_table.find_element(By.XPATH, ".//td[1]").text.strip()
                        
                        challan = {
                            "sno": cells[0].text.strip(),
                            "unit_name": cells[2].text.strip(),
                            "echallan_no": cells[3].text.strip(),
                            "date": cells[4].text.strip(),
                            "time": cells[5].text.strip(),
                            "place": cells[6].find_element(By.XPATH, ".//div").text.strip(),
                            "ps_limits": cells[7].find_element(By.XPATH, ".//div").text.strip(),
                            "violation": violation_text,
                            "fine_amount": cells[12].text.strip(),
                            "user_charges": cells[13].text.strip(),
                            "total_fine": cells[14].text.strip(),
                            "has_image": "Click For Image" in cells[15].text
                        }
                        challans.append(challan)
                except Exception as row_error:
                    print(f"Error processing row: {row_error}")
                    continue

            # Extract grand total
            try:
                total_row = table.find_element(By.XPATH, ".//tr[.//strong[contains(text(), 'Grand Total')]]")
                total_cells = total_row.find_elements(By.TAG_NAME, "td")
                grand_total = {
                    "fine_amount": total_cells[-4].text.strip(),
                    "user_charges": total_cells[-3].text.strip(),
                    "total_fine": total_cells[-2].text.strip()
                }
            except Exception as total_error:
                print(f"Error extracting grand total: {total_error}")
                grand_total = None

            results["status"] = "success"
            results["message"] = "Challans found."
            results["data"] = {
                "vehicle_info": vehicle_info,
                "challans": challans,
                "grand_total": grand_total
            }
            return results

        except Exception as table_error:
            print(f"Error finding or processing challan table: {table_error}")
            # If we get here, we couldn't find any of the expected elements
            results["status"] = "error"
            results["message"] = "Could not determine challan status. Please try again."
            return results

    except Exception as e:
        print(f"Error processing challan submission for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed during challan submission: {e}")


# --- Synchronous close helper (can be called from sync contexts if needed) ---
def close_session_sync(session_id: str):
    if session_id in active_sessions:
        print(f"Closing session: {session_id}")
        session_info = active_sessions.pop(session_id)
        if 'driver' in session_info:
            try:
                session_info['driver'].quit()
            except Exception as e:
                print(f"Error closing driver for session {session_id}: {e}")
        return True
    return False

# --- API Endpoints ---

@app.post("/start-session")
async def start_session():
    """Starts a new Selenium session and returns a session ID."""
    session_id = str(uuid.uuid4())
    try:
        driver = create_driver()
        active_sessions[session_id] = {"driver": driver}
        print(f"Session created: {session_id}")
        return JSONResponse(content={
            "session_id": session_id,
            "status": "session_started"
        })
    except Exception as e:
        print(f"Error creating session: {e}")
        # Clean up if driver was partially created but failed before storing
        if session_id in active_sessions and 'driver' in active_sessions[session_id]:
             active_sessions[session_id]['driver'].quit()
             del active_sessions[session_id]
        raise HTTPException(status_code=500, detail=f"Failed to start session: {e}")


@app.post("/process-vehicle/{session_id}")
async def process_vehicle(session_id: str, request: Request):
    """Processes an image upload or direct vehicle number for a session."""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    form_data = await request.form()
    image_file = form_data.get("image")
    vehicle_number_direct = form_data.get("vehicle_number")
    vehicle_number = ""

    if image_file:
        if hasattr(image_file, "file"):
            image_bytes = await image_file.read()
            vehicle_number = extract_text_from_image(image_bytes)
            if not vehicle_number:
                 raise HTTPException(status_code=400, detail="Could not extract vehicle number from image.")
        else:
            raise HTTPException(status_code=400, detail="Invalid image file.")
    elif vehicle_number_direct:
        vehicle_number = vehicle_number_direct.strip().upper()
    else:
        raise HTTPException(status_code=400, detail="No image or vehicle number provided.")

    active_sessions[session_id]['vehicle_number_input'] = vehicle_number # Store the number used

    # After successful vehicle number processing, get the captcha
    try:
        captcha_b64 = await get_captcha(session_id)
        return JSONResponse(content={
            "session_id": session_id,
            "status": "vehicle_processed",
            "vehicle_number": vehicle_number,
            "captcha_image": captcha_b64
        })
    except Exception as e:
        print(f"Error getting captcha after vehicle processing: {e}")
        raise HTTPException(status_code=500, detail=f"Vehicle number processed but failed to get captcha: {e}")


@app.post("/submit-challan/{session_id}")
async def submit_challan(session_id: str, request: Request):
    """Submits the vehicle number and captcha solution."""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    if 'vehicle_number_input' not in active_sessions[session_id]:
         raise HTTPException(status_code=400, detail="Vehicle number not processed yet for this session.")

    form_data = await request.form()
    captcha_solution = form_data.get("captcha")
    if not captcha_solution:
        raise HTTPException(status_code=400, detail="Captcha solution is required.")

    vehicle_number = active_sessions[session_id]['vehicle_number_input']

    try:
        result = await process_challan_submission(session_id, vehicle_number, captcha_solution)
        return JSONResponse(content=result)
    except HTTPException as e:
        # Re-raise HTTP exceptions from underlying functions
        raise e
    except Exception as e:
        print(f"Unexpected error during challan submission for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")


@app.post("/refresh-captcha/{session_id}")
async def refresh_captcha(session_id: str):
     """Gets a new captcha image for the session."""
     if session_id not in active_sessions:
         raise HTTPException(status_code=404, detail="Session not found")
     try:
        # Re-fetch the captcha page using the existing driver
        # Note: This assumes the site provides a new captcha on page reload or via a specific refresh button.
        # If there's a refresh button, clicking it would be better.
        captcha_b64 = await get_captcha(session_id) # Re-calls the get_captcha logic
        return JSONResponse(content={"captcha_image": captcha_b64})
     except HTTPException as e:
         raise e # Propagate errors from get_captcha
     except Exception as e:
         print(f"Error refreshing captcha for session {session_id}: {e}")
         raise HTTPException(status_code=500, detail=f"Failed to refresh captcha: {e}")


@app.post("/end-session/{session_id}")
async def end_session(session_id: str):
    """Closes the Selenium driver and cleans up the session."""
    if close_session_sync(session_id):
        return JSONResponse(content={"status": "session_closed", "session_id": session_id})
    else:
        raise HTTPException(status_code=404, detail="Session not found")


# Mangum handler for AWS Lambda
handler = Mangum(app, lifespan="off") # lifespan='off' might be needed for some background tasks/cleanup


# --- Optional: Local Development Startup ---
if __name__ == "__main__":
    import uvicorn
    # Note: Running locally might have issues if GOOGLE_API_KEY isn't set as an env var
    print("Starting Uvicorn server locally...")
    uvicorn.run(app, host="127.0.0.1", port=8000)

    # Add a cleanup hook for local development if needed
    import atexit
    def cleanup_all_sessions():
        print("Cleaning up all active Selenium sessions...")
        session_ids = list(active_sessions.keys())
        for session_id in session_ids:
            close_session_sync(session_id)
        print("Cleanup complete.")
    atexit.register(cleanup_all_sessions)

# Remove WebSocket related code:
# - Removed WebSocket imports
# - Removed ConnectionManager class
# - Removed websocket_endpoint function
# - Removed startup/shutdown events related to websocket cleanup (lifespan replaced)

# Changes for Lambda:
# - Added Mangum for wrapping the FastAPI app.
# - Modified create_driver for potential Lambda environment (headless, paths).
# - Removed lifespan context manager (less relevant for Lambda's stateless nature).
# - Changed session management to rely on function calls, storing state in active_sessions dict.
# - Added /start-session, /process-vehicle, /submit-challan, /refresh-captcha, /end-session HTTP endpoints.
# - Error handling uses HTTPException.
# - Added basic local run block with `if __name__ == "__main__":`.
# - Updated Google GenAI initialization.
# - Added synchronous session close helper.

# Note: The handler object is what AWS Lambda will invoke.
# The uvicorn command is only for local testing.
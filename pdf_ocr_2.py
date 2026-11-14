# <<< Start of the script (Imports, Config, Setup - UNCHANGED unless specified) >>>
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext
from app_storage import data_file
from dependency_paths import find_tool, poppler_directory
from pathlib import Path
import subprocess
import glob
import hashlib
import shutil
import sys
import json
import tempfile
import logging
import re # For parsing page ranges
from typing import List, Dict, Optional, Tuple, Any, Callable, Set

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # Not running as a bundled app (e.g., during development)
        # Assume the resource is relative to the script file
        base_path = os.path.dirname(os.path.abspath(__file__)) # Or os.getcwd() depending on structure

    return os.path.join(base_path, relative_path)

# Define the expected destination folder name from the spec file
# ** IMPORTANT: This MUST match the destination folder name in your .spec file's datas tuple **
tkdnd_rel_path = 'tkdnd_lib' # Or 'tkdnd_windows' etc. depending on what you used

# Construct the absolute path to the bundled tkdnd library
tkdnd_path = get_resource_path(tkdnd_rel_path)

# Check if the path exists before attempting to add it
if os.path.exists(tkdnd_path):
    print(f"Attempting to add tkdnd path: {tkdnd_path}") # Debug print
    try:
        # Create a temporary root window to access Tcl interpreter early
        # This is a bit of a hack but often necessary
        temp_root = tk.Tk()
        temp_root.withdraw() # Hide the window

        # Prepend the tkdnd library path to the Tcl auto_path
        # This tells Tcl where to look for packages like tkdnd
        command = f'lappend auto_path {{{tkdnd_path}}}' # Use braces for paths with spaces
        print(f"Executing Tcl command: {command}") # Debug print
        temp_root.tk.eval(command)

        temp_root.destroy() # Clean up the temporary window
        print("Successfully prepended tkdnd path to Tcl auto_path.") # Debug print
    except Exception as e:
        print(f"Error modifying Tcl auto_path: {e}") # Debug print
        # Log this error if logging is set up, or show a warning popup
        # messagebox.showwarning("TkDnD Setup Warning", f"Could not configure Tcl path for Drag & Drop: {e}")
else:
     print(f"Warning: Bundled tkdnd path not found at expected location: {tkdnd_path}") # Debug print
     # Log this warning

# --- Third-party Libraries ---
try:
    from PIL import Image, ImageTk
except ImportError:
    print("ERROR: Pillow library not found. Please run 'pip install Pillow'")
    sys.exit(1)

try:
    import pytesseract
except ImportError:
    print("ERROR: pytesseract library not found. Please run 'pip install pytesseract'")
    sys.exit(1)

try:
    from pdf2image import convert_from_path
    import pdf2image.exceptions
except ImportError:
    print("ERROR: pdf2image library not found. Please run 'pip install pdf2image'")
    sys.exit(1)

try:
    from PyPDF2 import PdfReader
    import PyPDF2.errors # Import the errors module itself
except ImportError:
    print("ERROR: PyPDF2 library not found. Please run 'pip install PyPDF2'")
    sys.exit(1)

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.utils import ImageReader
    import reportlab.lib.utils
except ImportError:
    print("ERROR: reportlab library not found. Please run 'pip install reportlab'")
    sys.exit(1)

try:
    import cv2 # OpenCV for preprocessing
    import numpy as np
except ImportError:
    print("WARNING: OpenCV library not found (pip install opencv-python). Image preprocessing options will be disabled.")
    cv2 = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    print("WARNING: tkinterdnd2 library not found (pip install tkinterdnd2). Drag and drop will not work.")
    TkinterDnD = None


# --- Configuration ---
DEFAULT_OCR_LANGUAGES = ['eng', 'ara', 'eng+ara']
DEFAULT_CONVERSION_DPI = 300
DEFAULT_GS_COMPRESSION_SETTING = "/ebook"
LOG_FILENAME = "pdf_ocr_tool.log"
CONFIG_FILENAME = "pdf_ocr_config.json"
APP_NAME = "BME PDF OCR Assistant (Enhanced)"

# Status Update Constants
STATUS_CONVERTING = "Converting PDF to images..."
STATUS_OCR = "Performing OCR page {current}/{total}..."
STATUS_CREATING_PDF = "Creating searchable PDF..."
STATUS_COMPRESSING = "Compressing PDF..."
STATUS_WRITING_TEXT = "Writing text file..."
STATUS_PREPROCESSING = "Preprocessing page {current}/{total}..."
STATUS_SKIPPED = "Skipped (already searchable)."
STATUS_FAILED = "Failed."
STATUS_CANCELLED = "Cancelled."
STATUS_DONE = "Done."
STATUS_WAITING = "Waiting to start..."
STATUS_LOADING_CACHE = "Loading images from cache..."
STATUS_CHECKING_PDF = "Checking PDF info..." # Added specific status

# --- Setup Paths and Logging ---
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BASE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = APP_DIR

TESSERACT_PATH = find_tool('TESSERACT_PATH',
    [os.path.join(BASE_DIR, 'Tesseract-OCR', 'tesseract.exe')], ['tesseract'])
GHOSTSCRIPT_PATH = find_tool('GHOSTSCRIPT_PATH',
    [os.path.join(BASE_DIR, 'Ghostscript', 'bin', 'gswin64c.exe')]
    + list(Path(BASE_DIR).glob('gs/*/bin/gswin64c.exe')),
    ['gswin64c', 'gswin32c', 'gs'])
CONFIG_FILE_PATH = data_file('BMEPdfOCR', CONFIG_FILENAME, [APP_DIR])
DATA_DIR = os.path.dirname(CONFIG_FILE_PATH)

try:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
except Exception as e:
    print(f"Warning: Could not set Tesseract path: {e}")

CACHE_DIR = os.path.join(DATA_DIR, ".cache")
LOG_FILE_PATH = data_file('BMEPdfOCR', LOG_FILENAME, [APP_DIR])
os.makedirs(CACHE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Utility Functions (load_config, save_config, parse_page_range, check_dependencies, get_file_hash - UNCHANGED) ---
def load_config() -> Dict:
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"Loaded configuration from {CONFIG_FILE_PATH}")
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load config file {CONFIG_FILE_PATH}: {e}")
            return {}
    return {}

def save_config(config: Dict):
    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        logger.info(f"Saved configuration to {CONFIG_FILE_PATH}")
    except IOError as e:
        logger.error(f"Failed to save config file {CONFIG_FILE_PATH}: {e}")

def parse_page_range(range_str: str, max_pages: int) -> Optional[Set[int]]:
    if not range_str.strip(): return None
    indices = set()
    parts = range_str.split(',')
    try:
        for part in parts:
            part = part.strip()
            if not part: continue
            if '-' in part:
                start, end = part.split('-', 1)
                start = int(start.strip()) if start.strip() else 1
                end = int(end.strip()) if end.strip() else max_pages
                if start < 1 or end > max_pages or start > end: raise ValueError(f"Invalid range part: {part}")
                indices.update(range(start - 1, end))
            else:
                page = int(part)
                if page < 1 or page > max_pages: raise ValueError(f"Page number out of range: {page}")
                indices.add(page - 1)
        if not indices: return None
        return indices
    except ValueError as e:
        logger.error(f"Invalid page range format: '{range_str}'. Error: {e}")
        raise ValueError(f"Invalid page range format: '{range_str}'. Use numbers, commas, and hyphens (e.g., '1-5, 8, 10-12').") from e

def check_dependencies():
    missing = []
    if not os.path.exists(TESSERACT_PATH): missing.append(f"Tesseract OCR not found at: {TESSERACT_PATH}")
    poppler = poppler_directory(BASE_DIR)
    for command in ('pdfinfo', 'pdftoppm'):
        executable = os.path.join(poppler, command + ('.exe' if os.name == 'nt' else '')) if poppler else shutil.which(command)
        if not executable or not os.path.isfile(executable):
            missing.append(f"Poppler {command} missing. Install Poppler or set POPPLER_PATH.")
    if not os.path.exists(GHOSTSCRIPT_PATH): missing.append(f"Ghostscript (optional for compression) not found at: {GHOSTSCRIPT_PATH}")
    return missing

def get_file_hash(file_path: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while True:
                buf = f.read(65536)
                if not buf: break
                hasher.update(buf)
        return hasher.hexdigest()
    except IOError as e:
        logger.error(f"Error reading file {file_path} for hashing: {e}")
        raise

# --- Image Preprocessing (preprocess_image - UNCHANGED) ---
def preprocess_image(image: Image.Image, grayscale: bool, threshold: bool) -> Image.Image:
    if not cv2: return image
    if not (grayscale or threshold): return image
    try:
        open_cv_image = np.array(image.convert('RGB'))[:, :, ::-1].copy()
        processed_image = open_cv_image
        if grayscale: processed_image = cv2.cvtColor(processed_image, cv2.COLOR_BGR2GRAY)
        if threshold:
            if len(processed_image.shape) == 2:
                _, processed_image = cv2.threshold(processed_image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            else:
                gray = cv2.cvtColor(processed_image, cv2.COLOR_BGR2GRAY)
                _, processed_image = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if len(processed_image.shape) == 2: return Image.fromarray(processed_image)
        else: return Image.fromarray(cv2.cvtColor(processed_image, cv2.COLOR_BGR2RGB))
    except Exception as e:
        logger.error(f"Error during image preprocessing: {e}", exc_info=True)
        return image

# --- Core Processing Functions ---

# **MODIFIED pdf_to_images** (Removed internal update_status calls, fixed encryption handling)
def pdf_to_images(pdf_path: str, dpi: int = DEFAULT_CONVERSION_DPI) -> Tuple[List[Image.Image], str, int]:
    """
    Converts PDF pages to images, using cache if available.
    Returns a list of PIL Images, the file hash, and total page count.
    Raises RuntimeError for password errors or other conversion failures.
    """
    logger.info(f"Starting image conversion for: {pdf_path}")
    num_pages = -1
    try:
        # 1. Get File Hash and Cache Path
        file_hash = get_file_hash(pdf_path)
        cache_dir = os.path.join(CACHE_DIR, file_hash)

        # 2. Get Page Count & Handle Encryption
        try:
            reader = PdfReader(pdf_path, strict=False)
            if reader.is_encrypted:
                 logger.info(f"PDF is encrypted: {pdf_path}. Attempting decrypt with empty password.")
                 try:
                      decrypt_result = reader.decrypt("")
                      # Check decryption result - varies by PyPDF2 version
                      # Older versions might return PasswordType enum, newer bool/int
                      # Let's assume failure if result is False or 0
                      if not decrypt_result:
                          logger.warning(f"Failed to decrypt PDF with empty password: {pdf_path}")
                          raise RuntimeError("PDF is password protected and cannot be decrypted with empty password.")
                      logger.info("PDF decrypted successfully with empty password.")
                 except NotImplementedError: # Some encrypted PDFs might not support decrypt this way
                     logger.warning(f"Decryption not supported for this PDF type: {pdf_path}")
                     raise RuntimeError("PDF encryption type not supported by PyPDF2 decrypt.")
                 except Exception as decrypt_err: # Catch other decryption errors
                      logger.warning(f"Error during decryption attempt for {pdf_path}: {decrypt_err}")
                      raise RuntimeError(f"Failed to decrypt PDF: {decrypt_err}")

            num_pages = len(reader.pages)
            logger.info(f"PDF has {num_pages} pages.")
        except PyPDF2.errors.PdfReadError as e: # Catch specific PyPDF2 read errors
             logger.error(f"PyPDF2 error reading PDF metadata for {pdf_path}: {e}")
             # Check if it's likely an encryption issue without a clear decrypt failure
             if "encrypted" in str(e).lower():
                 raise RuntimeError(f"Failed to read encrypted PDF (or requires non-empty password): {e}") from e
             else:
                 raise RuntimeError(f"Error reading PDF file structure: {e}") from e
        except Exception as e: # Catch other metadata reading errors
            logger.warning(f"Unexpected error reading PDF metadata for {pdf_path}: {e}")
            # Let pdf2image try, but caching check might be unreliable

        # 3. Check Cache
        image_paths = []
        if num_pages > 0:
            image_paths = [os.path.join(cache_dir, f"page_{i + 1}.png") for i in range(num_pages)]

        if os.path.exists(cache_dir) and num_pages > 0 and all(os.path.exists(img_path) for img_path in image_paths):
            logger.info(f"Loading {num_pages} images from cache for hash: {file_hash}")
            try:
                 images = [Image.open(img_path) for img_path in image_paths]
                 logger.info(f"Successfully loaded {len(images)} images from cache.")
                 return images, file_hash, num_pages
            except Exception as e:
                 logger.warning(f"Failed to load images from cache ({cache_dir}): {e}. Re-converting.")

        # 4. Convert using pdf2image
        logger.info(f"Cache miss or incomplete. Converting PDF to images (DPI={dpi}).")
        poppler_path = poppler_directory(BASE_DIR)

        images = convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path, strict=True)

        if not images: raise RuntimeError("pdf2image returned no images.")
        if num_pages == -1: num_pages = len(images)

        # 5. Save to Cache
        logger.info(f"Converted {len(images)} pages. Saving to cache: {cache_dir}")
        os.makedirs(cache_dir, exist_ok=True)
        for i, image in enumerate(images):
            image_path = os.path.join(cache_dir, f"page_{i + 1}.png")
            try:
                if image.mode == 'CMYK': image = image.convert('RGB')
                image.save(image_path, "PNG")
            except Exception as e:
                 logger.error(f"Failed to save image {image_path} to cache: {e}", exc_info=True)

        logger.info(f"Finished image conversion and caching for: {pdf_path}")
        return images, file_hash, num_pages

    # --- Specific Exception Handling ---
    except RuntimeError as e: # Catch our specific runtime errors (like passwords)
         logger.error(f"PDF Processing Error for {pdf_path}: {e}")
         raise # Re-raise RuntimeError
    except pdf2image.exceptions.PDFInfoNotInstalledError:
         logger.critical("Poppler 'pdfinfo' utility not found.")
         raise RuntimeError("Poppler not found or not in system PATH. Please install Poppler.")
    except pdf2image.exceptions.PDFPageCountError:
         logger.error(f"pdf2image count error for PDF: {pdf_path}.")
         raise RuntimeError(f"Error getting page count via pdf2image: {os.path.basename(pdf_path)}. Check if valid.")
    except FileNotFoundError:
        logger.error(f"Input PDF file not found: {pdf_path}")
        raise
    except Exception as e: # Catch any other conversion error
        logger.error(f"Unexpected error converting PDF to images ({pdf_path}): {e}", exc_info=True)
        raise RuntimeError(f"Failed to convert PDF to images: {e}")


# --- (ocr_image_to_data - UNCHANGED from previous version) ---
def ocr_image_to_data(image: Image.Image, page_number: int, file_hash: str, lang: str = 'eng',
                       preprocess_opts: Dict = None, update_status: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    logger.debug(f"Starting OCR for page {page_number} of hash {file_hash}")
    preprocess_opts = preprocess_opts or {}
    try:
        processed_image = image
        if preprocess_opts.get('grayscale') or preprocess_opts.get('threshold'):
            if update_status: update_status(f"Preprocessing page {page_number}...")
            processed_image = preprocess_image(image, grayscale=preprocess_opts.get('grayscale', False), threshold=preprocess_opts.get('threshold', False))

        cache_dir = os.path.join(CACHE_DIR, file_hash)
        ocr_cache_path = os.path.join(cache_dir, f"ocr_page_{page_number}_{lang}.json")

        if os.path.exists(ocr_cache_path):
            logger.debug(f"Loading OCR data from cache: {ocr_cache_path}")
            try:
                with open(ocr_cache_path, "r", encoding="utf-8") as f: data = json.load(f)
                return data
            except Exception as e: logger.warning(f"Error reading cached OCR data {ocr_cache_path}: {e}. Re-running OCR.")

        if update_status: update_status(f"OCR page {page_number}...")
        logger.debug(f"Performing OCR (Lang={lang}) for page {page_number}.")
        data = pytesseract.image_to_data(processed_image, lang=lang, output_type=pytesseract.Output.DICT)

        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(ocr_cache_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e: logger.error(f"Failed to save OCR data {ocr_cache_path} to cache: {e}", exc_info=True)
        return data
    except pytesseract.TesseractNotFoundError:
        raise RuntimeError(f"Tesseract OCR engine not found. Expected path: {TESSERACT_PATH}")
    except pytesseract.TesseractError as e:
         raise RuntimeError(f"Tesseract failed on page {page_number}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error processing page {page_number}: {e}")

# **MODIFIED is_pdf_searchable** (Fixed encryption handling)
def is_pdf_searchable(pdf_path: str) -> bool:
    logger.info(f"Checking if PDF is searchable: {pdf_path}")
    try:
        reader = PdfReader(pdf_path, strict=False)
        if reader.is_encrypted:
             try:
                  # Try decrypting with empty password; if it fails, assume not searchable
                  if not reader.decrypt(""):
                      logger.warning(f"PDF is encrypted and requires password: {pdf_path}")
                      return False
             except Exception: # Includes NotImplementedError or other decrypt issues
                  logger.warning(f"Cannot determine searchable status for encrypted PDF: {pdf_path}")
                  return False # Treat as not searchable if decryption fails/isn't supported

        total_text = ""
        pages_to_check = list(range(min(len(reader.pages), 3)))
        if len(reader.pages) > 3: pages_to_check.append(len(reader.pages) - 1)
        for i in pages_to_check:
            try:
                extracted = reader.pages[i].extract_text()
                if extracted and extracted.strip(): total_text += extracted
            except Exception: continue
            if len(total_text.strip()) > 50: return True

        searchable = len(total_text.strip()) > 0
        logger.info(f"PDF searchable status ({pdf_path}): {searchable}")
        return searchable
    except PyPDF2.errors.PdfReadError as e:
         logger.warning(f"PyPDF2 read error checking searchable status ({pdf_path}): {e}. Assuming not searchable.")
         return False
    except Exception as e:
        logger.error(f"Error checking if PDF is searchable ({pdf_path}): {e}", exc_info=True)
        return False


# --- (create_searchable_pdf - UNCHANGED from previous version) ---
def create_searchable_pdf(images: List[Image.Image], ocr_data_list: List[Dict[str, Any]], output_pdf_path: str, update_status: Optional[Callable[[str], None]] = None):
    if update_status: update_status(STATUS_CREATING_PDF)
    logger.info(f"Creating searchable PDF: {output_pdf_path}")
    temp_dir = None
    try:
        first_image = images[0] if images else None
        page_size = letter
        if first_image:
            try: # Try getting size info
                img_width_px, img_height_px = first_image.size
                img_dpi = first_image.info.get('dpi', (72, 72))[0]
                page_width_pt = img_width_px * 72 / img_dpi
                page_height_pt = img_height_px * 72 / img_dpi
                page_size = (page_width_pt, page_height_pt)
            except Exception as size_e:
                 logger.warning(f"Could not determine page size from image, using default 'letter': {size_e}")
                 page_size = letter

        width_pt, height_pt = page_size
        c = canvas.Canvas(output_pdf_path, pagesize=page_size)
        temp_dir = tempfile.mkdtemp(prefix="pdfocr_img_")

        num_images = len(images)
        for i, (image, ocr_data) in enumerate(zip(images, ocr_data_list)):
            page_num = i + 1
            if update_status: update_status(f"Creating PDF page {page_num}/{num_images}...")
            temp_image_path = os.path.join(temp_dir, f"temp_page_{page_num}.png")
            if image.mode == 'CMYK': image = image.convert('RGB')
            if image.mode == 'P': image = image.convert('RGBA')
            elif image.mode == 'L': image = image.convert('RGB')
            image.save(temp_image_path, "PNG")

            img_width_px, img_height_px = image.size
            draw_width, draw_height = width_pt, height_pt
            c.drawImage(ImageReader(temp_image_path), 0, 0, width=draw_width, height=draw_height)

            text = c.beginText()
            text.setTextRenderMode(3)
            c.setFillColorRGB(0, 0, 0, alpha=0)
            c.setStrokeColorRGB(0, 0, 0, alpha=0)
            scale_x = draw_width / img_width_px if img_width_px else 1
            scale_y = draw_height / img_height_px if img_height_px else 1

            processed_words = 0
            for j in range(len(ocr_data.get('text', []))):
                word = ocr_data['text'][j].strip()
                if word:
                    processed_words += 1
                    left_px, top_px, width_px, height_px = (int(ocr_data[k][j]) for k in ['left', 'top', 'width', 'height'])
                    pdf_x = left_px * scale_x
                    pdf_y = height_pt - ((top_px + height_px) * scale_y)
                    font_size = max(height_px * scale_y, 6)
                    word_width_on_page = width_px * scale_x
                    text.setFont("Helvetica", font_size)
                    text.setTextOrigin(pdf_x, pdf_y + (font_size * 0.15))
                    actual_word_width = c.stringWidth(word, "Helvetica", font_size)
                    if actual_word_width > 0:
                         h_scale = (word_width_on_page / actual_word_width) * 100
                         text.setHorizScale(max(h_scale, 50))
                    text.textLine(word)
                    text.setHorizScale(100)
            if processed_words > 0: c.drawText(text)
            c.showPage()

        logger.info("Saving final searchable PDF document.")
        c.save()
    except reportlab.lib.utils.RLAUNCH_ERROR as e: raise RuntimeError(f"ReportLab failed to create PDF: {e}")
    except Exception as e: raise RuntimeError(f"Failed to create searchable PDF: {e}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except Exception as e: logger.error(f"Failed to clean up temp dir {temp_dir}: {e}")


# --- (compress_pdf_with_ghostscript - UNCHANGED from previous version) ---
def compress_pdf_with_ghostscript(input_pdf: str, output_pdf: str, setting: str = DEFAULT_GS_COMPRESSION_SETTING, update_status: Optional[Callable[[str], None]] = None):
    if update_status: update_status(STATUS_COMPRESSING)
    logger.info(f"Compressing PDF: {input_pdf} with setting: {setting}")
    if not os.path.exists(GHOSTSCRIPT_PATH): raise FileNotFoundError(f"Ghostscript not found: {GHOSTSCRIPT_PATH}")
    temp_compressed_pdf = output_pdf + ".tmp_compress"
    try:
        command = [ GHOSTSCRIPT_PATH, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", f"-dPDFSETTINGS={setting}",
                    "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={temp_compressed_pdf}", input_pdf ]
        logger.info(f"Running Ghostscript command: {' '.join(command)}")
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if os.path.exists(temp_compressed_pdf) and os.path.getsize(temp_compressed_pdf) > 0:
             os.replace(temp_compressed_pdf, output_pdf)
             logger.info(f"Successfully compressed PDF: {output_pdf}")
        else:
             if os.path.exists(temp_compressed_pdf): os.remove(temp_compressed_pdf)
             raise RuntimeError("Ghostscript compression resulted in an invalid file.")
    except FileNotFoundError: raise
    except subprocess.CalledProcessError as e:
        logger.error(f"Ghostscript compression failed. Return code: {e.returncode}, Stderr: {e.stderr}")
        if os.path.exists(temp_compressed_pdf): os.remove(temp_compressed_pdf)
        raise RuntimeError(f"Ghostscript failed with return code {e.returncode}.") from e
    except Exception as e:
        logger.error(f"Error during PDF compression ({input_pdf}): {e}", exc_info=True)
        if os.path.exists(temp_compressed_pdf):
            try: os.remove(temp_compressed_pdf)
            except OSError as rm_err: logger.error(f"Could not remove temp file {temp_compressed_pdf}: {rm_err}")
        raise RuntimeError(f"Compression failed: {e}")


# --- Main Application Logic ---

# **MODIFIED process_single_pdf** (Removed lambda, adjusted exception catching)
def process_single_pdf(
    pdf_path: str,
    output_dir: str,
    output_suffix: str,
    ocr_lang: str,
    page_range_str: str,
    preprocess_opts: Dict,
    create_text_file: bool,
    compress_pdf: bool,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    check_cancel: Optional[Callable[[], bool]] = None
) -> Optional[bool]:
    """ Processes a single PDF file. Returns True, False, or None (cancelled). """
    filename = os.path.basename(pdf_path)
    logger.info(f"--- Starting processing for: {filename} ---")
    status = STATUS_WAITING

    # Helper to update progress, ensures progress_callback exists
    def update_progress(current: int, total: int, status_msg: str):
        if progress_callback: progress_callback(current, total, status_msg)

    try: # Outer try for general failures/cancellation
        if check_cancel and check_cancel(): return None
        update_progress(0, 1, STATUS_CHECKING_PDF) # Use total=1 for single-step status

        if not os.path.exists(pdf_path): raise FileNotFoundError(f"Input file not found: {pdf_path}")

        # 1. Check if already searchable
        if is_pdf_searchable(pdf_path):
            status = STATUS_SKIPPED
            logger.info(f"PDF is already searchable, skipping: {filename}")
            update_progress(1, 1, status) # Update final status
            return True

        # 2. Define output paths
        base_name = os.path.splitext(filename)[0]
        safe_base_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in base_name).rstrip()
        output_pdf_path = os.path.join(output_dir, f"{safe_base_name}{output_suffix}.pdf")
        output_text_path = os.path.join(output_dir, f"{safe_base_name}{output_suffix}.txt") if create_text_file else None
        output_pdf_path = os.path.abspath(output_pdf_path)
        if output_text_path: output_text_path = os.path.abspath(output_text_path)
        os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)

        # 3. Convert PDF to Images
        if check_cancel and check_cancel(): return None
        update_progress(0, 1, STATUS_CONVERTING) # Set status before long call
        # *** REMOVED update_status lambda from the call below ***
        all_images, file_hash, total_pages = pdf_to_images(pdf_path, dpi=DEFAULT_CONVERSION_DPI)
        if not all_images: raise RuntimeError("Failed to convert PDF (empty image list).")
        update_progress(1, total_pages, f"Converted {total_pages} pages") # Update after call

        # 4. Parse and Apply Page Range Filter
        target_pages_indices: Optional[Set[int]] = None
        if page_range_str:
             try:
                  target_pages_indices = parse_page_range(page_range_str, total_pages)
                  if target_pages_indices: logger.info(f"Applying page range filter: {[i+1 for i in sorted(target_pages_indices)]}")
                  else: logger.info("No valid page range specified, processing all pages.")
             except ValueError as e:
                  logger.error(f"Invalid page range '{page_range_str}' for {filename}: {e}. Processing all pages.")
                  messagebox.showwarning("Invalid Page Range", f"Invalid page range for {filename}: {e}\nProcessing all pages instead.")
                  target_pages_indices = None

        images_to_process = [(idx, img) for idx, img in enumerate(all_images) if target_pages_indices is None or idx in target_pages_indices]
        if not images_to_process: raise RuntimeError("No pages selected after applying filter.")
        num_pages_to_process = len(images_to_process)
        logger.info(f"Processing {num_pages_to_process} pages for {filename}.")

        # 5. Perform OCR on selected Images
        ocr_data_list, processed_images_for_pdf = [], []
        for i, (original_index, image) in enumerate(images_to_process):
            if check_cancel and check_cancel(): return None
            page_num_processing = i + 1
            page_num_original = original_index + 1
            status = STATUS_OCR.format(current=page_num_processing, total=num_pages_to_process)
            # Pass update_progress directly to ocr_image_to_data's status update if needed
            # For now, just update before the call
            update_progress(page_num_processing, num_pages_to_process, status.split('...')[0]) # Show stage

            ocr_data = ocr_image_to_data(
                image, page_number=page_num_original, file_hash=file_hash, lang=ocr_lang,
                preprocess_opts=preprocess_opts,
                update_status=lambda s: update_progress(page_num_processing, num_pages_to_process, s) # Pass status updater
            )
            ocr_data_list.append(ocr_data)
            processed_images_for_pdf.append(image)

        # 6. Create Searchable PDF
        if check_cancel and check_cancel(): return None
        status = STATUS_CREATING_PDF
        update_progress(num_pages_to_process, num_pages_to_process, status) # Update before call
        create_searchable_pdf(processed_images_for_pdf, ocr_data_list, output_pdf_path,
                               update_status=lambda s: update_progress(num_pages_to_process, num_pages_to_process, s))

        # 7. Generate Text File
        if output_text_path:
            if check_cancel and check_cancel(): return None
            status = STATUS_WRITING_TEXT
            update_progress(num_pages_to_process, num_pages_to_process, status)
            try:
                all_text = "\n\n".join(" ".join(filter(None, page_data.get('text', []))) for page_data in ocr_data_list)
                with open(output_text_path, 'w', encoding='utf-8') as f: f.write(all_text.strip())
                logger.info("Text file generated successfully.")
            except Exception as e:
                 logger.error(f"Failed writing text file {output_text_path}: {e}", exc_info=True)
                 messagebox.showwarning("Text File Error", f"Could not write text file for {filename}: {e}")

        # 8. Compress PDF
        if compress_pdf:
            if check_cancel and check_cancel(): return None
            status = STATUS_COMPRESSING
            update_progress(num_pages_to_process, num_pages_to_process, status)
            try:
                 compress_pdf_with_ghostscript(output_pdf_path, output_pdf_path, setting=DEFAULT_GS_COMPRESSION_SETTING,
                                                update_status=lambda s: update_progress(num_pages_to_process, num_pages_to_process, s))
            except FileNotFoundError:
                 logger.error("Ghostscript not found, cannot compress.")
                 messagebox.showwarning("Compression Skipped", f"Ghostscript not found at {GHOSTSCRIPT_PATH}. PDF not compressed.")
            except Exception as e:
                 logger.error(f"Compression failed for {output_pdf_path}: {e}", exc_info=True)
                 messagebox.showwarning("Compression Failed", f"Could not compress PDF {os.path.basename(output_pdf_path)}: {e}\nUncompressed OCR PDF saved.")

        if check_cancel and check_cancel(): return None

        status = STATUS_DONE
        update_progress(num_pages_to_process, num_pages_to_process, status)
        logger.info(f"--- Successfully finished processing: {filename} ---")
        return True

    # **MODIFIED except block** (Removed specific PasswordRequiredError)
    except (FileNotFoundError, RuntimeError, ValueError) as e: # Catch our raised RuntimeErrors, File not Found, ValueErrors
         status = STATUS_FAILED
         logger.error(f"--- Failed processing PDF: {filename} --- Error Type: {type(e).__name__}, Message: {e}", exc_info=True)
         # Provide specific messages based on error content if possible
         msg_prefix = f"Error processing:\n{filename}\n\n"
         if isinstance(e, FileNotFoundError): msg = "File not found or inaccessible."
         elif "password protected" in str(e).lower(): msg = "File is password protected."
         elif "Poppler not found" in str(e): msg = "Poppler dependency missing.\nPlease install Poppler."
         elif "Tesseract OCR engine not found" in str(e): msg = "Tesseract dependency missing or path incorrect."
         elif "Ghostscript failed" in str(e): msg = f"Ghostscript compression failed: {e}"
         else: msg = f"{type(e).__name__}: {e}" # General runtime/value error

         messagebox.showerror("Processing Error", msg_prefix + msg + f"\n\nCheck log ({LOG_FILENAME}) for details.")
         update_progress(0, 1, status) # Indicate failure
         return False
    except Exception as e: # Catch any other unexpected exception
        status = STATUS_FAILED
        logger.critical(f"--- UNEXPECTED CRITICAL FAILURE processing PDF: {filename} --- Error: {e}", exc_info=True)
        messagebox.showerror("Critical Error", f"An unexpected critical error occurred processing:\n{filename}\n\nError: {e}\n\nPlease check the log file ({LOG_FILENAME}).")
        update_progress(0, 1, status)
        return False


# --- GUI Application ---

# --- (TextHandler - UNCHANGED) ---
class TextHandler(logging.Handler):
    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget
    def emit(self, record):
        msg = self.format(record)
        def append_message():
             self.text_widget.config(state=tk.NORMAL)
             self.text_widget.insert(tk.END, msg + '\n')
             self.text_widget.config(state=tk.DISABLED)
             self.text_widget.see(tk.END)
        self.text_widget.after(0, append_message)

# --- (PDFOCRApp - __init__, _create_input/output/options/status_section, UI methods - MOSTLY UNCHANGED) ---
# Note: The update_progress method logic was already updated in the previous step
# to handle indeterminate states and different status messages, so it should work
# with the changes made in process_single_pdf and the removal of the lambda.
class PDFOCRApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(750, 650)
        self.root.geometry("800x700")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.config = load_config()

        # --- Variables ---
        self.input_pdf_paths: List[str] = []
        self.output_directory = tk.StringVar(value=self.config.get("last_output_dir", ""))
        self.output_suffix = tk.StringVar(value=self.config.get("output_suffix", "_OCR"))
        self.ocr_language = tk.StringVar(value=self.config.get("ocr_language", DEFAULT_OCR_LANGUAGES[0]))
        self.page_range_enabled = tk.BooleanVar(value=self.config.get("page_range_enabled", False))
        self.page_range_str = tk.StringVar(value=self.config.get("page_range_str", ""))
        self.preprocess_grayscale = tk.BooleanVar(value=self.config.get("preprocess_grayscale", False))
        self.preprocess_threshold = tk.BooleanVar(value=self.config.get("preprocess_threshold", False))
        self.compress_pdf_var = tk.BooleanVar(value=self.config.get("compress_pdf", True))
        self.generate_text_var = tk.BooleanVar(value=self.config.get("generate_text", False))
        self.open_folder_var = tk.BooleanVar(value=self.config.get("open_folder_on_complete", False))
        self.processing_active = False
        self.cancel_requested = False

        # --- Styles ---
        self.style = ttk.Style()
        try: self.style.theme_use("clam")
        except tk.TclError: self.style.theme_use(self.style.theme_names()[0])

        self.style.configure("TFrame", background="#e8e8e8")
        self.style.configure("TLabel", background="#e8e8e8", font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), background="#e8e8e8")
        self.style.configure("TButton", font=("Segoe UI", 10), padding=5)
        self.style.configure("Add.TButton", foreground="#006400", font=("Segoe UI", 10, "bold"))
        self.style.configure("Remove.TButton", foreground="#8B0000", font=("Segoe UI", 10, "bold"))
        self.style.configure("Action.TButton", background="#0078D7", foreground="white", font=("Segoe UI", 10, "bold"))
        self.style.map("Action.TButton", background=[("active", "#005A9E"), ("disabled", "#A0A0A0")])
        self.style.configure("Cancel.TButton", background="#D83B01", foreground="white", font=("Segoe UI", 10, "bold"))
        self.style.map("Cancel.TButton", background=[("active", "#B72E00"), ("disabled", "#A0A0A0")])
        self.style.configure("TCheckbutton", background="#e8e8e8", font=("Segoe UI", 10))
        self.style.configure("TRadiobutton", background="#e8e8e8", font=("Segoe UI", 10))
        self.style.configure("TCombobox", font=("Segoe UI", 9))
        self.style.configure("TEntry", font=("Segoe UI", 9), padding=3)
        self.style.configure("TLabelframe.Label", background="#e8e8e8", font=("Segoe UI", 10, "bold"))
        self.style.configure("TProgressbar", thickness=18)

        # --- Main Layout ---
        self.top_frame = ttk.Frame(root, padding="10 10 0 10")
        self.top_frame.pack(fill=tk.X, side=tk.TOP)
        header_label = ttk.Label(self.top_frame, text=APP_NAME, style="Header.TLabel")
        header_label.pack(pady=(0, 5))

        self.canvas_frame = ttk.Frame(root)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(5, 5), padx=5)
        self.canvas = tk.Canvas(self.canvas_frame, bg="#e8e8e8", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollable_frame = ttk.Frame(self.canvas, style="TFrame")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.scrollable_frame.bind("<Configure>", self.on_frame_configure)
        self.canvas.bind("<Configure>", self.on_frame_configure) # Also bind canvas configure
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel, add='+')
        self.canvas.bind_all("<Button-4>", self.on_mousewheel, add='+')
        self.canvas.bind_all("<Button-5>", self.on_mousewheel, add='+')

        self.bottom_frame = ttk.Frame(root, padding="5 10 10 10")
        self.bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # --- Create Content Sections ---
        self._create_input_section()
        self._create_output_section()
        self._create_options_section()
        self._create_status_section()

        # --- Action Buttons ---
        action_buttons_frame = ttk.Frame(self.bottom_frame)
        action_buttons_frame.pack(pady=(5, 0))
        self.process_button = ttk.Button(action_buttons_frame, text="Start OCR Processing", command=self.process_pdfs, style="Action.TButton", width=25)
        self.process_button.pack(side=tk.LEFT, padx=10)
        self.cancel_button = ttk.Button(action_buttons_frame, text="Cancel", command=self.request_cancel, style="Cancel.TButton", state=tk.DISABLED, width=15)
        self.cancel_button.pack(side=tk.LEFT, padx=10)

        # --- DnD Setup ---
        if TkinterDnD:
            self.file_listbox.drop_target_register(DND_FILES)
            self.file_listbox.dnd_bind('<<Drop>>', self.handle_drop)
            logger.info("Drag and Drop initialized.")
        else: logger.warning("Drag and Drop disabled (tkinterdnd2 not found).")

        # --- Final Setup ---
        self.root.after(100, self.on_frame_configure)
        self.check_initial_dependencies()


    def _create_input_section(self):
        input_frame = ttk.LabelFrame(self.scrollable_frame, text="Input PDF Documents", padding="10")
        input_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        input_frame.columnconfigure(0, weight=1)

        list_frame = ttk.Frame(input_frame)
        list_frame.grid(row=0, column=0, rowspan=5, sticky="nsew", padx=(0, 5))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(list_frame, height=10, width=70, font=("Segoe UI", 9), selectmode=tk.EXTENDED, relief=tk.SUNKEN, borderwidth=1)
        self.file_listbox_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_listbox.yview)
        self.file_listbox['yscrollcommand'] = self.file_listbox_scrollbar.set
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        self.file_listbox_scrollbar.grid(row=0, column=1, sticky="ns")

        input_buttons_frame = ttk.Frame(input_frame)
        input_buttons_frame.grid(row=0, column=1, rowspan=5, sticky="n", padx=(10, 0))
        ttk.Button(input_buttons_frame, text="Add Files...", command=self.browse_input_pdfs, style="Add.TButton", width=18).pack(pady=2, fill=tk.X)
        ttk.Button(input_buttons_frame, text="Add from Folder...", command=self.add_files_via_wildcard, style="Add.TButton", width=18).pack(pady=2, fill=tk.X)
        ttk.Separator(input_buttons_frame, orient=tk.HORIZONTAL).pack(pady=5, fill=tk.X)
        ttk.Button(input_buttons_frame, text="Move Up", command=lambda: self.move_selected_files(-1), width=18).pack(pady=2, fill=tk.X)
        ttk.Button(input_buttons_frame, text="Move Down", command=lambda: self.move_selected_files(1), width=18).pack(pady=2, fill=tk.X)
        ttk.Separator(input_buttons_frame, orient=tk.HORIZONTAL).pack(pady=5, fill=tk.X)
        ttk.Button(input_buttons_frame, text="Remove Selected", command=self.delete_selected_files, style="Remove.TButton", width=18).pack(pady=2, fill=tk.X)
        ttk.Button(input_buttons_frame, text="Remove All", command=self.delete_all_files, style="Remove.TButton", width=18).pack(pady=2, fill=tk.X)
        if not TkinterDnD: ttk.Label(input_frame, text="Drag & Drop requires 'tkinterdnd2'", font=("Segoe UI", 8), foreground="gray").grid(row=5, column=0, sticky="w", pady=(5,0))

    def _create_output_section(self):
        output_frame = ttk.LabelFrame(self.scrollable_frame, text="Output Settings", padding="10")
        output_frame.pack(fill=tk.X, padx=10, pady=5)
        output_frame.columnconfigure(1, weight=1)
        ttk.Label(output_frame, text="Output Folder:").grid(row=0, column=0, sticky="w", padx=(0,5), pady=2)
        out_dir_entry = ttk.Entry(output_frame, textvariable=self.output_directory, width=60)
        out_dir_entry.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(output_frame, text="Browse...", command=self.browse_output_directory, width=10).grid(row=0, column=2, padx=(5,0), pady=2)
        self.output_directory.trace_add("write", lambda *_: self.save_current_config())
        ttk.Label(output_frame, text="Output Suffix:").grid(row=1, column=0, sticky="w", padx=(0,5), pady=2)
        suffix_entry = ttk.Entry(output_frame, textvariable=self.output_suffix, width=15)
        suffix_entry.grid(row=1, column=1, sticky="w", pady=2)
        self.output_suffix.trace_add("write", lambda *_: self.save_current_config())

    def _create_options_section(self):
        options_frame = ttk.LabelFrame(self.scrollable_frame, text="Processing Options", padding="10")
        options_frame.pack(fill=tk.X, padx=10, pady=5)
        options_frame.columnconfigure(0, weight=1) # Allow left column to expand slightly if needed
        options_frame.columnconfigure(1, weight=1) # Allow right column to expand slightly if needed

        left_opt_frame = ttk.Frame(options_frame)
        left_opt_frame.grid(row=0, column=0, padx=(0, 15), sticky="nw")
        ttk.Label(left_opt_frame, text="OCR Language:").pack(anchor="w")
        lang_combo = ttk.Combobox(left_opt_frame, textvariable=self.ocr_language, values=DEFAULT_OCR_LANGUAGES, state="readonly", width=10)
        lang_combo.pack(anchor="w", pady=(0, 10))
        lang_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_config())

        page_range_frame = ttk.Frame(left_opt_frame)
        page_range_frame.pack(anchor="w", pady=(0, 10), fill=tk.X)
        page_range_check = ttk.Checkbutton(page_range_frame, text="Page Range:", variable=self.page_range_enabled, command=self._toggle_page_range_entry)
        page_range_check.grid(row=0, column=0, sticky='w')
        self.page_range_entry = ttk.Entry(page_range_frame, textvariable=self.page_range_str, width=20, state=tk.DISABLED)
        self.page_range_entry.grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(page_range_frame, text="(e.g. 1-5, 8, 10-)", font=("Segoe UI", 8)).grid(row=1, column=1, sticky='w', padx=5)
        self.page_range_enabled.trace_add("write", lambda *_: self.save_current_config())
        self.page_range_str.trace_add("write", lambda *_: self.save_current_config())
        self._toggle_page_range_entry()

        preproc_frame = ttk.LabelFrame(left_opt_frame, text="Image Preprocessing", padding=5)
        preproc_frame.pack(anchor="w", fill=tk.X, pady=(5,0))
        if cv2:
            cb_gray = ttk.Checkbutton(preproc_frame, text="Grayscale", variable=self.preprocess_grayscale)
            cb_gray.pack(anchor="w")
            cb_thresh = ttk.Checkbutton(preproc_frame, text="Binarize (Threshold)", variable=self.preprocess_threshold)
            cb_thresh.pack(anchor="w")
            self.preprocess_grayscale.trace_add("write", lambda *_: self.save_current_config())
            self.preprocess_threshold.trace_add("write", lambda *_: self.save_current_config())
        else: ttk.Label(preproc_frame, text="OpenCV not found.", foreground="gray").pack(anchor="w")

        right_opt_frame = ttk.Frame(options_frame)
        right_opt_frame.grid(row=0, column=1, sticky="nw", padx=(15, 0))
        ttk.Checkbutton(right_opt_frame, text="Compress output PDF", variable=self.compress_pdf_var, command=lambda: self.save_current_config()).pack(anchor="w", pady=1)
        ttk.Checkbutton(right_opt_frame, text="Generate text file (.txt)", variable=self.generate_text_var, command=lambda: self.save_current_config()).pack(anchor="w", pady=1)
        ttk.Checkbutton(right_opt_frame, text="Open output folder on complete", variable=self.open_folder_var, command=lambda: self.save_current_config()).pack(anchor="w", pady=(8, 1))
        ttk.Button(right_opt_frame, text="Clear Processing Cache", command=self.clear_cache).pack(anchor="w", pady=(12,0))

    def _create_status_section(self):
        status_frame = ttk.LabelFrame(self.scrollable_frame, text="Status & Progress", padding="10")
        status_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.overall_progress_label = ttk.Label(status_frame, text=f"Overall Progress: {STATUS_WAITING}")
        self.overall_progress_label.pack(anchor="w", fill=tk.X)
        self.overall_progress = ttk.Progressbar(status_frame, orient="horizontal", length=400, mode="determinate")
        self.overall_progress.pack(pady=(2,5), fill=tk.X)
        self.current_file_label = ttk.Label(status_frame, text="Current File: N/A")
        self.current_file_label.pack(anchor="w", fill=tk.X, pady=(5,0))
        self.current_file_status_label = ttk.Label(status_frame, text=f"Status: {STATUS_WAITING}", font=("Segoe UI", 9), foreground="gray")
        self.current_file_status_label.pack(anchor="w", fill=tk.X)
        self.current_file_progress = ttk.Progressbar(status_frame, orient="horizontal", length=400, mode="determinate")
        self.current_file_progress.pack(pady=(2,5), fill=tk.X)
        self.log_display = scrolledtext.ScrolledText(status_frame, height=10, width=80, font=("Consolas", 8), state=tk.DISABLED, wrap=tk.WORD, relief=tk.SUNKEN, borderwidth=1)
        self.log_display.pack(fill=tk.BOTH, expand=True, pady=(5,0))
        self.text_log_handler = TextHandler(self.log_display)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        self.text_log_handler.setFormatter(formatter)
        logger.addHandler(self.text_log_handler)
        logger.setLevel(logging.INFO)


    # --- (on_frame_configure, on_mousewheel, handle_drop, log_message, check_initial_dependencies - UNCHANGED/ Minor adjustments) ---
    def on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # This line prevents unnecessary horizontal scrollbar if content fits
        self.canvas.itemconfig(self.canvas_window, width=self.canvas.winfo_width())

    def on_mousewheel(self, event):
        if sys.platform.startswith('darwin'): delta = event.delta
        elif event.num == 4: delta = 1
        elif event.num == 5: delta = -1
        else: delta = event.delta / 120
        self.canvas.yview_scroll(int(-1 * delta), "units")

    def handle_drop(self, event):
        if not TkinterDnD: return
        filepaths_str = event.data
        filepaths = self.root.tk.splitlist(filepaths_str)
        added_count = 0
        for fp in filepaths:
            fp = fp.strip()
            if os.path.isdir(fp):
                folder_files = sorted(glob.glob(os.path.join(fp, "*.pdf")))
                new_paths = [f for f in folder_files if f not in self.input_pdf_paths]
                self.input_pdf_paths.extend(new_paths)
                if new_paths: logger.info(f"Added {len(new_paths)} via DnD from folder: {fp}")
                added_count += len(new_paths)
            elif os.path.isfile(fp) and fp.lower().endswith(".pdf"):
                 if fp not in self.input_pdf_paths:
                      self.input_pdf_paths.append(fp)
                      logger.info(f"Added file via DnD: {fp}")
                      added_count += 1
            else: logger.warning(f"Skipped dropped item: {fp}")
        if added_count > 0:
            self.update_file_listbox(); self.log_message("INFO", f"Added {added_count} PDF(s) via Drag and Drop.")
        else: self.log_message("WARN", "No new PDFs found in dropped items.")

    def log_message(self, level: str, message: str):
        level = level.upper()
        if level == "INFO": logger.info(message)
        elif level == "WARN" or level == "WARNING": logger.warning(message)
        elif level == "ERROR": logger.error(message)
        elif level == "CRITICAL": logger.critical(message)
        else: logger.debug(message)

    def check_initial_dependencies(self):
        missing_execs = check_dependencies()
        if missing_execs:
            self.log_message("ERROR", "Dependency check failed for executables:")
            for dep in missing_execs: self.log_message("ERROR", f" - {dep}")
            if any("Tesseract" in s or "Poppler" in s for s in missing_execs):
                 messagebox.showerror("Critical Dependency Missing", "\n".join(dep for dep in missing_execs if "Tesseract" in dep or "Poppler" in dep))
                 self.process_button.config(state="disabled")
            if any("Ghostscript" in s for s in missing_execs): self.log_message("WARN", "Ghostscript missing. Compression option will not work.")
        if not cv2:
            self.log_message("WARN", "OpenCV (cv2) not found. Image preprocessing disabled.")
            try: # Disable preprocessing checkboxes
                 frame = self.scrollable_frame.winfo_children()[2].winfo_children()[0].winfo_children()[3]
                 for w in frame.winfo_children(): w.config(state=tk.DISABLED)
            except Exception: pass # Ignore errors finding widgets
        if not TkinterDnD: self.log_message("WARN", "tkinterdnd2 not found. Drag and Drop disabled.")

    # --- (browse_input_pdfs, add_files_via_wildcard, update_file_listbox - UNCHANGED) ---
    def browse_input_pdfs(self):
        last_dir = self.config.get("last_input_dir", None)
        filepaths = filedialog.askopenfilenames(title="Select Input PDF Files", filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")], initialdir=last_dir)
        if filepaths:
            self.config["last_input_dir"] = os.path.dirname(filepaths[0])
            new_paths = [fp for fp in filepaths if fp not in self.input_pdf_paths]
            self.input_pdf_paths.extend(new_paths); self.update_file_listbox()
            self.log_message("INFO", f"Added {len(new_paths)} PDF file(s) via Browse.")

    def add_files_via_wildcard(self):
        last_dir = self.config.get("last_input_dir", None)
        directory = filedialog.askdirectory(title="Select Folder Containing PDFs", initialdir=last_dir)
        if directory:
            self.config["last_input_dir"] = directory
            wildcard_files = sorted(glob.glob(os.path.join(directory, "*.pdf")))
            if wildcard_files:
                new_paths = [fp for fp in wildcard_files if fp not in self.input_pdf_paths]
                self.input_pdf_paths.extend(new_paths); self.update_file_listbox()
                self.log_message("INFO", f"Added {len(new_paths)} PDF file(s) from folder.")
            else: messagebox.showinfo("No Files Found", f"No PDF files found in: {directory}")

    def update_file_listbox(self):
        self.file_listbox.delete(0, tk.END)
        for i, filepath in enumerate(self.input_pdf_paths): self.file_listbox.insert(tk.END, f"{i+1}. {os.path.basename(filepath)}")
        self.root.title(f"{APP_NAME} - {len(self.input_pdf_paths)} file(s) loaded")

    # --- (move_selected_files, delete_selected_files, delete_all_files, clear_cache, browse_output_directory - UNCHANGED) ---
    def move_selected_files(self, direction: int):
        indices = self.file_listbox.curselection()
        if not indices: return
        if direction == -1: # Move Up
            new_sel = []
            for i in sorted(indices):
                if i > 0:
                    self.input_pdf_paths[i], self.input_pdf_paths[i-1] = self.input_pdf_paths[i-1], self.input_pdf_paths[i]
                    new_sel.append(i-1)
                else: new_sel.append(i) # Stay at top
        elif direction == 1: # Move Down
            new_sel = []
            for i in sorted(indices, reverse=True):
                if i < len(self.input_pdf_paths) - 1:
                    self.input_pdf_paths[i], self.input_pdf_paths[i+1] = self.input_pdf_paths[i+1], self.input_pdf_paths[i]
                    new_sel.append(i+1)
                else: new_sel.append(i) # Stay at bottom
        else: return
        self.update_file_listbox()
        self.file_listbox.selection_clear(0, tk.END)
        for i in new_sel: self.file_listbox.selection_set(i)
        if new_sel: self.file_listbox.see(new_sel[0])

    def delete_selected_files(self):
        indices = self.file_listbox.curselection()
        if not indices: return
        removed_count = 0
        for i in sorted(indices, reverse=True):
            if 0 <= i < len(self.input_pdf_paths): self.input_pdf_paths.pop(i); removed_count += 1
        if removed_count > 0: self.update_file_listbox(); self.log_message("INFO", f"Removed {removed_count} file(s).")

    def delete_all_files(self):
        if not self.input_pdf_paths: return
        if messagebox.askyesno("Confirm Removal", f"Remove all {len(self.input_pdf_paths)} files?"):
            count = len(self.input_pdf_paths); self.input_pdf_paths.clear()
            self.update_file_listbox(); self.log_message("INFO", f"Removed all {count} files.")

    def clear_cache(self):
        if self.processing_active: messagebox.showwarning("Busy", "Cannot clear cache now."); return
        if os.path.exists(CACHE_DIR):
            if messagebox.askyesno("Confirm Cache Clear", f"Delete all cached data in:\n{CACHE_DIR}?"):
                self.log_message("INFO", "Starting cache clearing..."); self.root.update_idletasks()
                cleared, errors = 0, 0
                try:
                    for item in os.listdir(CACHE_DIR):
                        path = os.path.join(CACHE_DIR, item)
                        try:
                             if os.path.isdir(path): shutil.rmtree(path)
                             else: os.remove(path)
                             cleared += 1
                        except Exception as e: errors += 1; logger.error(f"Could not remove {path}: {e}")
                    msg = f"Cache cleared. Removed {cleared} items." + (f" Failed: {errors}." if errors else "")
                    self.log_message("INFO", msg); messagebox.showinfo("Cache Cleared", msg)
                except Exception as e: messagebox.showerror("Cache Error", f"Could not access cache: {e}")
        else: self.log_message("INFO", "No cache directory found.")

    def browse_output_directory(self):
        last = self.config.get("last_output_dir", None)
        directory = filedialog.askdirectory(title="Select Output Folder", initialdir=last)
        if directory: self.output_directory.set(directory); self.log_message("INFO", f"Output folder set.")

    # --- (_toggle_page_range_entry, update_progress, set_ui_state, request_cancel - UNCHANGED/ Minor adjustments) ---
    def _toggle_page_range_entry(self):
        state = tk.NORMAL if self.page_range_enabled.get() else tk.DISABLED
        try: self.page_range_entry.config(state=state)
        except Exception: pass # Widget might not exist yet during init
        self.save_current_config()

    def update_progress(self, current: int, total: int, status_msg: str):
        if self.cancel_requested: return
        self.current_file_status_label.config(text=f"Status: {status_msg}")
        is_page_progress = status_msg.startswith(STATUS_OCR.split(' ')[0]) or status_msg.startswith(STATUS_PREPROCESSING.split(' ')[0]) or status_msg.startswith("Creating PDF page")

        if total > 0 and is_page_progress:
            self.current_file_progress.config(mode='determinate')
            progress_percent = (current / total) * 100
            self.current_file_progress["value"] = progress_percent
            self.current_file_label.config(text=f"Current File Page: {current}/{total}")
        elif status_msg in [STATUS_CONVERTING, STATUS_CREATING_PDF, STATUS_COMPRESSING, STATUS_WRITING_TEXT, STATUS_LOADING_CACHE, STATUS_CHECKING_PDF]:
             self.current_file_progress.config(mode='indeterminate')
             self.current_file_progress.start(10)
             self.current_file_label.config(text="Current File: Processing...")
        elif status_msg in [STATUS_DONE, STATUS_SKIPPED, STATUS_FAILED, STATUS_CANCELLED]:
             self.current_file_progress.stop()
             self.current_file_progress.config(mode='determinate')
             self.current_file_progress["value"] = 100 if status_msg == STATUS_DONE else 0
             self.current_file_label.config(text=f"Current File: {status_msg}")
        else: # Waiting or other states
             self.current_file_progress.stop()
             self.current_file_progress.config(mode='determinate')
             self.current_file_progress["value"] = 0
             self.current_file_label.config(text="Current File: N/A")
        self.root.update_idletasks()

    def set_ui_state(self, active: bool):
        self.processing_active = active
        process_state = tk.DISABLED if active else tk.NORMAL
        cancel_state = tk.NORMAL if active else tk.DISABLED
        other_state = tk.DISABLED if active else tk.NORMAL

        self.process_button.config(state=process_state)
        self.cancel_button.config(state=cancel_state)
        try: # Input buttons
            cont = self.scrollable_frame.winfo_children()[0].winfo_children()[1]
            for w in cont.winfo_children(): w.config(state=other_state)
        except Exception: pass
        try: # Output section
            sect = self.scrollable_frame.winfo_children()[1]
            for w in sect.winfo_children(): w.winfo_children()[1].config(state=other_state) # entry
            sect.winfo_children()[0].winfo_children()[2].config(state=other_state) # browse
        except Exception: pass
        try: # Options section
            sect = self.scrollable_frame.winfo_children()[2]
            left = sect.winfo_children()[0]; right = sect.winfo_children()[1]
            left.winfo_children()[1].config(state=other_state) # lang
            pr_frame = left.winfo_children()[2] # page range
            pr_frame.winfo_children()[0].config(state=other_state) # pr check
            if self.page_range_enabled.get(): pr_frame.winfo_children()[1].config(state=other_state) # pr entry
            if cv2: # preproc
                preproc = left.winfo_children()[3]
                for w in preproc.winfo_children(): w.config(state=other_state)
            for w in right.winfo_children(): w.config(state=other_state) # Right side opts/cache
        except Exception: pass

    def request_cancel(self):
        if self.processing_active:
             if messagebox.askyesno("Confirm Cancellation", "Stop the current processing batch?"):
                 self.cancel_requested = True; self.log_message("WARN", "Cancellation requested...")
                 self.cancel_button.config(state=tk.DISABLED)

    # --- (process_pdfs - UNCHANGED from previous version, relies on process_single_pdf changes) ---
    def process_pdfs(self):
        if self.processing_active: return
        if not self.input_pdf_paths: messagebox.showerror("No Input Files", "Please add PDF files."); return
        output_dir = self.output_directory.get()
        if not output_dir or not os.path.isdir(output_dir): messagebox.showerror("Output Folder Invalid", f"Select a valid output folder."); return

        output_suffix = self.output_suffix.get().strip()
        ocr_lang = self.ocr_language.get()
        page_range_str = self.page_range_str.get() if self.page_range_enabled.get() else ""
        preprocess_opts = {"grayscale": self.preprocess_grayscale.get(), "threshold": self.preprocess_threshold.get()} if cv2 else {}
        create_text = self.generate_text_var.get()
        compress = self.compress_pdf_var.get()
        open_folder = self.open_folder_var.get()

        if compress and not os.path.exists(GHOSTSCRIPT_PATH):
             if not messagebox.askyesno("Ghostscript Missing", f"Compression enabled, but Ghostscript not found.\nContinue without compression?"): return
             compress = False

        self.save_current_config()
        self.cancel_requested = False
        self.set_ui_state(active=True)
        self.log_message("INFO", f"=== Starting Batch Processing ({len(self.input_pdf_paths)} files) ===")
        self.log_message("INFO", f"Options: Lang={ocr_lang}, Suffix='{output_suffix}', Compress={compress}, Text={create_text}, Preproc={preprocess_opts}, Pages='{page_range_str or 'All'}'")

        total_files = len(self.input_pdf_paths)
        counts = {"success": 0, "fail": 0, "skipped": 0, "cancelled": 0}

        self.overall_progress["value"] = 0
        self.overall_progress_label.config(text=f"Overall Progress: Preparing... (0/{total_files})")
        self.update_progress(0, 0, STATUS_WAITING)
        self.root.update_idletasks()

        try:
             for i, pdf_path in enumerate(self.input_pdf_paths):
                 if self.cancel_requested:
                      counts["cancelled"] = total_files - i; break
                 file_index = i + 1
                 filename = os.path.basename(pdf_path)
                 self.overall_progress_label.config(text=f"Overall Progress: File {file_index}/{total_files} ({filename})")
                 self.update_progress(0, 0, STATUS_WAITING)
                 self.root.update_idletasks()

                 result = process_single_pdf(
                     pdf_path, output_dir, output_suffix, ocr_lang, page_range_str, preprocess_opts,
                     create_text, compress, self.update_progress, lambda: self.cancel_requested
                 )

                 if result is True:
                      last_status = self.current_file_status_label.cget("text").replace("Status: ", "")
                      if last_status == STATUS_SKIPPED: counts["skipped"] += 1
                      else: counts["success"] += 1
                 elif result is False: counts["fail"] += 1
                 elif result is None: counts["cancelled"] = total_files - i; break
                 self.overall_progress["value"] = (file_index / total_files) * 100
                 self.root.update_idletasks()
        finally:
             self.set_ui_state(active=False)
             self.cancel_requested = False
             final_status = STATUS_DONE if counts["cancelled"] == 0 else STATUS_CANCELLED
             self.update_progress(0, 0, final_status) # Final current status

             final_overall = "Overall Progress: Completed"
             if counts["cancelled"] > 0: final_overall += f" (Cancelled)"
             elif counts["fail"] > 0: final_overall += f" (with errors)"
             self.overall_progress_label.config(text=final_overall)

             summary = f"Batch finished.\n\nSuccess: {counts['success']}, Skipped: {counts['skipped']}, Failed: {counts['fail']}"
             if counts['cancelled'] > 0: summary += f", Cancelled: {counts['cancelled']}"
             summary += f"\n\nOutput: {output_dir}\nLog: {LOG_FILENAME}"
             log_summary = f"Summary: Success={counts['success']}, Skipped={counts['skipped']}, Failed={counts['fail']}, Cancelled={counts['cancelled']}"
             logger.info("=== Batch Processing Finished ==="); logger.info(log_summary)
             self.log_message("INFO", "=== Batch Processing Finished ==="); self.log_message("INFO", log_summary)
             messagebox.showinfo("Processing Finished", summary)
             if open_folder and (counts["success"] > 0 or counts["skipped"] > 0): self.open_output_folder(output_dir)

    # --- (open_output_folder, save_current_config, on_closing - UNCHANGED) ---
    def open_output_folder(self, folder_path):
         try:
             if sys.platform == "win32": os.startfile(os.path.realpath(folder_path))
             elif sys.platform == "darwin": subprocess.check_call(["open", folder_path])
             else: subprocess.check_call(["xdg-open", folder_path])
             self.log_message("INFO", f"Opened output folder.")
         except Exception as e:
             logger.error(f"Failed to open output folder '{folder_path}': {e}", exc_info=True)
             messagebox.showerror("Error", f"Could not open output folder:\n{e}")

    def save_current_config(self):
        self.config["last_output_dir"] = self.output_directory.get()
        self.config["last_input_dir"] = self.config.get("last_input_dir")
        self.config["output_suffix"] = self.output_suffix.get()
        self.config["ocr_language"] = self.ocr_language.get()
        self.config["page_range_enabled"] = self.page_range_enabled.get()
        self.config["page_range_str"] = self.page_range_str.get()
        self.config["preprocess_grayscale"] = self.preprocess_grayscale.get()
        self.config["preprocess_threshold"] = self.preprocess_threshold.get()
        self.config["compress_pdf"] = self.compress_pdf_var.get()
        self.config["generate_text"] = self.generate_text_var.get()
        self.config["open_folder_on_complete"] = self.open_folder_var.get()
        save_config(self.config)

    def on_closing(self):
        if self.processing_active:
            if messagebox.askyesno("Exit Confirmation", "Processing active. Exit anyway?"):
                self.cancel_requested = True
                logger.info("Exiting during active processing."); self.save_current_config(); self.root.destroy()
            else: return
        else: logger.info("Exiting application."); self.save_current_config(); self.root.destroy()

# --- Main Execution (UNCHANGED) ---
if __name__ == "__main__":
    if TkinterDnD: main_root = TkinterDnD.Tk(); logger.info("Using TkinterDnD root.")
    else: main_root = tk.Tk(); logger.info("Using standard Tk root.")

    if not os.path.exists(TESSERACT_PATH):
         logger.critical(f"Tesseract not found at {TESSERACT_PATH}.")
         messagebox.showerror("Tesseract Missing", f"Critical Error: Tesseract OCR not found at:\n{TESSERACT_PATH}\n\nCannot run.")
         main_root.destroy(); sys.exit(1)

    app = PDFOCRApp(main_root)
    main_root.mainloop()

# <<< End of the script >>>

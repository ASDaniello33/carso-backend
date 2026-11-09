mods = ['pytesseract', 'easyocr', 'cv2', 'numpy', 'PIL', 'paddleocr', 'doctr', 'keras_ocr']
for m in mods:
    try:
        __import__(m)
        print('OK  ', m)
    except Exception as e:
        print('NON ', m, type(e).__name__)

try:
    import pytesseract
    print('tesseract version:', pytesseract.get_tesseract_version())
except Exception as e:
    print('tesseract indisponible:', type(e).__name__, e)

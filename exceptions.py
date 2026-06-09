"""
exceptions.py — Shared Custom Exceptions
=========================================
All pipeline-specific errors in one place so that app.py, ocr.py,
extract.py, and db.py can import from a single module.
"""


class BlurryImageError(Exception):
    """
    Raised when OCR returns too little text, indicating the photo
    is blurry, dark, or otherwise unreadable.
    """
    def __init__(self, message: str = "Image is too blurry or unreadable."):
        self.message = message
        super().__init__(self.message)


class NotAnInvoiceError(Exception):
    """
    Raised when the extracted content does not appear to be a
    valid GST invoice (e.g. a selfie, menu, or random photo).
    """
    def __init__(self, message: str = "The image does not appear to be a GST invoice."):
        self.message = message
        super().__init__(self.message)


class DuplicateInvoiceError(Exception):
    """
    Raised when an invoice with the same vendor_gstin + invoice_number
    already exists in the bills table.
    """
    def __init__(self, existing_bill_id: str):
        self.existing_bill_id = existing_bill_id
        super().__init__(f"Invoice already exists with ID: {existing_bill_id}")

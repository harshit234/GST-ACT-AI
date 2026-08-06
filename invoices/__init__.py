"""
invoices — Customer Invoice Generation Blueprint
=================================================
Handles creation, management, and PDF generation of GST-compliant
sales invoices for merchants.
"""

from flask import Blueprint

invoices_bp = Blueprint(
    'invoices',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/invoices'
)

from invoices import routes  # noqa: E402, F401

"""Realistic (fictional) sample letters so anyone can try Paperwise in seconds.

Rendered as photos-of-paper PNGs with dates relative to today, so deadlines always look live and the
vision model gets exercised exactly as with a real phone photo.
"""
from __future__ import annotations

import io
import random
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%B %d, %Y")


def _letters() -> dict[str, dict]:
    return {
        "debt_collection": {
            "label": "Debt collector letter",
            "blurb": "A collection agency says you owe $1,284.37",
            "header": "NORTHGATE RECOVERY PARTNERS, LLC",
            "sub": "PO Box 55210 · Columbus, OH 43215 · (800) 555-0142",
            "body": f"""{_d(-4)}

Alex Rivera
1420 Maple Street, Apt 3B
Sacramento, CA 95814

RE: Original Creditor: Brightline Wireless
Account Number: NRP-4471-2290
Balance Due: $1,284.37

Dear Alex Rivera,

This letter is to inform you that the above account has been placed with
our office for collection. Our records show you owe $1,284.37.

  Itemization as of the charge-off date:
    Amount owed on charge-off date ........ $1,102.50
    Interest since charge-off ............. +   $96.87
    Fees since charge-off ................. +   $85.00
    Total amount of the debt now .......... $1,284.37

How can you dispute the debt?
Call or write to us by {_d(26)} to dispute all or part of the debt.
If you do not, we will assume that our information is correct.
If you write to us by {_d(26)}, we must stop collection on any
amount you dispute until we send you information that shows you owe
the debt.

You may be eligible for a settlement. Pay $899.00 by {_d(14)} to
resolve this account in full.

This communication is from a debt collector. This is an attempt to
collect a debt and any information obtained will be used for that purpose.

Sincerely,
Collections Department""",
        },
        "debt_followup": {
            "label": "Second notice (same debt)",
            "blurb": "Shows memory: links to the first collector letter",
            "header": "NORTHGATE RECOVERY PARTNERS, LLC",
            "sub": "PO Box 55210 · Columbus, OH 43215 · (800) 555-0142",
            "body": f"""{_d(-1)}

Alex Rivera
1420 Maple Street, Apt 3B
Sacramento, CA 95814

SECOND NOTICE — ACCOUNT PAST DUE

Account Number: NRP-4471-2290
Original Creditor: Brightline Wireless
Current Balance: $1,341.12

Dear Alex Rivera,

We have not received payment or a response regarding the account above.
Additional interest has accrued. If this balance remains unresolved,
our client may consider further options, which could include reporting
this account to one or more credit bureaus.

To avoid further action, please contact our office within 10 days of
the date of this letter to arrange payment.

You may pay by phone at the number above or by mail to the address above.

This communication is from a debt collector.

Collections Department""",
        },
        "irs_scam": {
            "label": "“IRS” final notice",
            "blurb": "Threatens arrest unless you pay with gift cards",
            "header": "FEDERAL TAX ENFORCEMENT DIVISION",
            "sub": "Department of Tax Recovery · Washington",
            "body": f"""FINAL NOTICE BEFORE LEGAL ACTION          Case ID: FT-88213-XA

Dear Taxpayer,

Our records indicate you have an UNPAID FEDERAL TAX LIABILITY of
$4,870.00 for tax years 2023-2024. Multiple attempts to contact you
have failed.

A WARRANT FOR YOUR ARREST will be issued and your bank accounts,
wages and property will be SEIZED within 48 HOURS unless payment
is received.

To stop enforcement action immediately you must call our Resolution
Officer at (202) 555-0199 TODAY. Payment must be made using Apple or
Google Play gift cards or Bitcoin to avoid processing delays.

DO NOT IGNORE THIS NOTICE. DO NOT DISCUSS THIS MATTER WITH ANYONE,
including your bank, as this may delay resolution.

Deadline: {_d(2)}

Officer M. Daniels, Badge #4471
Federal Tax Enforcement""",
        },
        "rent_increase": {
            "label": "Rent increase notice",
            "blurb": "Landlord raises rent 18% with 30 days notice",
            "header": "OAKRIDGE PROPERTY MANAGEMENT",
            "sub": "880 Capitol Mall, Suite 200 · Sacramento, CA 95814",
            "body": f"""{_d(-2)}

To: Alex Rivera, Tenant
Premises: 1420 Maple Street, Apt 3B, Sacramento, CA 95814

NOTICE OF CHANGE IN TERMS OF TENANCY

You are hereby notified that, effective {_d(28)}, the monthly rent for
the premises you occupy will be increased from $1,950.00 to $2,300.00
per month (an increase of $350.00).

All other terms of your tenancy remain unchanged.

Rent is due on the first day of each month. Failure to pay the new
amount when due may result in a Three-Day Notice to Pay Rent or Quit.

The building was constructed in 1998.

If you have questions, contact the management office at (916) 555-0187.

Oakridge Property Management, Agent for Owner""",
        },
        "medical_bill": {
            "label": "Hospital bill",
            "blurb": "A $2,340 ER bill after insurance",
            "header": "ST. BRIDGET REGIONAL MEDICAL CENTER",
            "sub": "Patient Financial Services · (916) 555-0120",
            "body": f"""STATEMENT DATE: {_d(-6)}       GUARANTOR: Alex Rivera
ACCOUNT #: 20-7781034          PATIENT: Alex Rivera

DATE OF SERVICE   DESCRIPTION                          CHARGES
{_d(-52)[:-6]}     EMERGENCY DEPT VISIT LVL 4           $3,410.00
                  CT HEAD W/O CONTRAST                 $2,180.00
                  LAB SERVICES                           $612.00
                  PHARMACY                               $208.00
                                                       ---------
                  TOTAL CHARGES                        $6,410.00
                  INSURANCE PAYMENT (BlueHarbor)      -$3,120.00
                  CONTRACTUAL ADJUSTMENT                -$950.00

                  PATIENT BALANCE DUE                  $2,340.00

PAYMENT DUE BY: {_d(21)}

Accounts not paid within 90 days may be referred to an outside
collection agency.

Financial assistance may be available. Ask us about our Financial
Assistance Policy or visit our website.

Please detach and return the bottom portion with your payment.""",
        },
    }


def list_samples() -> list[dict]:
    return [{"id": k, "label": v["label"], "blurb": v["blurb"]} for k, v in _letters().items()]


def _font(size: int, bold: bool = False):
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else []) + ["DejaVuSansMono.ttf", "consola.ttf", "cour.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_sample(sample_id: str) -> bytes:
    letter = _letters()[sample_id]
    w, margin = 1275, 90
    lines = letter["body"].splitlines()
    body_font, head_font, sub_font = _font(24), _font(38, bold=True), _font(20)
    h = 300 + len(lines) * 34 + 120
    img = Image.new("RGB", (w, h), (252, 250, 244))
    d = ImageDraw.Draw(img)
    d.text((margin, 70), letter["header"], fill=(25, 35, 60), font=head_font)
    d.text((margin, 125), letter["sub"], fill=(80, 80, 90), font=sub_font)
    d.line((margin, 170, w - margin, 170), fill=(25, 35, 60), width=3)
    y = 210
    for line in lines:
        d.text((margin, y), line, fill=(30, 30, 30), font=body_font)
        y += 34
    # A touch of realism: slight rotation, blur and paper noise like a phone photo.
    rng = random.Random(sample_id)
    img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=True, fillcolor=(205, 200, 190))
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def sample_text(sample_id: str) -> str:
    letter = _letters()[sample_id]
    return f"{letter['header']}\n{letter['sub']}\n\n{letter['body']}"

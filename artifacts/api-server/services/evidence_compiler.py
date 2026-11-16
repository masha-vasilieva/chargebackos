"""PDF bytes stay in memory. Synthetic dossiers cannot be used for representment."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


def compile_demo_pdf(dispute):
    output = BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output, pagesize=A4, title="Synthetic demo dossier", author="Chargeback OS", rightMargin=40, leftMargin=40)
    p = lambda text: Paragraph(escape(str(text)), styles["BodyText"])
    story = [
        Paragraph("CHARGEBACK OS / DEMO DOSSIER", styles["Title"]),
        p("SYNTHETIC SAMPLE — NOT VALID PAYMENT EVIDENCE"),
        Spacer(1, 20),
        p(f"Dispute: {dispute['id']} | Merchant: {dispute['merchant']}"),
        p(f"Amount: {dispute['amount']:.2f} USD | Reason: {dispute['reason']}"),
        Spacer(1, 18), Paragraph("1. Telemetry summary", styles["Heading1"]),
    ]
    missing = not dispute["telemetry"]
    rows = [["Field", "Synthetic value"], ["Signup IP", "192.0.2.14" if not missing else "Missing"],
            ["Checkout IP", "192.0.2.14" if not missing else "Missing"],
            ["Post-renewal IP", "192.0.2.14" if not missing else "Missing"],
            ["Device", "demo-device-01" if not missing else "Missing"],
            ["Terms acceptance", "Synthetic clickwrap event" if not missing else "Missing"],
            ["Evidence source", "Demo generator — not a live telemetry provider"]]
    table = Table([[p(c) for c in row] for row in rows], colWidths=[145, 365])
    table.setStyle(TableStyle([("GRID", (0,0),(-1,-1), .5, colors.black), ("BACKGROUND",(0,0),(-1,0),colors.lightgrey), ("VALIGN",(0,0),(-1,-1),"TOP"), ("BOTTOMPADDING",(0,0),(-1,-1),10), ("TOPPADDING",(0,0),(-1,-1),10)]))
    story.extend([table, Spacer(1,20), p("No Visa CE 3.0 or Mastercard eligibility is asserted. Matching IP addresses alone do not establish eligibility. Prior qualifying transactions and current scheme rules must be verified."), PageBreak(),
                  Paragraph("2. Chronological activity audit", styles["Heading1"]), p("SYNTHETIC SAMPLE — DO NOT SUBMIT"), Spacer(1,16)])
    for activity in dispute["events"]:
        story.extend([p(activity["time"]), p(activity["text"]), Spacer(1,12)])
    if missing:
        story.append(p("No telemetry available. Human review is required; evidence must not be invented."))
    story.extend([Spacer(1,20), p("This document was generated in memory for product evaluation. Nothing has been uploaded to S3 or submitted to a payment gateway.")])
    doc.build(story)
    result = output.getvalue()
    if len(result) >= 10 * 1024 * 1024:
        raise ValueError("Evidence exceeds 10 MB")
    return result

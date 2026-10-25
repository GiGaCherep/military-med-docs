import io
import os
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_PATH = "C:/Windows/Fonts/arial.ttf"
if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('Arial', FONT_PATH))
    FONT_NAME = 'Arial'
else:
    FONT_NAME = 'Helvetica' 

def generate_pdf_report(outpatients, medicals, meds, start_date, end_date, report_type="all"):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontName=FONT_NAME, fontSize=16, alignment=1, spaceAfter=20)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName=FONT_NAME, fontSize=12, spaceAfter=10, spaceBefore=15)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontName=FONT_NAME, fontSize=8, leading=10)
    header_cell_style = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontName=FONT_NAME, fontSize=8, leading=10, textColor=colors.white)

    date_range = f"с {start_date} по {end_date}" if start_date or end_date else "за всё время"
    elements.append(Paragraph(f"Сводный отчет ({date_range})", title_style))
    elements.append(Spacer(1, 10))

    # 1. Амбулаторные больные
    if report_type in ["all", "outpatient"]:
        elements.append(Paragraph(f"Амбулаторный приём ({len(outpatients)})", h2_style))
        if outpatients:
            data = [[Paragraph("№", header_cell_style), Paragraph("Дата", header_cell_style), Paragraph("Военнослужащий", header_cell_style), Paragraph("Диагноз", header_cell_style), Paragraph("Исход", header_cell_style)]]
            for i, r in enumerate(outpatients, 1):
                data.append([Paragraph(str(i), cell_style), Paragraph(r.visit_date.strftime('%d.%m.%Y'), cell_style), Paragraph(r.serviceman.full_name, cell_style), Paragraph(r.diagnosis, cell_style), Paragraph(r.outcome or '—', cell_style)])
            t = Table(data, colWidths=[20, 60, 150, 160, 80])
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#005f73")), ('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('FONTNAME', (0, 0), (-1, -1), FONT_NAME), ('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            elements.append(t)
        else:
            elements.append(Paragraph("Нет данных.", styles['Normal']))

    # 2. Медзаписи
    if report_type in ["all", "medical"]:
        elements.append(Paragraph(f"Медицинские записи ({len(medicals)})", h2_style))
        if medicals:
            data = [[Paragraph("№", header_cell_style), Paragraph("Дата", header_cell_style), Paragraph("Военнослужащий", header_cell_style), Paragraph("Диагноз", header_cell_style), Paragraph("Помощь", header_cell_style)]]
            for i, r in enumerate(medicals, 1):
                data.append([Paragraph(str(i), cell_style), Paragraph(r.created_at.strftime('%d.%m.%Y'), cell_style), Paragraph(r.serviceman.full_name, cell_style), Paragraph(r.diagnosis, cell_style), Paragraph(r.treatment or '—', cell_style)])
            t = Table(data, colWidths=[20, 60, 150, 130, 100])
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#005f73")), ('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('FONTNAME', (0, 0), (-1, -1), FONT_NAME), ('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            elements.append(t)
        else:
            elements.append(Paragraph("Нет данных.", styles['Normal']))

    # 3. Выдача лекарств
    if report_type in ["all", "meds"]:
        elements.append(Paragraph(f"Выдача медикаментов ({len(meds)})", h2_style))
        if meds:
            data = [[Paragraph("№", header_cell_style), Paragraph("Дата", header_cell_style), Paragraph("Препарат", header_cell_style), Paragraph("Кому", header_cell_style), Paragraph("Кол-во", header_cell_style)]]
            for i, t in enumerate(meds, 1):
                sm_name = t.serviceman.full_name if t.serviceman else '—'
                data.append([Paragraph(str(i), cell_style), Paragraph(t.created_at.strftime('%d.%m.%Y'), cell_style), Paragraph(t.medication.name, cell_style), Paragraph(sm_name, cell_style), Paragraph(f"{abs(t.amount)} шт.", cell_style)])
            t = Table(data, colWidths=[20, 60, 150, 140, 50])
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#b00020")), ('ALIGN', (0, 0), (-1, -1), 'LEFT'), ('FONTNAME', (0, 0), (-1, -1), FONT_NAME), ('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            elements.append(t)
        else:
            elements.append(Paragraph("Нет данных.", styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer
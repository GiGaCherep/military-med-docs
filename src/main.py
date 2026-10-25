import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

# --- НАСТРОЙКА ПУТЕЙ ---
_file = Path(__file__).resolve()
for p in (str(_file.parent.parent), str(_file.parent)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# -----------------------

# ИМПОРТЫ ВАШИХ МОДУЛЕЙ И БИБЛИОТЕК
import pdf_reports
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, status, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import or_, func, and_
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user_web, require_admin
from models import (
    AuditLog, Document, DocumentApproval, DocumentSignature,
    Notification, Role, Serviceman, User, MedicalRecord, OutpatientRecord,
    Medication, StockTransaction, MedicationCategory, StorageLocation
)
import pharma_service
from schemas import CLASSIFICATIONS, DOC_TYPES
from security import (
    ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, get_password_hash, verify_password,
)

# --- Настройки путей ---
ROOT_DIR = _file.parent.parent
TEMPLATES_DIR = ROOT_DIR / "templates"
STATIC_DIR = ROOT_DIR / "static"
UPLOADS_DIR = ROOT_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))
app = FastAPI(title="Military Med Docs")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# --- ПЕРЕХВАТ ОШИБКИ 401 (Истечение сессии) ---
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401 and not request.url.path.startswith("/api/"):
        return RedirectResponse(url="/?message=Сессия+истекла.+Пожалуйста,+войдите+снова.", status_code=303)
    return await http_exception_handler(request, exc)

# --- Константы ---
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".docx"}
MAX_FILE_SIZE = 20 * 1024 * 1024

STATUS_LABELS = {
    "draft": "Черновик",
    "review": "На проверке",
    "approved": "Опубликован",
    "rejected": "Отклонён",
}

CLASSIFICATION_ACCESS = {
    "ДСП": {"admin", "doctor", "commander"},
    "Секретно": {"admin"},
    "Совершенно секретно": {"admin"},
}

TRANSITIONS = {
    ("draft", "review"): {"admin", "doctor", "commander"},
    ("review", "approved"): {"admin", "commander"},
    ("review", "rejected"): {"admin", "commander"},
    ("review", "draft"): {"admin", "commander"},
}


# --- Вспомогательные функции ---
def render(template: str, request: Request, **context):
    return TEMPLATES.TemplateResponse(request=request, name=template, context=context)

def can_transition(user: User, from_status: str, to_status: str) -> bool:
    allowed = TRANSITIONS.get((from_status, to_status))
    return bool(allowed and user.role and user.role.name in allowed)

def can_view_classification(user: User, classification: str) -> bool:
    allowed_roles = CLASSIFICATION_ACCESS.get(classification)
    return bool(allowed_roles and user.role and user.role.name in allowed_roles)

def audit(db: Session, actor: User, action: str, target_type: str = None, target_id: int = None, details: dict = None):
    db.add(AuditLog(
        actor_id=actor.id, action=action, target_type=target_type,
        target_id=target_id, details=json.dumps(details, ensure_ascii=False) if details else None
    ))

def notify(db: Session, user_id: int, document_id: int | None, message: str):
    db.add(Notification(user_id=user_id, document_id=document_id, message=message))

def compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def utc_now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# --- Базовые страницы и авторизация ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, message: str = ""):
    return render("index.html", request, success=message)

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return render("index.html", request, error="Неверный логин или пароль")
    if not user.is_active:
        return render("index.html", request, error="Учётная запись деактивирована")
    
    token = create_access_token({"sub": user.username, "role": user.role.name})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=token, httponly=True, secure=False, samesite="lax", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    return response

@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    accessible = [c for c in CLASSIFICATIONS if can_view_classification(current_user, c)]
    base_query = db.query(Document).filter(Document.classification.in_(accessible))
    
    total = base_query.count()
    last_week = base_query.filter(Document.created_at >= utc_now() - timedelta(days=7)).count()
    on_review = base_query.filter(Document.status == "review").count()
    recent = base_query.options(joinedload(Document.author)).order_by(Document.created_at.desc()).limit(10).all()
    unread_notifications = db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_read.is_(False)).count()

    return render("dashboard.html", request, current_user=current_user, stats={"total": total, "last_week": last_week, "on_review": on_review}, documents=recent, status_labels=STATUS_LABELS, unread_notifications=unread_notifications, message=request.query_params.get("message"), error=request.query_params.get("error"))


# --- ОТЧЕТЫ ---
@app.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request, 
    start_date: str = "", 
    end_date: str = "", 
    report_type: str = "all",
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    start = None
    end = None
    if start_date:
        try: start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError: pass
    if end_date:
        try: end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError: pass

    outpatients = []
    medicals = []
    meds = []

    if report_type in ["all", "outpatient"]:
        q_outpatient = db.query(OutpatientRecord).options(joinedload(OutpatientRecord.serviceman), joinedload(OutpatientRecord.doctor))
        if start: q_outpatient = q_outpatient.filter(OutpatientRecord.visit_date >= start)
        if end: q_outpatient = q_outpatient.filter(OutpatientRecord.visit_date <= end)
        outpatients = q_outpatient.order_by(OutpatientRecord.visit_date.desc()).all()

    if report_type in ["all", "medical"]:
        q_medical = db.query(MedicalRecord).options(joinedload(MedicalRecord.serviceman), joinedload(MedicalRecord.doctor))
        if start: q_medical = q_medical.filter(func.date(MedicalRecord.created_at) >= start)
        if end: q_medical = q_medical.filter(func.date(MedicalRecord.created_at) <= end)
        medicals = q_medical.order_by(MedicalRecord.created_at.desc()).all()

    if report_type in ["all", "meds"]:
        q_meds = db.query(StockTransaction).options(joinedload(StockTransaction.medication), joinedload(StockTransaction.serviceman), joinedload(StockTransaction.user)).filter(StockTransaction.amount < 0)
        if start: q_meds = q_meds.filter(func.date(StockTransaction.created_at) >= start)
        if end: q_meds = q_meds.filter(func.date(StockTransaction.created_at) <= end)
        meds = q_meds.order_by(StockTransaction.created_at.desc()).all()

    return render(
        "reports.html", request,
        current_user=current_user,
        start_date=start_date,
        end_date=end_date,
        report_type=report_type,
        outpatients=outpatients,
        medicals=medicals,
        meds=meds
    )

@app.get("/reports/export")
async def export_reports_pdf(
    start_date: str = "", 
    end_date: str = "", 
    report_type: str = "all",
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    start = None
    end = None
    if start_date:
        try: start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError: pass
    if end_date:
        try: end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError: pass

    outpatients = []
    medicals = []
    meds = []

    if report_type in ["all", "outpatient"]:
        q_outpatient = db.query(OutpatientRecord).options(joinedload(OutpatientRecord.serviceman))
        if start: q_outpatient = q_outpatient.filter(OutpatientRecord.visit_date >= start)
        if end: q_outpatient = q_outpatient.filter(OutpatientRecord.visit_date <= end)
        outpatients = q_outpatient.order_by(OutpatientRecord.visit_date.desc()).all()

    if report_type in ["all", "medical"]:
        q_medical = db.query(MedicalRecord).options(joinedload(MedicalRecord.serviceman))
        if start: q_medical = q_medical.filter(func.date(MedicalRecord.created_at) >= start)
        if end: q_medical = q_medical.filter(func.date(MedicalRecord.created_at) <= end)
        medicals = q_medical.order_by(MedicalRecord.created_at.desc()).all()

    if report_type in ["all", "meds"]:
        q_meds = db.query(StockTransaction).options(joinedload(StockTransaction.medication), joinedload(StockTransaction.serviceman), joinedload(StockTransaction.user)).filter(StockTransaction.amount < 0)
        if start: q_meds = q_meds.filter(func.date(StockTransaction.created_at) >= start)
        if end: q_meds = q_meds.filter(func.date(StockTransaction.created_at) <= end)
        meds = q_meds.order_by(StockTransaction.created_at.desc()).all()

    pdf_buffer = pdf_reports.generate_pdf_report(outpatients, medicals, meds, start_date, end_date, report_type)

    headers = {
        'Content-Disposition': 'attachment; filename="report.pdf"'
    }
    return StreamingResponse(pdf_buffer, media_type="application/pdf", headers=headers)


# --- Документы ---
@app.get("/documents", response_class=HTMLResponse)
async def documents_list(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    accessible = [c for c in CLASSIFICATIONS if can_view_classification(current_user, c)]
    documents = (
        db.query(Document).options(joinedload(Document.author))
        .filter(Document.classification.in_(accessible))
        .order_by(Document.created_at.desc()).all()
    )
    return render("documents.html", request, current_user=current_user, documents=documents, status_labels=STATUS_LABELS)

@app.get("/documents/new", response_class=HTMLResponse)
async def document_new_form(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    users = db.query(User).options(joinedload(User.role)).filter(User.is_active.is_(True)).all()
    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    return render("document_new.html", request, current_user=current_user, users=users, servicemen=servicemen, doc_types=DOC_TYPES, classifications=CLASSIFICATIONS, error=None)

@app.post("/documents/new", response_class=HTMLResponse)
async def document_new_submit(
    request: Request, title: str = Form(...), doc_type: str = Form(...),
    doc_number: str = Form(""), doc_date: str = Form(""), unit: str = Form(""),
    military_unit: str = Form(""), classification: str = Form(...),
    author_id: int = Form(...), serviceman_id: str = Form(""), description: str = Form(""),
    file: UploadFile | None = File(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    users = db.query(User).options(joinedload(User.role)).filter(User.is_active.is_(True)).all()
    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    
    def error_page(msg: str):
        return render("document_new.html", request, current_user=current_user, users=users, servicemen=servicemen, doc_types=DOC_TYPES, classifications=CLASSIFICATIONS, error=msg)

    if len(title) < 3: return error_page("Название слишком короткое")
    if not can_view_classification(current_user, classification): return error_page("Нет прав устанавливать этот гриф")

    file_name = file_path = file_size = file_mime = None
    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS: return error_page("Недопустимый формат файла")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE: return error_page("Файл больше 20 МБ")
        
        safe_name = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOADS_DIR / safe_name
        dest.write_bytes(content)
        file_name, file_path, file_size, file_mime = file.filename, f"uploads/{safe_name}", len(content), file.content_type

    doc_date_value = None
    if doc_date:
        try: doc_date_value = datetime.strptime(doc_date, "%Y-%m-%d").date()
        except ValueError: return error_page("Неверный формат даты")

    doc = Document(
        title=title, doc_type=doc_type, doc_number=doc_number or None, doc_date=doc_date_value,
        unit=unit or None, military_unit=military_unit or None, classification=classification,
        author_id=author_id, serviceman_id=int(serviceman_id) if serviceman_id else None,
        file_name=file_name, file_path=file_path, file_size=file_size, file_mime=file_mime,
        description=description or None, status="draft"
    )
    db.add(doc)
    db.flush()
    audit(db, current_user, "document.create", "document", doc.id, {"title": title})
    db.commit()

    return RedirectResponse(url="/dashboard?message=Документ+зарегистрирован", status_code=303)

@app.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_view(doc_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    doc = (
        db.query(Document)
        .options(
            joinedload(Document.author).joinedload(User.role),
            joinedload(Document.approvals).joinedload(DocumentApproval.approver),
            joinedload(Document.signatures).joinedload(DocumentSignature.signer),
            joinedload(Document.serviceman)
        )
        .filter(Document.id == doc_id).first()
    )
    if not doc: return RedirectResponse(url="/dashboard?error=Документ+не+найден", status_code=303)
    if not can_view_classification(current_user, doc.classification): return RedirectResponse(url="/dashboard?error=Нет+доступа", status_code=303)

    audit(db, current_user, "document.view", "document", doc.id)
    db.commit()

    available_transitions = [
        (f, t) for (f, t), roles in TRANSITIONS.items()
        if f == doc.status and current_user.role.name in roles
    ]
    can_sign = (
        doc.status == "approved"
        and not any(s.signer_id == current_user.id for s in doc.signatures)
        and current_user.role.name in {"admin", "commander"}
    )

    return render("document_view.html", request, current_user=current_user, doc=doc, status_labels=STATUS_LABELS, available_transitions=available_transitions, can_sign=can_sign, error=request.query_params.get("error"), message=request.query_params.get("message"))

@app.post("/documents/{doc_id}/status")
async def document_change_status(doc_id: int, to_status: str = Form(...), comment: str = Form(""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    doc = db.query(Document).options(joinedload(Document.author)).filter(Document.id == doc_id).first()
    if not doc: return RedirectResponse(url="/dashboard?error=Документ+не+найден", status_code=303)
    if not can_view_classification(current_user, doc.classification): return RedirectResponse(url=f"/documents/{doc_id}?error=Нет+доступа", status_code=303)

    from_status = doc.status
    if not can_transition(current_user, from_status, to_status): return RedirectResponse(url=f"/documents/{doc_id}?error=Недопустимый+переход", status_code=303)
    if to_status in {"approved", "rejected"} and current_user.id == doc.author_id: return RedirectResponse(url=f"/documents/{doc_id}?error=Нельзя+согласовывать+свой+документ", status_code=303)
    if to_status in {"rejected", "draft"} and from_status == "review":
        if not comment.strip() or len(comment.strip()) < 5: return RedirectResponse(url=f"/documents/{doc_id}?error=Комментарий+обязателен", status_code=303)

    db.add(DocumentApproval(document_id=doc.id, approver_id=current_user.id, from_status=from_status, to_status=to_status, comment=comment.strip() or None))
    doc.status = to_status

    if doc.author_id != current_user.id:
        msg = f"Статус документа «{doc.title}» изменён: {STATUS_LABELS.get(from_status, from_status)} → {STATUS_LABELS.get(to_status, to_status)}"
        if comment.strip(): msg += f". Комментарий: {comment.strip()}"
        notify(db, doc.author_id, doc.id, msg)

    audit(db, current_user, "document.status_change", "document", doc.id, {"from": from_status, "to": to_status, "comment": comment.strip() or None})
    db.commit()
    return RedirectResponse(url=f"/documents/{doc_id}?message=Статус+изменён", status_code=303)

@app.post("/documents/{doc_id}/sign")
async def document_sign(doc_id: int, full_name: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc: return RedirectResponse(url="/dashboard?error=Документ+не+найден", status_code=303)
    if not can_view_classification(current_user, doc.classification): return RedirectResponse(url=f"/documents/{doc_id}?error=Нет+доступа", status_code=303)
    if doc.status != "approved": return RedirectResponse(url=f"/documents/{doc_id}?error=Подписать+можно+только+опубликованный", status_code=303)
    if current_user.role.name not in {"admin", "commander"}: return RedirectResponse(url=f"/documents/{doc_id}?error=Нет+прав", status_code=303)
    if any(s.signer_id == current_user.id for s in doc.signatures): return RedirectResponse(url=f"/documents/{doc_id}?error=Вы+уже+подписали", status_code=303)
    if not full_name.strip(): return RedirectResponse(url=f"/documents/{doc_id}?error=ФИО+обязательно", status_code=303)

    if doc.file_path: content_hash = compute_hash(ROOT_DIR / doc.file_path)
    else:
        h = hashlib.sha256()
        h.update(f"{doc.title}|{doc.doc_type}|{doc.classification}|{doc.id}".encode())
        content_hash = h.hexdigest()

    db.add(DocumentSignature(document_id=doc.id, signer_id=current_user.id, signer_full_name=full_name.strip(), signer_role=current_user.role.name, content_hash=content_hash))
    audit(db, current_user, "document.sign", "document", doc.id, {"hash": content_hash})
    db.commit()
    return RedirectResponse(url=f"/documents/{doc_id}?message=Документ+подписан", status_code=303)


# --- Справочник военнослужащих ---
@app.get("/servicemen", response_class=HTMLResponse)
async def servicemen_list(request: Request, q: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    query = db.query(Serviceman).filter(Serviceman.is_active.is_(True))
    if q:
        search = f"%{q}%"
        query = query.filter(
            or_(
                Serviceman.full_name.ilike(search), 
                Serviceman.service_number.ilike(search), 
                Serviceman.unit.ilike(search),
                Serviceman.callsign.ilike(search)
            )
        )
    servicemen = query.order_by(Serviceman.full_name).all()
    return render("servicemen.html", request, current_user=current_user, servicemen=servicemen, q=q)

@app.get("/servicemen/new", response_class=HTMLResponse)
async def serviceman_new_form(request: Request, current_user: User = Depends(get_current_user_web)):
    return render("serviceman_new.html", request, current_user=current_user, error=None)

@app.post("/servicemen/new", response_class=HTMLResponse)
async def serviceman_new_submit(
    request: Request, full_name: str = Form(...), rank: str = Form(...), service_number: str = Form(""),
    callsign: str = Form(""), unit: str = Form(...), military_unit: str = Form(...), blood_type: str = Form(""), allergies: str = Form(""),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    # Проверка на уникальность, только если номер указан
    if service_number and db.query(Serviceman).filter(Serviceman.service_number == service_number).first():
        return render("serviceman_new.html", request, current_user=current_user, error="Номер жетона уже существует")

    sm = Serviceman(
        full_name=full_name, rank=rank, 
        service_number=service_number or None, 
        callsign=callsign or None, 
        unit=unit, military_unit=military_unit, 
        blood_type=blood_type or None, allergies=allergies or None
    )
    db.add(sm)
    db.flush()
    audit(db, current_user, "serviceman.create", "serviceman", sm.id, {"name": full_name})
    db.commit()
    return RedirectResponse(url="/servicemen?message=Военнослужащий+добавлен", status_code=303)

@app.get("/servicemen/{serviceman_id}/edit", response_class=HTMLResponse)
async def serviceman_edit_form(
    serviceman_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web)
):
    sm = db.query(Serviceman).filter(Serviceman.id == serviceman_id).first()
    if not sm:
        return RedirectResponse(url="/servicemen?error=Военнослужащий+не+найден", status_code=303)
    return render("serviceman_edit.html", request, current_user=current_user, sm=sm, error=None)

@app.post("/servicemen/{serviceman_id}/edit", response_class=HTMLResponse)
async def serviceman_edit_submit(
    serviceman_id: int,
    request: Request,
    full_name: str = Form(...),
    rank: str = Form(...),
    service_number: str = Form(""),
    callsign: str = Form(""),
    unit: str = Form(...),
    military_unit: str = Form(...),
    blood_type: str = Form(""),
    allergies: str = Form(""),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web)
):
    sm = db.query(Serviceman).filter(Serviceman.id == serviceman_id).first()
    if not sm:
        return RedirectResponse(url="/servicemen?error=Военнослужащий+не+найден", status_code=303)

    # Проверка уникальности номера жетона (если он указан)
    if service_number:
        existing = db.query(Serviceman).filter(
            Serviceman.service_number == service_number,
            Serviceman.id != serviceman_id
        ).first()
        if existing:
            return render("serviceman_edit.html", request, current_user=current_user, sm=sm, error="Номер жетона уже занят другим военнослужащим")

    sm.full_name = full_name
    sm.rank = rank
    sm.service_number = service_number or None
    sm.callsign = callsign or None
    sm.unit = unit
    sm.military_unit = military_unit
    sm.blood_type = blood_type or None
    sm.allergies = allergies or None
    sm.is_active = bool(is_active)

    audit(db, current_user, "serviceman.edit", "serviceman", sm.id, {"name": full_name})
    db.commit()

    return RedirectResponse(url=f"/servicemen/{serviceman_id}?message=Данные+пациента+обновлены", status_code=303)

@app.get("/servicemen/{serviceman_id}", response_class=HTMLResponse)
async def serviceman_detail(
    serviceman_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    sm = (
        db.query(Serviceman)
        .options(
            joinedload(Serviceman.documents).joinedload(Document.author),
            joinedload(Serviceman.medical_records).joinedload(MedicalRecord.doctor),
            joinedload(Serviceman.stock_transactions).joinedload(StockTransaction.medication)
        )
        .filter(Serviceman.id == serviceman_id)
        .first()
    )
    
    if not sm:
        return RedirectResponse(url="/servicemen?error=Военнослужащий+не+найден", status_code=303)

    accessible_docs = [
        doc for doc in sm.documents 
        if can_view_classification(current_user, doc.classification)
    ]
    
    accessible_docs.sort(key=lambda x: x.created_at, reverse=True)
    medical_records = sorted(sm.medical_records, key=lambda x: x.created_at, reverse=True)

    triage_labels = {
        "red": ("Неотложная помощь", "badge-danger"),
        "black": ("Инфекция / Изоляция", "badge-dark"),
        "yellow": ("Сан. обработка", "badge-warning"),
        "blue": ("Радиационное поражение", "badge-primary"),
    }

    medications = db.query(Medication).filter(Medication.current_stock > 0).order_by(Medication.name).all()
    dispensed_meds = [t for t in sm.stock_transactions if t.amount < 0]

    return render(
        "serviceman_detail.html", request,
        current_user=current_user,
        sm=sm,
        documents=accessible_docs,
        medical_records=medical_records,
        dispensed_meds=dispensed_meds,
        medications=medications,
        status_labels=STATUS_LABELS,
        triage_labels=triage_labels,
        error=request.query_params.get("error"),
        message=request.query_params.get("message"),
    )

@app.post("/servicemen/{serviceman_id}/dispense")
async def serviceman_dispense_medication(
    serviceman_id: int,
    medication_id: int = Form(...),
    amount: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web)
):
    sm = db.query(Serviceman).filter(Serviceman.id == serviceman_id).first()
    med = db.query(Medication).filter(Medication.id == medication_id).first()
    
    if not sm or not med:
        return RedirectResponse(url=f"/servicemen/{serviceman_id}?error=Пациент+или+препарат+не+найден", status_code=303)
    
    if amount <= 0:
        return RedirectResponse(url=f"/servicemen/{serviceman_id}?error=Количество+должно+быть+больше+0", status_code=303)
        
    if med.current_stock < amount:
        return RedirectResponse(url=f"/servicemen/{serviceman_id}?error=Недостаточно+препарата+на+складе+(остаток:+{med.current_stock})", status_code=303)
        
    med.current_stock -= amount
    
    txn = StockTransaction(
        medication_id=med.id,
        user_id=current_user.id,
        serviceman_id=sm.id,
        amount=-amount, 
        reason=f"Назначение: {reason}"
    )
    db.add(txn)
    
    audit(db, current_user, "medication.dispense", "medication", med.id, {"serviceman": sm.full_name, "amount": amount})
    db.commit()
    
    return RedirectResponse(url=f"/servicemen/{serviceman_id}?message=Лекарство+успешно+списано", status_code=303)


# --- Медицинские записи и журналы ---
@app.get("/medical/new", response_class=HTMLResponse)
async def medical_new_form(
    request: Request, serviceman_id: int = None,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    selected_sm = db.query(Serviceman).filter(Serviceman.id == serviceman_id).first() if serviceman_id else None
    
    return render(
        "medical_new.html", request,
        current_user=current_user,
        servicemen=servicemen,
        selected_sm=selected_sm,
        error=None
    )

@app.post("/medical/new", response_class=HTMLResponse)
async def medical_new_submit(
    request: Request, serviceman_id: int = Form(...), record_type: str = Form(...),
    diagnosis: str = Form(...), icd_code: str = Form(""), complaints: str = Form(""),
    treatment: str = Form(""), outcome: str = Form(""), triage_color: str = Form(""),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    sm = db.query(Serviceman).filter(Serviceman.id == serviceman_id).first()
    if not sm:
        return RedirectResponse(url="/medical/new?error=Военнослужащий+не+найден", status_code=303)

    is_infectious = (record_type == "infection")

    record = MedicalRecord(
        serviceman_id=serviceman_id, doctor_id=current_user.id,
        record_type=record_type, is_infectious=is_infectious,
        diagnosis=diagnosis, icd_code=icd_code or None,
        complaints=complaints or None, treatment=treatment or None,
        outcome=outcome or None, triage_color=triage_color or None,
    )
    db.add(record)
    db.flush()
    audit(db, current_user, "medical_record.create", "medical_record", record.id, {"diagnosis": diagnosis})
    db.commit()

    return RedirectResponse(url="/medical/journals?message=Запись+добавлена", status_code=303)

@app.get("/medical/journals", response_class=HTMLResponse)
async def journals_index(request: Request, current_user: User = Depends(get_current_user_web)):
    return render("journals_index.html", request, current_user=current_user)

@app.get("/medical/journals/admission", response_class=HTMLResponse)
async def journal_admission(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    records = (
        db.query(MedicalRecord)
        .options(joinedload(MedicalRecord.serviceman), joinedload(MedicalRecord.doctor))
        .filter(MedicalRecord.record_type == "admission")
        .order_by(MedicalRecord.created_at.desc()).all()
    )
    return render("journal_admission.html", request, current_user=current_user, records=records)

@app.get("/medical/journals/infection", response_class=HTMLResponse)
async def journal_infection(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    records = (
        db.query(MedicalRecord)
        .options(joinedload(MedicalRecord.serviceman), joinedload(MedicalRecord.doctor))
        .filter(MedicalRecord.is_infectious.is_(True))
        .order_by(MedicalRecord.created_at.desc()).all()
    )
    return render("journal_infection.html", request, current_user=current_user, records=records)

@app.get("/medical/journals/outside", response_class=HTMLResponse)
async def journal_outside(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    records = (
        db.query(MedicalRecord)
        .options(joinedload(MedicalRecord.serviceman), joinedload(MedicalRecord.doctor))
        .filter(MedicalRecord.record_type == "outside")
        .order_by(MedicalRecord.created_at.desc()).all()
    )
    return render("journal_outside.html", request, current_user=current_user, records=records)


# --- Журнал амбулаторных больных ---
@app.get("/medical/journals/outpatient", response_class=HTMLResponse)
async def journal_outpatient(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    records = (
        db.query(OutpatientRecord)
        .options(joinedload(OutpatientRecord.serviceman), joinedload(OutpatientRecord.doctor))
        .order_by(OutpatientRecord.visit_date.desc(), OutpatientRecord.id.desc())
        .all()
    )
    return render("journal_outpatient.html", request, current_user=current_user, records=records, message=request.query_params.get("message"))

@app.get("/medical/journals/outpatient/new", response_class=HTMLResponse)
async def outpatient_new_form(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    return render(
        "outpatient_form.html", request, 
        current_user=current_user, 
        servicemen=servicemen, 
        record=None, 
        today=date.today().isoformat(),
        error=None
    )

@app.post("/medical/journals/outpatient/new", response_class=HTMLResponse)
async def outpatient_new_submit(
    request: Request, serviceman_id: int = Form(...), visit_date: str = Form(...),
    diagnosis: str = Form(...), icd_code: str = Form(""), complaints: str = Form(""),
    treatment: str = Form(""), outcome: str = Form(""),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    try:
        v_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
    except ValueError:
        return render("outpatient_form.html", request, current_user=current_user, servicemen=servicemen, record=None, today=visit_date, error="Неверный формат даты")

    record = OutpatientRecord(
        serviceman_id=serviceman_id, doctor_id=current_user.id, visit_date=v_date,
        diagnosis=diagnosis, icd_code=icd_code or None, complaints=complaints or None,
        treatment=treatment or None, outcome=outcome or None,
    )
    db.add(record)
    db.flush()
    audit(db, current_user, "outpatient.create", "outpatient", record.id, {"diagnosis": diagnosis})
    db.commit()

    return RedirectResponse(url="/medical/journals/outpatient?message=Запись+добавлена", status_code=303)

@app.get("/medical/journals/outpatient/{record_id}/edit", response_class=HTMLResponse)
async def outpatient_edit_form(record_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    record = db.query(OutpatientRecord).filter(OutpatientRecord.id == record_id).first()
    if not record:
        return RedirectResponse(url="/medical/journals/outpatient?error=Запись+не+найдена", status_code=303)

    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    return render(
        "outpatient_form.html", request, 
        current_user=current_user, 
        servicemen=servicemen, 
        record=record, 
        today=record.visit_date.isoformat(),
        error=None
    )

@app.post("/medical/journals/outpatient/{record_id}/edit", response_class=HTMLResponse)
async def outpatient_edit_submit(
    record_id: int, request: Request, serviceman_id: int = Form(...), visit_date: str = Form(...),
    diagnosis: str = Form(...), icd_code: str = Form(""), complaints: str = Form(""),
    treatment: str = Form(""), outcome: str = Form(""),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    record = db.query(OutpatientRecord).filter(OutpatientRecord.id == record_id).first()
    if not record:
        return RedirectResponse(url="/medical/journals/outpatient?error=Запись+не+найдена", status_code=303)

    servicemen = db.query(Serviceman).filter(Serviceman.is_active.is_(True)).order_by(Serviceman.full_name).all()
    
    try:
        v_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
    except ValueError:
        return render("outpatient_form.html", request, current_user=current_user, servicemen=servicemen, record=record, today=visit_date, error="Неверный формат даты")

    record.serviceman_id = serviceman_id
    record.visit_date = v_date
    record.diagnosis = diagnosis
    record.icd_code = icd_code or None
    record.complaints = complaints or None
    record.treatment = treatment or None
    record.outcome = outcome or None

    audit(db, current_user, "outpatient.edit", "outpatient", record.id, {"diagnosis": diagnosis})
    db.commit()

    return RedirectResponse(url="/medical/journals/outpatient?message=Запись+обновлена", status_code=303)


# --- АПТЕКА ---
@app.get("/pharmacy", response_class=HTMLResponse)
async def pharmacy_index(
    request: Request, 
    q: str = "", 
    category_id: str = "",
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    medications = []
    ai_result = None
    categories = db.query(MedicationCategory).order_by(MedicationCategory.name).all()

    query = db.query(Medication)

    selected_category = None
    if category_id and category_id.isdigit():
        selected_category = int(category_id)
        query = query.filter(Medication.category_id == selected_category)
    
    if q:
        query = query.filter(Medication.name.ilike(f"%{q}%"))
        if not query.first() and not selected_category:
            ai_result = pharma_service.search_disease_info(q)

    medications = query.order_by(Medication.name).all()

    return render(
        "pharmacy/index.html", request, 
        current_user=current_user, 
        medications=medications, 
        q=q, 
        ai_result=ai_result, 
        categories=categories, 
        selected_category=selected_category,
        message=request.query_params.get("message"),
        error=request.query_params.get("error")
    )

@app.get("/pharmacy/ai-search", response_class=HTMLResponse)
async def pharmacy_ai_search_form(request: Request, current_user: User = Depends(get_current_user_web)):
    return render("pharmacy/ai_search.html", request, current_user=current_user, result=None, symptoms="")

@app.post("/pharmacy/ai-search", response_class=HTMLResponse)
async def pharmacy_ai_search_submit(
    request: Request, symptoms: str = Form(...), 
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    result = pharma_service.search_disease_info(symptoms)
    
    for med_info in result["medications"]:
        stock_med = db.query(Medication).filter(Medication.name.ilike(f"%{med_info['name']}%")).first()
        med_info["in_stock"] = stock_med.current_stock if stock_med else 0
        med_info["stock_id"] = stock_med.id if stock_med else None
        
    return render("pharmacy/ai_search.html", request, current_user=current_user, result=result, symptoms=symptoms)

@app.get("/pharmacy/settings", response_class=HTMLResponse)
async def pharmacy_settings(
    request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    categories = db.query(MedicationCategory).order_by(MedicationCategory.name).all()
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    return render("pharmacy/settings.html", request, current_user=current_user, categories=categories, locations=locations)

@app.post("/pharmacy/settings/category")
async def add_category(name: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    if not db.query(MedicationCategory).filter(MedicationCategory.name == name).first():
        db.add(MedicationCategory(name=name))
        db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Группа+добавлена", status_code=303)

@app.post("/pharmacy/settings/location")
async def add_location(name: str = Form(...), description: str = Form(""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    if not db.query(StorageLocation).filter(StorageLocation.name == name).first():
        db.add(StorageLocation(name=name, description=description or None))
        db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Место+добавлено", status_code=303)

@app.get("/pharmacy/settings/category/{cat_id}/edit", response_class=HTMLResponse)
async def edit_category_form(cat_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    cat = db.query(MedicationCategory).filter(MedicationCategory.id == cat_id).first()
    if not cat:
        return RedirectResponse(url="/pharmacy/settings?error=Группа+не+найдена", status_code=303)
    return render("pharmacy/category_edit.html", request, current_user=current_user, cat=cat, error=None)

@app.post("/pharmacy/settings/category/{cat_id}/edit")
async def edit_category_submit(cat_id: int, request: Request, name: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    cat = db.query(MedicationCategory).filter(MedicationCategory.id == cat_id).first()
    if not cat:
        return RedirectResponse(url="/pharmacy/settings?error=Группа+не+найдена", status_code=303)
    
    if db.query(MedicationCategory).filter(MedicationCategory.name == name, MedicationCategory.id != cat_id).first():
        return render("pharmacy/category_edit.html", request, current_user=current_user, cat=cat, error="Группа с таким названием уже существует")

    cat.name = name
    db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Группа+обновлена", status_code=303)

@app.post("/pharmacy/settings/category/{cat_id}/delete")
async def delete_category(cat_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    cat = db.query(MedicationCategory).filter(MedicationCategory.id == cat_id).first()
    if not cat:
        return RedirectResponse(url="/pharmacy/settings?error=Группа+не+найдена", status_code=303)
    
    if cat.medications:
        return RedirectResponse(url="/pharmacy/settings?error=Нельзя+удалить+группу,+к+ней+привязаны+препараты", status_code=303)
    
    db.delete(cat)
    db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Группа+удалена", status_code=303)

@app.get("/pharmacy/settings/location/{loc_id}/edit", response_class=HTMLResponse)
async def edit_location_form(loc_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    loc = db.query(StorageLocation).filter(StorageLocation.id == loc_id).first()
    if not loc:
        return RedirectResponse(url="/pharmacy/settings?error=Место+не+найдено", status_code=303)
    return render("pharmacy/location_edit.html", request, current_user=current_user, loc=loc, error=None)

@app.post("/pharmacy/settings/location/{loc_id}/edit")
async def edit_location_submit(loc_id: int, request: Request, name: str = Form(...), description: str = Form(""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    loc = db.query(StorageLocation).filter(StorageLocation.id == loc_id).first()
    if not loc:
        return RedirectResponse(url="/pharmacy/settings?error=Место+не+найдено", status_code=303)
    
    if db.query(StorageLocation).filter(StorageLocation.name == name, StorageLocation.id != loc_id).first():
        return render("pharmacy/location_edit.html", request, current_user=current_user, loc=loc, error="Место с таким названием уже существует")

    loc.name = name
    loc.description = description or None
    db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Место+обновлено", status_code=303)

@app.post("/pharmacy/settings/location/{loc_id}/delete")
async def delete_location(loc_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    loc = db.query(StorageLocation).filter(StorageLocation.id == loc_id).first()
    if not loc:
        return RedirectResponse(url="/pharmacy/settings?error=Место+не+найдено", status_code=303)
    
    if loc.medications:
        return RedirectResponse(url="/pharmacy/settings?error=Нельзя+удалить+место,+к+нему+привязаны+препараты", status_code=303)
    
    db.delete(loc)
    db.commit()
    return RedirectResponse(url="/pharmacy/settings?message=Место+удалено", status_code=303)

@app.get("/pharmacy/new", response_class=HTMLResponse)
async def pharmacy_new(request: Request, prefill_name: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    categories = db.query(MedicationCategory).order_by(MedicationCategory.name).all()
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    return render("pharmacy/form.html", request, current_user=current_user, medication=None, prefill_name=prefill_name, categories=categories, locations=locations, error=None)

@app.post("/pharmacy/new")
async def pharmacy_new_submit(
    request: Request, name: str = Form(...), form: str = Form(""), dosage: str = Form(""),
    description: str = Form(""), current_stock: int = Form(0),
    category_id: int = Form(None), location_id: int = Form(None),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    info = {"indications": "", "contraindications": "", "interactions": "", "side_effects": ""}
    
    med = Medication(
        name=name, form=form, dosage=dosage, description=description,
        category_id=category_id, location_id=location_id,
        indications=info["indications"], contraindications=info["contraindications"],
        interactions=info["interactions"], side_effects=info["side_effects"],
        current_stock=current_stock
    )
    db.add(med)
    db.flush()
    
    if current_stock > 0:
        db.add(StockTransaction(medication_id=med.id, user_id=current_user.id, amount=current_stock, reason="Первичное поступление"))
    
    audit(db, current_user, "medication.create", "medication", med.id, {"name": name})
    db.commit()
    return RedirectResponse(url="/pharmacy?message=Лекарство+добавлено", status_code=303)

@app.get("/pharmacy/{med_id}/edit", response_class=HTMLResponse)
async def pharmacy_edit_form(
    med_id: int, 
    request: Request, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        return RedirectResponse(url="/pharmacy?error=Препарат+не+найден", status_code=303)

    categories = db.query(MedicationCategory).order_by(MedicationCategory.name).all()
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()

    return render(
        "pharmacy/edit.html", request,
        current_user=current_user,
        med=med,
        categories=categories,
        locations=locations,
        error=request.query_params.get("error")
    )

@app.post("/pharmacy/{med_id}/edit", response_class=HTMLResponse)
async def pharmacy_edit_submit(
    med_id: int,
    request: Request,
    name: str = Form(...),
    form: str = Form(""),
    dosage: str = Form(""),
    description: str = Form(""),
    indications: str = Form(""),
    contraindications: str = Form(""),
    interactions: str = Form(""),
    side_effects: str = Form(""),
    category_id: int = Form(None),
    location_id: int = Form(None),
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        return RedirectResponse(url="/pharmacy?error=Препарат+не+найден", status_code=303)

    med.name = name
    med.form = form or None
    med.dosage = dosage or None
    med.description = description or None
    med.indications = indications or None
    med.contraindications = contraindications or None
    med.interactions = interactions or None
    med.side_effects = side_effects or None
    med.category_id = category_id
    med.location_id = location_id

    audit(db, current_user, "medication.edit", "medication", med.id, {"name": name})
    db.commit()

    return RedirectResponse(url=f"/pharmacy/{med_id}?message=Данные+препарата+обновлены", status_code=303)

@app.post("/pharmacy/{med_id}/stock")
async def pharmacy_stock_update(
    med_id: int, amount: int = Form(...), reason: str = Form(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med: return RedirectResponse(url="/pharmacy?error=Не+найдено", status_code=303)
    
    if amount < 0 and abs(amount) > med.current_stock:
        return RedirectResponse(url="/pharmacy?error=Недостаточно+на+складе", status_code=303)
        
    med.current_stock += amount
    db.add(StockTransaction(medication_id=med.id, user_id=current_user.id, amount=amount, reason=reason))
    db.commit()
    return RedirectResponse(url="/pharmacy?message=Склад+обновлен", status_code=303)

@app.post("/pharmacy/{med_id}/sync")
async def pharmacy_medication_sync(
    med_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        return RedirectResponse(url="/pharmacy?error=Препарат+не+найден", status_code=303)

    info = pharma_service.search_drug_info(med.name)
    
    med.indications = info["indications"]
    med.contraindications = info["contraindications"]
    med.interactions = info["interactions"]
    med.side_effects = info["side_effects"]
    med.last_synced_at = datetime.utcnow()
    
    audit(db, current_user, "medication.sync", "medication", med.id, {"name": med.name})
    db.commit()

    return RedirectResponse(url=f"/pharmacy/{med_id}?message=Информация+обновлена+из+интернета", status_code=303)

@app.post("/pharmacy/{med_id}/delete")
async def pharmacy_medication_delete(
    med_id: int, 
    confirm_name: str = Form(...), 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        return RedirectResponse(url="/pharmacy?error=Препарат+не+найден", status_code=303)

    if confirm_name.strip().lower() != med.name.lower():
        return RedirectResponse(
            url=f"/pharmacy/{med_id}/edit?error=Введённое+название+не+совпадает", 
            status_code=303
        )

    name = med.name
    
    db.query(StockTransaction).filter(StockTransaction.medication_id == med_id).delete()
    db.delete(med)
    audit(db, current_user, "medication.delete", "medication", med_id, {"name": name})
    db.commit()

    return RedirectResponse(url="/pharmacy?message=Препарат+удален+из+реестра", status_code=303)

@app.get("/pharmacy/{med_id}", response_class=HTMLResponse)
async def pharmacy_medication_detail(
    med_id: int, 
    request: Request, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_web)
):
    med = db.query(Medication).filter(Medication.id == med_id).first()
    if not med:
        return RedirectResponse(url="/pharmacy?error=Препарат+не+найден", status_code=303)

    transactions = (
        db.query(StockTransaction)
        .options(joinedload(StockTransaction.user), joinedload(StockTransaction.serviceman))
        .filter(StockTransaction.medication_id == med_id)
        .order_by(StockTransaction.created_at.desc())
        .limit(20)
        .all()
    )

    return render(
        "pharmacy/detail.html", request,
        current_user=current_user,
        med=med,
        transactions=transactions,
        message=request.query_params.get("message"),
        error=request.query_params.get("error")
    )


# --- Уведомления ---
@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    items = db.query(Notification).filter(Notification.user_id == current_user.id).order_by(Notification.created_at.desc()).all()
    return render("notifications.html", request, current_user=current_user, items=items)

@app.post("/notifications/{nid}/read")
async def notification_read(nid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    n = db.query(Notification).filter(Notification.id == nid, Notification.user_id == current_user.id).first()
    if n:
        n.is_read = True
        db.commit()
    return RedirectResponse(url="/notifications", status_code=303)

@app.post("/notifications/read-all")
async def notifications_read_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user_web)):
    db.query(Notification).filter(Notification.user_id == current_user.id, Notification.is_read.is_(False)).update({"is_read": True})
    db.commit()
    return RedirectResponse(url="/notifications", status_code=303)


# --- Админ: Аудит и Пользователи ---
@app.get("/admin/audit", response_class=HTMLResponse)
async def audit_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    logs = db.query(AuditLog).options(joinedload(AuditLog.actor)).order_by(AuditLog.created_at.desc()).limit(200).all()
    return render("audit_log.html", request, current_user=current_user, logs=logs)

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_list(request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    users = db.query(User).options(joinedload(User.role)).order_by(User.id).all()
    roles = db.query(Role).order_by(Role.name).all()
    return render("admin_users.html", request, current_user=current_user, users=users, roles=roles, message=request.query_params.get("message"), error=request.query_params.get("error"))

@app.post("/admin/users")
async def admin_user_create(username: str = Form(...), password: str = Form(...), role_id: int = Form(...), is_active: str | None = Form(None), db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    if not re.fullmatch(r"[a-zA-Z0-9._-]{3,32}", username):
        return RedirectResponse(url="/admin/users?error=Неверный+логин", status_code=303)
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse(url="/admin/users?error=Логин+занят", status_code=303)
    if len(password) < 12:
        return RedirectResponse(url="/admin/users?error=Пароль+короткий", status_code=303)
    
    db_user = User(username=username, password_hash=get_password_hash(password), role_id=role_id, is_active=bool(is_active))
    db.add(db_user)
    db.commit()
    return RedirectResponse(url="/admin/users?message=Пользователь+создан", status_code=303)

@app.post("/admin/users/{user_id}/deactivate")
async def admin_user_deactivate(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    user = db.query(User).options(joinedload(User.role)).filter(User.id == user_id).first()
    if not user: return RedirectResponse(url="/admin/users?error=Не+найден", status_code=303)
    if user.id == current_user.id: return RedirectResponse(url="/admin/users?error=Нельзя+деактивировать+себя", status_code=303)
    
    if user.role.name == "admin":
        active_admins = db.query(User).join(Role).filter(Role.name == "admin", User.is_active.is_(True), User.id != user_id).count()
        if active_admins == 0: return RedirectResponse(url="/admin/users?error=Последний+админ", status_code=303)

    user.is_active = False
    db.commit()
    return RedirectResponse(url="/admin/users?message=Пользователь+деактивирован", status_code=303)

@app.post("/admin/users/{user_id}/activate")
async def admin_user_activate(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = True
        db.commit()
    return RedirectResponse(url="/admin/users?message=Пользователь+активирован", status_code=303)

@app.post("/admin/users/{user_id}/delete")
async def admin_user_delete(user_id: int, confirm_username: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    user = db.query(User).options(joinedload(User.role)).filter(User.id == user_id).first()
    if not user: return RedirectResponse(url="/admin/users?error=Не+найден", status_code=303)
    if confirm_username.strip() != user.username: return RedirectResponse(url="/admin/users?error=Логин+не+совпадает", status_code=303)
    if user.id == current_user.id: return RedirectResponse(url="/admin/users?error=Нельзя+удалить+себя", status_code=303)
    
    if user.role.name == "admin":
        other_admins = db.query(User).join(Role).filter(Role.name == "admin", User.is_active.is_(True), User.id != user_id).count()
        if other_admins == 0: return RedirectResponse(url="/admin/users?error=Последний+админ", status_code=303)

    db.delete(user)
    db.commit()
    return RedirectResponse(url="/admin/users?message=Пользователь+удалён", status_code=303)


# --- API для быстрого добавления военнослужащего (из модального окна) ---
@app.post("/api/servicemen/quick-add")
async def api_serviceman_quick_add(
    full_name: str = Body(...),
    rank: str = Body(...),
    service_number: str = Body(""),
    unit: str = Body(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web)
):
    if service_number and db.query(Serviceman).filter(Serviceman.service_number == service_number).first():
        raise HTTPException(status_code=400, detail="Военнослужащий с таким номером жетона уже существует")

    sm = Serviceman(
        full_name=full_name, rank=rank, 
        service_number=service_number or None,
        unit=unit or "Не указано",
        military_unit="Не указано"
    )
    db.add(sm)
    db.flush()
    audit(db, current_user, "serviceman.create_quick", "serviceman", sm.id, {"name": full_name})
    db.commit()

    return {"id": sm.id, "full_name": sm.full_name, "rank": sm.rank, "service_number": sm.service_number or "", "unit": sm.unit}
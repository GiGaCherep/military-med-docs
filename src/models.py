import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import relationship

_file = Path(__file__).resolve()
for p in (str(_file.parent.parent), str(_file.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from database import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    role = relationship("Role", back_populates="users")
    documents = relationship("Document", back_populates="author")
    approvals = relationship("DocumentApproval", back_populates="approver")
    signatures = relationship("DocumentSignature", back_populates="signer")
    notifications = relationship("Notification", back_populates="user")


class Serviceman(Base):
    __tablename__ = "servicemen"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False, index=True)
    rank = Column(String(64), nullable=False)
    service_number = Column(String(64), unique=True, nullable=False, index=True)
    callsign = Column(String(64), nullable=True, index=True)
    unit = Column(String(128), nullable=False)
    military_unit = Column(String(128), nullable=False)
    blood_type = Column(String(16), nullable=True)
    allergies = Column(Text, nullable=True)
    
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    documents = relationship("Document", back_populates="serviceman")
    medical_records = relationship("MedicalRecord", back_populates="serviceman")
    outpatient_records = relationship("OutpatientRecord", back_populates="serviceman")
    stock_transactions = relationship("StockTransaction", back_populates="serviceman")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    doc_type = Column(String(50), nullable=False)
    doc_number = Column(String(64), nullable=True)
    doc_date = Column(Date, nullable=True)
    unit = Column(String(128), nullable=True)
    military_unit = Column(String(128), nullable=True)
    classification = Column(String(50), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    serviceman_id = Column(Integer, ForeignKey("servicemen.id"), nullable=True, index=True)
    file_name = Column(String(255), nullable=True)
    file_path = Column(String(512), nullable=True)
    file_size = Column(Integer, nullable=True)
    file_mime = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    author = relationship("User", back_populates="documents")
    serviceman = relationship("Serviceman", back_populates="documents")
    approvals = relationship("DocumentApproval", back_populates="document", cascade="all, delete-orphan", order_by="DocumentApproval.created_at")
    signatures = relationship("DocumentSignature", back_populates="document", cascade="all, delete-orphan", order_by="DocumentSignature.signed_at")


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, index=True)
    serviceman_id = Column(Integer, ForeignKey("servicemen.id"), nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    record_type = Column(String(32), nullable=False, default="admission")
    is_infectious = Column(Boolean, default=False, nullable=False)
    diagnosis = Column(String(255), nullable=False)
    icd_code = Column(String(16), nullable=True)
    complaints = Column(Text, nullable=True)
    treatment = Column(Text, nullable=True)
    outcome = Column(String(64), nullable=True)
    triage_color = Column(String(16), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    serviceman = relationship("Serviceman", back_populates="medical_records")
    doctor = relationship("User")


class OutpatientRecord(Base):
    __tablename__ = "outpatient_records"

    id = Column(Integer, primary_key=True, index=True)
    serviceman_id = Column(Integer, ForeignKey("servicemen.id"), nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    visit_date = Column(Date, nullable=False)
    diagnosis = Column(String(255), nullable=False)
    icd_code = Column(String(16), nullable=True)
    complaints = Column(Text, nullable=True)
    treatment = Column(Text, nullable=True)
    outcome = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    serviceman = relationship("Serviceman", back_populates="outpatient_records")
    doctor = relationship("User")


class DocumentApproval(Base):
    __tablename__ = "document_approvals"
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_status = Column(String(32), nullable=False)
    to_status = Column(String(32), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    document = relationship("Document", back_populates="approvals")
    approver = relationship("User", back_populates="approvals")


class DocumentSignature(Base):
    __tablename__ = "document_signatures"
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    signer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    signer_full_name = Column(String(255), nullable=False)
    signer_role = Column(String(50), nullable=False)
    content_hash = Column(String(128), nullable=False)
    signed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    document = relationship("Document", back_populates="signatures")
    signer = relationship("User", back_populates="signatures")


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    user = relationship("User", back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(32), nullable=True)
    target_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor = relationship("User")


# --- АПТЕКА ---

class MedicationCategory(Base):
    __tablename__ = "medication_categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    
    medications = relationship("Medication", back_populates="category")


class StorageLocation(Base):
    __tablename__ = "storage_locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    
    medications = relationship("Medication", back_populates="location")


class Medication(Base):
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    form = Column(String(64), nullable=True)
    dosage = Column(String(64), nullable=True)
    description = Column(Text, nullable=True)
    
    category_id = Column(Integer, ForeignKey("medication_categories.id"), nullable=True, index=True)
    location_id = Column(Integer, ForeignKey("storage_locations.id"), nullable=True, index=True)

    indications = Column(Text, nullable=True)
    contraindications = Column(Text, nullable=True)
    interactions = Column(Text, nullable=True)
    side_effects = Column(Text, nullable=True)
    
    current_stock = Column(Integer, default=0, nullable=False)
    last_synced_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    category = relationship("MedicationCategory", back_populates="medications")
    location = relationship("StorageLocation", back_populates="medications")
    transactions = relationship("StockTransaction", back_populates="medication")


class StockTransaction(Base):
    __tablename__ = "stock_transactions"

    id = Column(Integer, primary_key=True, index=True)
    medication_id = Column(Integer, ForeignKey("medications.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    serviceman_id = Column(Integer, ForeignKey("servicemen.id"), nullable=True, index=True)

    amount = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    medication = relationship("Medication", back_populates="transactions")
    user = relationship("User")
    serviceman = relationship("Serviceman", back_populates="stock_transactions")
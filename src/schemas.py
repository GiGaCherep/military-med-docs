from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RoleBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=32)


class RoleCreate(RoleBase):
    pass


class RoleResponse(RoleBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)


class UserCreate(UserBase):
    password: str = Field(..., min_length=12, max_length=128)
    role_id: int
    is_active: bool = True


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=32)
    password: Optional[str] = Field(None, min_length=12, max_length=128)
    role_id: Optional[int] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    role: RoleResponse
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class UserMeResponse(UserResponse):
    pass


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    exp: int
    role: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=128)


# --- Документы ---

DOC_TYPES = ["Приказ", "Инструкция", "Протокол", "Справка", "Рапорт", "Журнал", "Акт"]
CLASSIFICATIONS = ["ДСП", "Секретно", "Совершенно секретно"]
STATUSES = ["draft", "review", "approved", "rejected"]


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    doc_type: str = Field(..., min_length=3, max_length=50)
    doc_number: Optional[str] = Field(None, max_length=64)
    doc_date: Optional[date] = None
    unit: Optional[str] = Field(None, max_length=128)
    military_unit: Optional[str] = Field(None, max_length=128)
    classification: str = Field(..., max_length=50)
    description: Optional[str] = None


class DocumentResponse(BaseModel):
    id: int
    title: str
    doc_type: str
    doc_number: Optional[str]
    doc_date: Optional[date]
    unit: Optional[str]
    military_unit: Optional[str]
    classification: str
    author_id: int
    file_name: Optional[str]
    file_size: Optional[int]
    file_mime: Optional[str]
    description: Optional[str]
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
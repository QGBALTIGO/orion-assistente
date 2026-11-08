"""Private encrypted dental examination uploads. Never uses Orion's public media CDN."""
import base64
import binascii
import hashlib
import re
import secrets
from urllib.parse import quote
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app.dental_clinic_schemas import ExamUpload
from app.dental_clinic_api import (
    router, clinical_session, patient_row, audit, key_material, crypt_context
)

MAX_BYTES=8*1024*1024


def file_content(payload:ExamUpload):
    try:
        raw=base64.b64decode(payload.data_base64,validate=True)
    except (ValueError,binascii.Error):
        raise HTTPException(422,"Arquivo inválido") from None
    if not 0<len(raw)<=MAX_BYTES:
        raise HTTPException(413,"Envie arquivos de até 8 MB")
    magic={"application/pdf":raw.startswith(b"%PDF-"),
           "image/png":raw.startswith(b"\x89PNG\r\n\x1a\n"),
           "image/jpeg":raw.startswith(b"\xff\xd8\xff")}
    if not magic.get(payload.media_type):
        raise HTTPException(422,"Formato real do arquivo não corresponde ao selecionado")
    name=payload.filename.strip()
    if (name in {".",".."} or not re.fullmatch(r"[\w .()\[\]-]{1,180}",name,re.UNICODE)
            or "/" in name or "\\" in name):
        raise HTTPException(422,"Nome de arquivo inválido")
    ext={"application/pdf":".pdf","image/png":".png","image/jpeg":(".jpg",".jpeg")}
    if not (name.lower().endswith(ext[payload.media_type])):
        raise HTTPException(422,"Extensão não corresponde ao conteúdo do exame")
    return raw,name


@router.post("/patients/{patient_id}/exams",status_code=201)
async def upload_exam(tenant_id:UUID,patient_id:UUID,payload:ExamUpload,actor:Actor=Depends(authenticate)):
    key=key_material()
    raw,name=file_content(payload)
    async with clinical_session(tenant_id,actor,write=True,dentist=True) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        identity=uuid4()
        nonce=secrets.token_bytes(12)
        ciphertext=AESGCM(key).encrypt(nonce,raw,crypt_context(tenant_id,patient_id,"exam",identity))
        digest=hashlib.sha256(raw).hexdigest()
        await db.execute(text("""
            INSERT INTO dental_exam_files
             (id,tenant_id,patient_id,filename,media_type,byte_count,digest,nonce,
              encrypted_content,authored_by)
            VALUES (:id,:tenant,:patient,:filename,:mime,:count,:digest,:nonce,:cipher,:author)
        """),{"id":identity,"tenant":tenant_id,"patient":patient_id,"filename":name,
               "mime":payload.media_type,"count":len(raw),"digest":digest,
               "nonce":nonce,"cipher":ciphertext,"author":actor.account_id})
        await audit(db,tenant_id,actor,"exam_uploaded",patient_id)
        return {"id":identity,"filename":name,"media_type":payload.media_type,
                "byte_count":len(raw),"saved":True}


@router.get("/patients/{patient_id}/exams")
async def list_exams(tenant_id:UUID,patient_id:UUID,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        records=(await db.execute(text("""
            SELECT id,filename,media_type,byte_count,created_at,authored_by
            FROM dental_exam_files WHERE tenant_id=:tenant AND patient_id=:patient
            ORDER BY created_at DESC,id DESC LIMIT 100
        """),{"tenant":tenant_id,"patient":patient_id})).mappings().all()
        await audit(db,tenant_id,actor,"exam_directory_read",patient_id)
        return {"items":[dict(record) for record in records]}


@router.get("/patients/{patient_id}/exams/{exam_id}")
async def download_exam(tenant_id:UUID,patient_id:UUID,exam_id:UUID,
                        actor:Actor=Depends(authenticate)):
    key=key_material()
    async with clinical_session(tenant_id,actor) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        file=(await db.execute(text("""
            SELECT id,filename,media_type,byte_count,nonce,digest,encrypted_content
            FROM dental_exam_files WHERE tenant_id=:tenant AND patient_id=:patient AND id=:exam
        """),{"tenant":tenant_id,"patient":patient_id,"exam":exam_id})).mappings().one_or_none()
        if not file:
            raise HTTPException(404,"Exame não encontrado nesta clínica")
        try:
            raw=AESGCM(key).decrypt(bytes(file["nonce"]),bytes(file["encrypted_content"]),
                       crypt_context(tenant_id,patient_id,"exam",exam_id))
            if len(raw)!=file["byte_count"] or hashlib.sha256(raw).hexdigest()!=file["digest"]:
                raise InvalidTag("checksum mismatch")
        except (InvalidTag,ValueError):
            raise HTTPException(503,"Arquivo não pôde ser validado") from None
        await audit(db,tenant_id,actor,"exam_downloaded",patient_id)
        return Response(content=raw,media_type=file["media_type"],headers={
            "Cache-Control":"no-store, max-age=0","Pragma":"no-cache",
            "X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
            "Content-Security-Policy":"default-src 'none'; sandbox",
            "Content-Disposition":"attachment; filename*=UTF-8''"+quote(file["filename"]),
        })

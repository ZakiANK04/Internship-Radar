"""Local document library management: CV versions, transcripts, certificates, and attachments.

Documents are stored strictly within the local data directory (outside tracked git files).
Filenames and paths are sanitized against path traversal and validated for size and type.
"""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config, db

MAX_DOCUMENT_SIZE = 15 * 1024 * 1024  # 15 MB per file
# Gmail refuses messages over 25 MB once encoded; base64 adds ~37 %, so raw attachments must stay under ~18 MB.
MAX_TOTAL_ATTACHMENTS_SIZE = 18 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
ALLOWED_DOC_TYPES = {"cv", "transcript", "recommendation", "reference", "cover_letter", "certificate", "language_test", "portfolio", "other"}
ALLOWED_LANGUAGES = {"EN", "FR", "ALL", "OTHER"}


class DocumentError(ValueError):
    pass


def ensure_documents_dir() -> Path:
    config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    return config.DOCUMENTS_DIR


def safe_file_slug(filename: str) -> str:
    """Generate a sanitized, safe filename slug with no directory separators or special characters."""
    name = Path(filename).name
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    plain = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    clean_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", plain).strip("._-")[:60] or "document"
    return f"{clean_stem}{ext}"


def validate_file(file_bytes: bytes, original_filename: str) -> tuple[str, int, str]:
    """Validate document file type and size, returning (safe_filename, size, mime_type)."""
    if not file_bytes:
        raise DocumentError("Document file is empty.")
    if len(file_bytes) > MAX_DOCUMENT_SIZE:
        raise DocumentError(
            f"File size ({len(file_bytes) / (1024 * 1024):.1f} MB) exceeds maximum allowed ({MAX_DOCUMENT_SIZE // (1024 * 1024)} MB)."
        )

    # Sanitize and check extension
    name = Path(original_filename).name
    if ".." in original_filename or "/" in original_filename or "\\" in original_filename:
        # Prevent any path traversal characters in input
        name = Path(original_filename.replace("\\", "/")).name
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentError(
            f"File extension '{ext}' is not supported. Allowed formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )

    safe_name = safe_file_slug(name)
    mime = mimetypes.guess_type(safe_name)[0] or "application/pdf"
    return safe_name, len(file_bytes), mime


def get_document_path(doc: dict[str, Any] | int) -> Path:
    """Resolve and verify absolute path for a stored document, preventing directory traversal."""
    if isinstance(doc, int):
        record = db.one("SELECT * FROM documents WHERE id = ?", (doc,))
        if not record:
            raise DocumentError(f"Document #{doc} not found.")
        doc = record

    stored_name = doc.get("filename")
    if not stored_name:
        raise DocumentError("Document record has no filename.")

    # Guard against path traversal in stored filename
    clean_name = Path(stored_name).name
    if clean_name != stored_name or ".." in stored_name:
        raise DocumentError("Invalid stored document filename.")

    doc_dir = ensure_documents_dir().resolve()
    target = (doc_dir / clean_name).resolve()
    if not str(target).startswith(str(doc_dir)):
        raise DocumentError("Document path escapes document storage directory.")
    return target


def add_document(
    file_bytes: bytes,
    original_filename: str,
    *,
    title: str,
    doc_type: str = "cv",
    language: str = "ALL",
    version: Optional[str] = None,
    notes: Optional[str] = None,
    is_default: bool = False,
) -> dict[str, Any]:
    """Validate, safely store a document file and register it in SQLite."""
    doc_type = doc_type.lower()
    if doc_type not in ALLOWED_DOC_TYPES:
        raise DocumentError(f"Invalid doc_type '{doc_type}'. Allowed: {', '.join(sorted(ALLOWED_DOC_TYPES))}")

    language = (language or "ALL").upper()
    if language not in ALLOWED_LANGUAGES:
        language = "OTHER"

    title = title.strip() or Path(original_filename).stem

    safe_name, file_size, mime_type = validate_file(file_bytes, original_filename)

    # Generate a unique stored filename: doc_<uuid4>_<slug>
    unique_prefix = uuid.uuid4().hex[:12]
    stored_filename = f"doc_{unique_prefix}_{safe_name}"

    doc_dir = ensure_documents_dir()
    target_path = doc_dir / stored_filename
    if file_size > COMPRESS_ABOVE:  # heavy scans are made light before they are ever attached
        smaller = _smaller(file_bytes, target_path.suffix)
        if smaller is not None:
            _backup(stored_filename, file_bytes)
            file_bytes, file_size = smaller, len(smaller)
    target_path.write_bytes(file_bytes)

    stamp = db.now()
    with db.write() as conn:
        if is_default and doc_type == "cv":
            # One default CV per language
            conn.execute(
                "UPDATE documents SET is_default = 0 WHERE doc_type = ? AND language = ?",
                (doc_type, language),
            )
        cursor = conn.execute(
            """INSERT INTO documents (title, doc_type, language, filename, original_filename,
                                     file_size, mime_type, version, notes, is_default, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                doc_type,
                language,
                stored_filename,
                Path(original_filename).name,
                file_size,
                mime_type,
                version or "",
                notes or "",
                int(is_default),
                stamp,
                stamp,
            ),
        )
        doc_id = cursor.lastrowid

    return get_document(doc_id)


# ------------------------------------------------------------ compression
COMPRESS_ABOVE = 1_500_000   # files larger than this are worth compressing (sent with every e-mail)


def _smaller(data: bytes, extension: str) -> Optional[bytes]:
    from .compress import compress

    try:
        return compress(data, extension)
    except Exception:  # an unusual file is kept exactly as it is
        return None


def originals_dir() -> Path:
    folder = ensure_documents_dir() / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _backup(stored_filename: str, data: bytes) -> None:
    target = originals_dir() / Path(stored_filename).name
    if not target.exists():
        target.write_bytes(data)


def compress_document(doc_id: int) -> dict[str, Any]:
    """Replace a stored file by a lighter copy; the original is kept in data/documents/originals."""
    doc = get_document(doc_id)
    if not doc:
        raise DocumentError(f"Document #{doc_id} not found.")
    path = get_document_path(doc)
    data = path.read_bytes()
    smaller = _smaller(data, path.suffix)
    result = {"id": doc_id, "title": doc["title"], "before": len(data), "after": len(data), "compressed": False}
    if smaller is None:
        return result
    _backup(doc["filename"], data)
    path.write_bytes(smaller)
    db.execute("UPDATE documents SET file_size = ?, updated_at = ? WHERE id = ?", (len(smaller), db.now(), doc_id))
    return {**result, "after": len(smaller), "compressed": True}


def has_original(doc: dict[str, Any]) -> bool:
    return (originals_dir() / Path(doc["filename"]).name).exists()


def restore_original(doc_id: int) -> dict[str, Any]:
    doc = get_document(doc_id)
    if not doc or not has_original(doc):
        raise DocumentError("No original copy is kept for this document.")
    original = (originals_dir() / Path(doc["filename"]).name).read_bytes()
    get_document_path(doc).write_bytes(original)
    db.execute("UPDATE documents SET file_size = ?, updated_at = ? WHERE id = ?", (len(original), db.now(), doc_id))
    return get_document(doc_id)


def compress_large(min_size: int = COMPRESS_ABOVE) -> list[dict[str, Any]]:
    rows = db.rows("SELECT id FROM documents WHERE file_size > ? ORDER BY is_default DESC, file_size DESC", (min_size,))
    return [compress_document(row["id"]) for row in rows]


def update_document(doc_id: int, **fields: Any) -> dict[str, Any]:
    """Update metadata for an existing document."""
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise DocumentError(f"Document #{doc_id} not found.")

    allowed_keys = {"title", "doc_type", "language", "version", "notes", "is_default"}
    updates = {}
    for key, val in fields.items():
        if key in allowed_keys:
            if key == "doc_type":
                v = str(val).lower()
                if v not in ALLOWED_DOC_TYPES:
                    raise DocumentError(f"Invalid doc_type '{v}'.")
                updates[key] = v
            elif key == "language":
                v = str(val or "ALL").upper()
                updates[key] = v if v in ALLOWED_LANGUAGES else "OTHER"
            elif key == "is_default":
                updates[key] = int(bool(val))
            else:
                updates[key] = str(val or "").strip()

    if not updates:
        return doc

    updates["updated_at"] = db.now()
    with db.write() as conn:
        if updates.get("is_default") and updates.get("doc_type", doc["doc_type"]) == "cv":
            target_type = updates.get("doc_type", doc["doc_type"])
            target_lang = updates.get("language", doc["language"])
            conn.execute(
                "UPDATE documents SET is_default = 0 WHERE doc_type = ? AND language = ? AND id != ?",
                (target_type, target_lang, doc_id),
            )
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE documents SET {set_clause} WHERE id = ?", (*updates.values(), doc_id))

    return get_document(doc_id)


def delete_document(doc_id: int) -> bool:
    """Delete document from SQLite and filesystem."""
    doc = db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        return False

    # Remove file if it exists
    try:
        path = get_document_path(doc)
        if path.exists():
            path.unlink()
    except Exception:
        pass

    with db.write() as conn:
        conn.execute("DELETE FROM application_documents WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return True


def get_document(doc_id: int) -> Optional[dict[str, Any]]:
    return db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))


def list_documents(doc_type: Optional[str] = None, language: Optional[str] = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM documents"
    conditions = []
    params = []
    if doc_type:
        conditions.append("doc_type = ?")
        params.append(doc_type.lower())
    if language:
        conditions.append("(language = ? OR language = 'ALL')")
        params.append(language.upper())

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY doc_type, is_default DESC, title"
    rows = db.rows(query, params)
    for row in rows:
        row["has_original"] = has_original(row)
    return rows


def get_defaults_for_language(language: str = "EN") -> list[int]:
    """Documents attached to a new application in this language: the default CV for that
    language, then every other document marked "send by default" in that language or in all languages.
    """
    lang = (language or "EN").upper()
    # Find matching default CV
    cv_doc = db.one(
        """SELECT id FROM documents
           WHERE doc_type = 'cv' AND is_default = 1 AND (language = ? OR language = 'ALL')
           ORDER BY (language = ?) DESC LIMIT 1""",
        (lang, lang),
    )
    if not cv_doc:
        # Fallback to any CV matching language or any default CV
        cv_doc = db.one(
            """SELECT id FROM documents
               WHERE doc_type = 'cv'
               ORDER BY (language = ?) DESC, is_default DESC, id LIMIT 1""",
            (lang,),
        )
    extras = db.rows(
        """SELECT id FROM documents WHERE doc_type != 'cv' AND is_default = 1 AND (language = ? OR language = 'ALL')
           ORDER BY CASE doc_type WHEN 'cover_letter' THEN 0 WHEN 'transcript' THEN 1 WHEN 'recommendation' THEN 2
                    WHEN 'reference' THEN 3 WHEN 'language_test' THEN 4 ELSE 5 END, title""",
        (lang,),
    )
    return ([cv_doc["id"]] if cv_doc else []) + [row["id"] for row in extras]


def get_application_documents(application_id: int) -> list[dict[str, Any]]:
    """Retrieve full metadata of all documents attached to an application, ordered."""
    app = db.one("SELECT selected_document_ids, language FROM applications WHERE id = ?", (application_id,))
    if not app:
        return []

    import json

    ids: list[int] = []
    # Check application_documents table first
    rows = db.rows(
        """SELECT d.* FROM documents d
           JOIN application_documents ad ON ad.document_id = d.id
           WHERE ad.application_id = ?
           ORDER BY ad.position, d.id""",
        (application_id,),
    )
    if rows:
        return rows

    # Fallback to selected_document_ids JSON
    raw_ids = app.get("selected_document_ids")
    if raw_ids:
        try:
            ids = json.loads(raw_ids)
        except Exception:
            ids = []

    if ids:
        marks = ",".join("?" for _ in ids)
        doc_rows = db.rows(f"SELECT * FROM documents WHERE id IN ({marks})", ids)
        by_id = {r["id"]: r for r in doc_rows}
        return [by_id[i] for i in ids if i in by_id]

    # Legacy fallback: return default document for application language
    default_ids = get_defaults_for_language(app.get("language") or "EN")
    return [r for r in (get_document(i) for i in default_ids) if r]


def set_application_documents(application_id: int, document_ids: list[int]) -> list[dict[str, Any]]:
    """Set the exact documents attached to an application, verifying their existence."""
    import json

    # Validate that document_ids exist
    cleaned_ids: list[int] = []
    for d_id in document_ids:
        try:
            int_id = int(d_id)
            if get_document(int_id):
                if int_id not in cleaned_ids:
                    cleaned_ids.append(int_id)
        except (ValueError, TypeError):
            continue

    with db.write() as conn:
        conn.execute("DELETE FROM application_documents WHERE application_id = ?", (application_id,))
        for idx, doc_id in enumerate(cleaned_ids):
            conn.execute(
                "INSERT INTO application_documents (application_id, document_id, position) VALUES (?, ?, ?)",
                (application_id, doc_id, idx),
            )
        conn.execute(
            "UPDATE applications SET selected_document_ids = ?, updated_at = ? WHERE id = ?",
            (json.dumps(cleaned_ids), db.now(), application_id),
        )

    return get_application_documents(application_id)


def _certificate_type(name: str) -> str:
    lowered = name.lower()
    if "recommand" in lowered or "recommend" in lowered:
        return "recommendation"
    if any(word in lowered for word in ("tcf", "ef set", "efset", "toefl", "ielts", "delf", "dalf")):
        return "language_test"
    return "certificate"


def sync_existing_documents() -> int:
    """Safely discover existing CVs and data documents and register them if not already added.
    Preserves all original files in place; copies local copies to DOCUMENTS_DIR.
    """
    if db.one("SELECT count(*) as n FROM documents")["n"] > 0:
        return 0

    ensure_documents_dir()
    added = 0

    # 1. Check profile CV files
    profile = db.profile()
    cv_files = profile.get("cv_files") or {}
    for lang, fname in cv_files.items():
        if fname:
            path = config.ROOT_DIR / fname
            if path.exists() and path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
                title = f"CV ({lang})"
                if "Job" in fname:
                    title = f"Job CV ({lang})"
                elif "Master" in fname:
                    title = f"Master / Research CV ({lang})"
                try:
                    add_document(
                        path.read_bytes(),
                        path.name,
                        title=title,
                        doc_type="cv",
                        language=lang,
                        version="1.0",
                        notes=f"Auto-imported from {path.name}",
                        is_default=True,
                    )
                    added += 1
                except Exception:
                    pass

    # 2. Check ROOT_DIR for other PDF CV files
    for p in config.ROOT_DIR.glob("*CV*.pdf"):
        if p.name not in cv_files.values():
            lang = "FR" if "_FR" in p.name.upper() else "EN" if "_EN" in p.name.upper() else "ALL"
            title = p.stem.replace("_", " ")
            try:
                add_document(
                    p.read_bytes(),
                    p.name,
                    title=title,
                    doc_type="cv",
                    language=lang,
                    version="1.0",
                    is_default=False,
                )
                added += 1
            except Exception:
                pass

    # 3. Check data/Academic Transcripts
    transcripts_dir = config.DATA_DIR / "Academic Transcripts"
    if transcripts_dir.exists() and transcripts_dir.is_dir():
        for p in transcripts_dir.glob("*.pdf"):
            try:
                add_document(
                    p.read_bytes(),
                    p.name,
                    title=p.stem.replace("_", " "),
                    doc_type="transcript",
                    language="ALL",
                    is_default=False,
                )
                added += 1
            except Exception:
                pass

    # 4. Check data/Certificates
    certs_dir = config.DATA_DIR / "Certificates"
    if certs_dir.exists() and certs_dir.is_dir():
        for p in certs_dir.glob("*.pdf"):
            try:
                add_document(
                    p.read_bytes(),
                    p.name,
                    title=p.stem.replace("_", " "),
                    doc_type=_certificate_type(p.name),
                    language="ALL",
                    is_default=False,
                )
                added += 1
            except Exception:
                pass

    return added

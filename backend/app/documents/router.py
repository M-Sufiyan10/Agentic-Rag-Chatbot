"""Document endpoints: upload, list, get and delete (own documents only)."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
from app.config import Settings
from app.db import get_session
from app.documents import service
from app.documents.extraction import SUPPORTED_EXTENSIONS
from app.documents.ingestion import ingest_document
from app.documents.schemas import DocumentOut
from app.documents.storage import FileStorage

router = APIRouter(prefix="/documents", tags=["documents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    settings: Settings = request.app.state.settings
    storage: FileStorage = request.app.state.storage
    filename = service.clean_filename(file.filename)
    ext = service.extension_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported file type. Upload a PDF, DOCX, TXT or MD file.",
        )
    data = await service.read_limited(
        file, settings.max_upload_mb * 1024 * 1024, settings.max_upload_mb
    )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    service.check_contents_match_extension(ext, data)

    document = await service.create_document(session, storage, user.id, filename, data)
    background_tasks.add_task(
        ingest_document,
        document.id,
        sessionmaker=request.app.state.sessionmaker,
        storage=storage,
        embeddings=request.app.state.embeddings,
        settings=settings,
    )
    return DocumentOut.model_validate(document)


@router.get("", response_model=list[DocumentOut])
async def list_documents(session: SessionDep, user: CurrentUser) -> list[DocumentOut]:
    documents = await service.list_documents(session, user.id)
    return [DocumentOut.model_validate(d) for d in documents]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: UUID, session: SessionDep, user: CurrentUser) -> DocumentOut:
    document = await service.get_owned_document(session, user.id, document_id)
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID, request: Request, session: SessionDep, user: CurrentUser
) -> Response:
    document = await service.get_owned_document(session, user.id, document_id)
    await service.delete_document(session, request.app.state.storage, document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

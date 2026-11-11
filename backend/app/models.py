"""Import every model so Alembic autogenerate and metadata see them."""

from app.auth.models import User
from app.conversations.models import Conversation
from app.documents.models import Document, DocumentChunk

__all__ = ["Conversation", "Document", "DocumentChunk", "User"]

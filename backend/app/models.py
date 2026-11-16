"""Import every model so Alembic autogenerate and metadata see them."""

from app.auth.models import User
from app.conversations.models import Conversation
from app.documents.models import Document, DocumentChunk
from app.feedback.models import MessageFeedback

__all__ = ["Conversation", "Document", "DocumentChunk", "MessageFeedback", "User"]

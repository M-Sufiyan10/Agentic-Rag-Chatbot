"""Import every model so Alembic autogenerate and metadata see them."""

from app.auth.models import User
from app.conversations.models import Conversation

__all__ = ["Conversation", "User"]

"""Per-user vector search over document chunks (real pgvector)."""

import uuid

from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.documents.models import Document, DocumentChunk
from app.documents.retrieval import (
    DocumentOverview,
    count_mismatched_chunks,
    document_overview,
    search_chunks,
)

MODEL = "nomic-embed-text"


async def _user(db: AsyncSession) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.commit()
    return user


async def _document_with_chunk(
    db: AsyncSession,
    embeddings: Embeddings,
    user: User,
    text: str,
    *,
    status: str = "ready",
    embedding_model: str = MODEL,
    filename: str = "notes.txt",
) -> Document:
    doc = Document(
        user_id=user.id,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(text),
        storage_key=f"{user.id}/{uuid.uuid4()}.txt",
        status=status,
    )
    db.add(doc)
    await db.flush()
    db.add(
        DocumentChunk(
            document_id=doc.id,
            user_id=user.id,
            chunk_index=0,
            content=text,
            embedding=embeddings.embed_query(text),
            embedding_model=embedding_model,
        )
    )
    await db.commit()
    return doc


async def test_search_is_scoped_to_the_user(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    alice, bob = await _user(db_session), await _user(db_session)
    alice_doc = await _document_with_chunk(db_session, embeddings, alice, "zebra migration")
    await _document_with_chunk(db_session, embeddings, bob, "quantum tunnelling")
    query = embeddings.embed_query("zebra migration")

    as_bob = await search_chunks(
        db_session, user_id=bob.id, query_vector=query, embedding_model=MODEL, k=10
    )
    as_alice = await search_chunks(
        db_session, user_id=alice.id, query_vector=query, embedding_model=MODEL, k=10
    )

    assert [c.content for c in as_bob] == ["quantum tunnelling"]
    assert as_alice[0].content == "zebra migration"
    assert as_alice[0].document_id == alice_doc.id
    assert as_alice[0].filename == "notes.txt"
    assert as_alice[0].chunk_index == 0
    assert as_alice[0].distance < 1e-6


async def test_search_orders_by_distance_and_limits_k(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    alice = await _user(db_session)
    for word in ["alpha", "beta", "gamma"]:
        await _document_with_chunk(db_session, embeddings, alice, word)

    results = await search_chunks(
        db_session,
        user_id=alice.id,
        query_vector=embeddings.embed_query("beta"),
        embedding_model=MODEL,
        k=2,
    )

    assert len(results) == 2
    assert results[0].content == "beta"
    assert results[0].distance <= results[1].distance


async def test_other_embedding_model_is_excluded_and_counted(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    alice, bob = await _user(db_session), await _user(db_session)
    await _document_with_chunk(db_session, embeddings, alice, "zebra migration")
    await _document_with_chunk(
        db_session, embeddings, alice, "old vectors", embedding_model="other"
    )
    await _document_with_chunk(db_session, embeddings, bob, "bob old", embedding_model="other")

    results = await search_chunks(
        db_session,
        user_id=alice.id,
        query_vector=embeddings.embed_query("old vectors"),
        embedding_model=MODEL,
        k=10,
    )

    assert [c.content for c in results] == ["zebra migration"]
    assert await count_mismatched_chunks(db_session, user_id=alice.id, embedding_model=MODEL) == 1
    assert await count_mismatched_chunks(db_session, user_id=bob.id, embedding_model=MODEL) == 1


async def test_processing_document_is_excluded(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    alice = await _user(db_session)
    await _document_with_chunk(db_session, embeddings, alice, "half done", status="processing")

    results = await search_chunks(
        db_session,
        user_id=alice.id,
        query_vector=embeddings.embed_query("half done"),
        embedding_model=MODEL,
        k=10,
    )

    assert results == []


async def test_document_overview_is_scoped_newest_first_and_limited(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    alice, bob = await _user(db_session), await _user(db_session)
    for name in ["a.txt", "b.txt", "c.txt"]:
        await _document_with_chunk(db_session, embeddings, alice, name, filename=name)
    await _document_with_chunk(db_session, embeddings, alice, "p", status="processing")
    await _document_with_chunk(db_session, embeddings, alice, "f", status="failed")
    await _document_with_chunk(db_session, embeddings, bob, "secret", filename="bob.txt")

    overview = await document_overview(db_session, user_id=alice.id, limit=2)

    assert overview == DocumentOverview(ready=["c.txt", "b.txt"], ready_total=3, processing=1)


async def test_document_overview_without_documents(db_session: AsyncSession) -> None:
    alice = await _user(db_session)

    overview = await document_overview(db_session, user_id=alice.id, limit=20)

    assert overview == DocumentOverview(ready=[], ready_total=0, processing=0)


async def test_search_finds_k_own_chunks_among_many_others_through_the_index(
    db_session: AsyncSession, embeddings: Embeddings
) -> None:
    # pgvector's HNSW scan returns hnsw.ef_search (40) nearest candidates from the
    # whole table and only then applies WHERE user_id = ...; without an iterative
    # scan a user with few chunks among many gets fewer than k results, or none.
    alice, bob = await _user(db_session), await _user(db_session)
    for word in ["alpha", "beta", "gamma", "delta"]:
        await _document_with_chunk(db_session, embeddings, alice, word)
    bob_doc = Document(
        user_id=bob.id,
        filename="big.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key=f"{bob.id}/{uuid.uuid4()}.txt",
        status="ready",
    )
    db_session.add(bob_doc)
    await db_session.flush()
    db_session.add_all(
        DocumentChunk(
            document_id=bob_doc.id,
            user_id=bob.id,
            chunk_index=i,
            content=f"bob {i}",
            embedding=embeddings.embed_query(f"bob {i}"),
            embedding_model=MODEL,
        )
        for i in range(1500)
    )
    await db_session.commit()
    # Make the planner use the HNSW index, as it does once the table is large (on a
    # table this small it would otherwise pick the user_id btree and sort).
    for setting in ["enable_seqscan", "enable_bitmapscan", "enable_sort"]:
        await db_session.execute(text(f"SET LOCAL {setting} = off"))

    results = await search_chunks(
        db_session,
        user_id=alice.id,
        query_vector=embeddings.embed_query("an unrelated question"),
        embedding_model=MODEL,
        k=4,
    )

    assert sorted(c.content for c in results) == ["alpha", "beta", "delta", "gamma"]
    distances = [c.distance for c in results]
    assert distances == sorted(distances)

"""
rag.py

This module's job: given a player's name, retrieve a handful of the most
relevant snippets from (a) recent news articles and (b) analyst/sportswriter
commentary about them — RAG (embed a small document set,
similarity-search it against a query, return the top matches)

"""

import asyncio
import logging
import re
from os import getenv

import numpy as np
import requests
import voyageai
from dotenv import load_dotenv
from tavily import TavilyClient
from voyageai import AsyncClient as AsyncVoyageAIClient

load_dotenv()
TAVILY_CLIENT_KEY = getenv("TAVILY_API_KEY")
VOYAGE_CLIENT_KEY = getenv("VOYAGE_API_KEY")

# voyage_client already has max_retries=3 built in (Voyage's own SDK retries
# transient failures like rate limits internally) — the retry loop around
# embed_texts below is a deliberate second layer on top of that, not a
# duplicate: it also covers failure modes the SDK's own retry may not (e.g.
# a raw network-level connection error), and keeps embed_texts and
# search_articles consistent with each other, since Tavily has no built-in
# retry of its own at all.
voyage_client = AsyncVoyageAIClient(api_key=VOYAGE_CLIENT_KEY, max_retries=3, timeout=30.0)
tavily_client = TavilyClient(api_key=TAVILY_CLIENT_KEY)

MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0


def chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    """Split one article's text into smaller pieces of roughly `chunk_size` characters each."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > chunk_size:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    return chunks


async def search_articles(player_name: str, category: str, max_results: int = 5) -> list[dict]:
    """Call Tavily and return each result as {"url", "title", "content"}."""
    query = (
        f"{player_name} NBA news"
        if category == "news"
        else f"{player_name} NBA analyst opinion outlook"
    )
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            response = await asyncio.to_thread(tavily_client.search, query, max_results=max_results)
            break
        except requests.exceptions.RequestException:
            if attempt == MAX_RETRY_ATTEMPTS:
                raise
            logging.warning(
                f"Tavily search failed (attempt {attempt}/{MAX_RETRY_ATTEMPTS}), retrying..."
            )
            await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    return [
        {"url": result["url"], "title": result.get("title", ""), "content": result["content"]}
        for result in response["results"]
        if "content" in result
    ]


def chunk_article(article: dict, chunk_size: int = 500) -> list[dict]:
    """Chunk one article's content, tagging each piece with the article's url/title."""
    return [
        {"text": piece, "url": article["url"], "title": article["title"]}
        for piece in chunk_text(article["content"], chunk_size)
    ]


async def embed_texts(texts: list[str], input_type: str) -> list[np.ndarray]:
    """Call Voyage's embedding endpoint on a batch of strings at once
    and return one numpy vector per input string."""
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            embeddings = await voyage_client.embed(
                texts, model="voyage-3.5-lite", input_type=input_type
            )
            break
        except voyageai.error.VoyageError:
            if attempt == MAX_RETRY_ATTEMPTS:
                raise
            logging.warning(
                f"Voyage embed failed (attempt {attempt}/{MAX_RETRY_ATTEMPTS}), retrying..."
            )
            await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    return [np.array(embedding) for embedding in embeddings.embeddings]


def most_similar(
    query_vector: np.ndarray,
    chunk_vectors: list[np.ndarray],
    chunks: list[dict],
    k: int = 3,
    min_similarity: float = 0.58,
) -> list[dict]:
    """Compute cosine similarity between query_vector and every entry in chunk_vectors,
    return the k chunks (each a {"text","url","title"} dict) with the highest scores."""
    similarities = [
        np.dot(query_vector, vec) / (np.linalg.norm(query_vector) * np.linalg.norm(vec))
        for vec in chunk_vectors
    ]
    top_indices = np.argsort(similarities)[-k:][::-1]
    return [chunks[i] for i in top_indices if similarities[i] >= min_similarity]


async def get_relevant_context(player_name: str, k_per_category: int = 2) -> dict[str, list[dict]]:
    """Retrieve the most relevant recent news snippets
    AND analyst/sportswriter commentary about a player."""
    news_articles, analysis_articles = await asyncio.gather(
        search_articles(player_name, category="news"),
        search_articles(player_name, category="analysis"),
    )
    news_chunks = [chunk for article in news_articles for chunk in chunk_article(article)]
    analysis_chunks = [chunk for article in analysis_articles for chunk in chunk_article(article)]
    news_query = f"recent news about {player_name} NBA"
    analysis_query = f"analyst opinion about {player_name} NBA"

    chunk_texts = [c["text"] for c in news_chunks] + [c["text"] for c in analysis_chunks]
    chunk_vectors, query_vectors = await asyncio.gather(
        embed_texts(chunk_texts, input_type="document"),
        embed_texts([news_query, analysis_query], input_type="query"),
    )
    news_vecs = chunk_vectors[: len(news_chunks)]
    analysis_vecs = chunk_vectors[len(news_chunks) :]
    return {
        "news": most_similar(query_vectors[0], news_vecs, news_chunks, k=k_per_category),
        "analysis": most_similar(
            query_vectors[1], analysis_vecs, analysis_chunks, k=k_per_category
        ),
    }

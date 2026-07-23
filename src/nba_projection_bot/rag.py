"""
rag.py — Stage 6b: retrieval-augmented "recent news" context for a player.

This module's job: given a player's name, retrieve a handful of the most
relevant snippets from (a) recent news articles and (b) analyst/sportswriter
commentary about them — genuine RAG (embed a small document set,
similarity-search it against a query, return the top matches), not just
another API wrapper.

News and analysis are kept as two separate categories throughout this
module, not merged into one pool. That's deliberate: a news item ("listed
questionable with a hamstring issue") is a reported fact, while an
analysis item ("this analyst expects a breakout") is someone's opinion
about future performance — the two need different framing downstream
(agent.py's system prompt), and pooling them into one ranked list risks
one category crowding out the other regardless of framing.

This is deliberately separate from the existing `web_search` tool. That
tool is a server-side Anthropic tool — it runs entirely on Anthropic's
infrastructure and the raw results never pass through this codebase, so
there's no hook to embed or rank them yourself. Building actual RAG means
owning the retrieval step, which means a client-side search call instead:

  1. search_articles()  — Tavily, a search API built for feeding LLM/RAG
     pipelines, returns clean article text per result (no HTML scraping).
  2. chunk_text()        — split each article into smaller embeddable pieces.
  3. embed_texts()        — Voyage AI, Anthropic's recommended embeddings
     provider, turns each chunk (and the query) into a vector.
  4. most_similar()       — rank chunks by cosine similarity to the query
     vector, return the top k. Plain numpy — no vector database needed,
     since this corpus is small and built fresh per call, never persisted.

Deliberate scope choice: results from this module are presented to the
user as a separate, clearly-labeled "recent news" note — never blended
into or allowed to influence the actual statistical projection, which
still comes only from simulation.project_stat. RAG here is for narrative
color, not data.
"""

import asyncio
from os import getenv
import re
from dotenv import load_dotenv
import numpy as np
from sqlalchemy import text
from tavily import TavilyClient
from voyageai import AsyncClient as AsyncVoyageAIClient

load_dotenv()
TAVILY_CLIENT_KEY = getenv("TAVILY_API_KEY")
VOYAGE_CLIENT_KEY = getenv("VOYAGE_API_KEY")

voyage_client = AsyncVoyageAIClient(api_key=VOYAGE_CLIENT_KEY, max_retries=3, timeout=30.0)
tavily_client = TavilyClient(api_key=TAVILY_CLIENT_KEY)

def chunk_text(text: str, chunk_size: int = 500) -> list[str]:
   """Split one article's text into smaller pieces of roughly `chunk_size`
      characters each."""
   sentences = re.split(r'(?<=[.!?])\s+', text)
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
   query = f"{player_name} NBA news" if category == "news" else f"{player_name} NBA analyst opinion outlook"
   response = await asyncio.to_thread(tavily_client.search, query, max_results=max_results)
   return [
      {"url": result["url"], "title": result.get("title", ""), "content": result["content"]}
      for result in response["results"] if "content" in result
   ]

def chunk_article(article: dict, chunk_size: int = 500) -> list[dict]:
   """Chunk one article's content, tagging each piece with the article's url/title."""
   return [
      {"text": piece, "url": article["url"], "title": article["title"]}
      for piece in chunk_text(article["content"], chunk_size)
   ]

async def embed_texts(texts: list[str], input_type: str) -> list[np.ndarray]:
   """Call Voyage's embedding endpoint on a batch of strings at once and return one numpy vector per input string."""
   embeddings = await voyage_client.embed(texts, model="voyage-3.5-lite", input_type=input_type)
   return [np.array(embedding) for embedding in embeddings.embeddings]

def most_similar(
   query_vector: np.ndarray,
   chunk_vectors: list[np.ndarray],
   chunks: list[dict],
   k: int = 3,
   min_similarity: float = 0.58,
) -> list[dict]:
   """Compute cosine similarity between query_vector and every entry in chunk_vectors, return the k chunks (each a {"text","url","title"} dict) with the highest scores."""
   similarities = [np.dot(query_vector, vec) / (np.linalg.norm(query_vector) * np.linalg.norm(vec)) for vec in chunk_vectors]
   top_indices = np.argsort(similarities)[-k:][::-1]
   return [chunks[i] for i in top_indices if similarities[i] >= min_similarity]

async def get_relevant_context(player_name: str, k_per_category: int = 2) -> dict[str, list[dict]]:
   """Retrieve the most relevant recent news snippets AND analyst/sportswriter commentary about a player."""
   news_articles, analysis_articles = await asyncio.gather(
      search_articles(player_name, category="news"),
      search_articles(player_name, category="analysis")
   )
   news_chunks = [chunk for article in news_articles for chunk in chunk_article(article)]
   analysis_chunks = [chunk for article in analysis_articles for chunk in chunk_article(article)]
   news_query = f"recent news about {player_name} NBA"
   analysis_query = f"analyst opinion about {player_name} NBA"

   chunk_texts = [c["text"] for c in news_chunks] + [c["text"] for c in analysis_chunks]
   chunk_vectors, query_vectors = await asyncio.gather(
      embed_texts(chunk_texts, input_type="document"),
      embed_texts([news_query, analysis_query], input_type="query")
   )
   news_vecs = chunk_vectors[:len(news_chunks)]
   analysis_vecs = chunk_vectors[len(news_chunks):]
   return {"news": most_similar(query_vectors[0], news_vecs, news_chunks, k=k_per_category),
           "analysis": most_similar(query_vectors[1], analysis_vecs, analysis_chunks, k=k_per_category)}

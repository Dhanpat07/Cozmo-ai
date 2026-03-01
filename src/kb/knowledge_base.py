"""
Knowledge Base using FAISS vector search
Stores FAQ entries, embedded at startup
Retrieves top-k matches for each user utterance
"""
import asyncio
import logging
import os
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = "text-embedding-3-small"  # Fast, cheap, good quality
EMBED_DIM = 1536

# --- FAQ Knowledge Base Entries ---
KNOWLEDGE_BASE = [
    {
        "id": "refund_policy",
        "text": "Our refund policy allows returns within 30 days of purchase for a full refund. Items must be in original condition. Digital products are non-refundable after download.",
        "keywords": ["refund", "return", "money back", "policy"]
    },
    {
        "id": "pricing_standard",
        "text": "Our standard plan costs $29/month, which includes 5 users, 10GB storage, and email support. No setup fees or contracts required.",
        "keywords": ["price", "cost", "how much", "pricing", "plan"]
    },
    {
        "id": "pricing_pro",
        "text": "Our Pro plan is $79/month, including unlimited users, 100GB storage, priority phone support, and advanced analytics. Annual billing saves 20%.",
        "keywords": ["pro plan", "premium", "enterprise", "upgrade"]
    },
    {
        "id": "pricing_objection",
        "text": "We understand cost is important. Compared to alternatives, our solution saves teams an average of 5 hours per week. We also offer a 14-day free trial with no credit card required.",
        "keywords": ["too expensive", "cheaper", "competitor", "discount", "expensive"]
    },
    {
        "id": "free_trial",
        "text": "Yes, we offer a free 14-day trial with full access to all Pro features. No credit card required to start. You'll receive a reminder 3 days before the trial ends.",
        "keywords": ["free trial", "try", "demo", "test", "no credit card"]
    },
    {
        "id": "cancellation",
        "text": "You can cancel your subscription at any time from your account settings. Cancellation takes effect at the end of your current billing period. No cancellation fees.",
        "keywords": ["cancel", "cancellation", "stop", "terminate", "end subscription"]
    },
    {
        "id": "support",
        "text": "Customer support is available Monday to Friday, 9am to 6pm EST. You can reach us via email at support@acmecorp.com or call 1-800-ACME-NOW. Pro users get 24/7 priority support.",
        "keywords": ["support", "help", "contact", "hours", "phone number"]
    },
    {
        "id": "security",
        "text": "We use AES-256 encryption for data at rest and TLS 1.3 for data in transit. We are SOC 2 Type II certified and GDPR compliant. Your data is stored in US-based data centers.",
        "keywords": ["security", "privacy", "encryption", "safe", "gdpr", "compliance"]
    },
    {
        "id": "integrations",
        "text": "We integrate with Salesforce, HubSpot, Slack, Microsoft Teams, Google Workspace, Zapier, and 50+ other platforms via REST API and webhooks.",
        "keywords": ["integrate", "integration", "connect", "api", "salesforce", "slack"]
    },
    {
        "id": "onboarding",
        "text": "Onboarding takes less than 10 minutes. We provide a step-by-step setup wizard, video tutorials, and live chat support. Most customers are up and running within the first day.",
        "keywords": ["setup", "onboard", "get started", "how long", "install", "implementation"]
    },
    {
        "id": "data_export",
        "text": "You can export all your data at any time in CSV, JSON, or Excel formats. Data exports are immediate and available from your account settings.",
        "keywords": ["export", "data", "download", "csv", "excel", "my data"]
    },
    {
        "id": "uptime_sla",
        "text": "We guarantee 99.9% uptime SLA. In the event of downtime, we provide service credits. Our status page at status.acmecorp.com shows real-time system health.",
        "keywords": ["uptime", "sla", "downtime", "reliability", "status", "outage"]
    }
]


class KnowledgeBase:
    def __init__(self):
        self._index = None
        self._entries = KNOWLEDGE_BASE.copy()
        self._embeddings: Optional[np.ndarray] = None
        self._initialized = False

    async def initialize(self):
        """Embed all entries and build FAISS index at startup"""
        logger.info("Initializing knowledge base with %d entries...", len(self._entries))

        try:
            import faiss
            texts = [e["text"] for e in self._entries]
            embeddings = await self._embed_batch(texts)

            if embeddings is not None:
                self._embeddings = np.array(embeddings, dtype=np.float32)
                # Normalize for cosine similarity
                faiss.normalize_L2(self._embeddings)
                self._index = faiss.IndexFlatIP(EMBED_DIM)
                self._index.add(self._embeddings)
                self._initialized = True
                logger.info("Knowledge base ready (FAISS index with %d vectors)", len(self._entries))
            else:
                logger.warning("KB embedding failed, using keyword fallback")

        except ImportError:
            logger.warning("FAISS not available, using keyword-based search fallback")
        except Exception as e:
            logger.error("KB initialization error: %s", e, exc_info=True)

    async def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Search knowledge base for relevant entries.
        Returns list of {text, score} dicts.
        """
        if self._initialized and self._index is not None:
            return await self._vector_search(query, top_k)
        else:
            return self._keyword_search(query, top_k)

    async def _vector_search(self, query: str, top_k: int) -> list[dict]:
        """Vector similarity search using FAISS"""
        try:
            import faiss
            query_embedding = await self._embed_single(query)
            if query_embedding is None:
                return self._keyword_search(query, top_k)

            q = np.array([query_embedding], dtype=np.float32)
            faiss.normalize_L2(q)

            scores, indices = self._index.search(q, top_k)

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and score > 0.3:  # Threshold for relevance
                    results.append({
                        "text": self._entries[idx]["text"],
                        "score": float(score),
                        "id": self._entries[idx]["id"]
                    })
            return results

        except Exception as e:
            logger.error("Vector search failed: %s", e)
            return self._keyword_search(query, top_k)

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        """Fallback keyword-based search"""
        query_lower = query.lower()
        scored = []

        for entry in self._entries:
            score = sum(
                1 for kw in entry["keywords"]
                if kw in query_lower
            )
            # Also check if any words from query appear in text
            query_words = set(query_lower.split())
            text_words = set(entry["text"].lower().split())
            overlap = len(query_words & text_words)
            score += overlap * 0.1

            if score > 0:
                scored.append({"text": entry["text"], "score": score, "id": entry["id"]})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    async def _embed_batch(self, texts: list[str]) -> Optional[list]:
        """Embed multiple texts using OpenAI embeddings API"""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"model": EMBED_MODEL, "input": texts}
            async with session.post(
                "https://api.openai.com/v1/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [item["embedding"] for item in data["data"]]
                else:
                    logger.error("Embedding API error: %d", resp.status)
                    return None

    async def _embed_single(self, text: str) -> Optional[list]:
        """Embed a single text"""
        results = await self._embed_batch([text])
        return results[0] if results else None

"""Two knowledge bases in ChromaDB (in memory) with a self-correcting search.

KB 1: procurement_policy   (reorder rules, approval matrix, laws)
KB 2: supplier_contracts   (supplier profiles and scorecard)

Documents are split at '## ' headings. Each chunk becomes a TF-IDF vector
(scikit-learn). Chroma stores the vectors and finds the closest chunks.
Agentic RAG: search, grade the results, rewrite the query if they are weak, search once more.
"""
import re
from functools import lru_cache
from pathlib import Path

import chromadb
from sklearn.feature_extraction.text import TfidfVectorizer

KB_DIR = Path(__file__).parent / "knowledge_base"
KBS = ["procurement_policy", "supplier_contracts"]
GOOD_ENOUGH = 0.12       # cosine similarity a result needs to count as relevant


def _chunks():
    out = []
    for kb in KBS:
        for path in sorted((KB_DIR / kb).glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            for part in re.split(r"\n(?=## )", text):
                heading = part.splitlines()[0].lstrip("# ").strip()
                body = part.strip()
                if len(body) > 40:
                    out.append({"kb": kb, "source": f"{kb}/{path.name}", "doc_title": title,
                                "heading": heading, "text": body})
    return out


@lru_cache(maxsize=1)
def _index():
    chunks = _chunks()
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    matrix = vec.fit_transform([c["heading"] + " " + c["text"] for c in chunks])
    client = chromadb.EphemeralClient()
    cols = {}
    for kb in KBS:
        col = client.get_or_create_collection(f"{kb}_v1", metadata={"hnsw:space": "cosine"},
                                              embedding_function=None)
        ids, embs, docs, metas = [], [], [], []
        for i, c in enumerate(chunks):
            if c["kb"] == kb:
                ids.append(f"{kb}-{i}")
                embs.append(matrix[i].toarray()[0].tolist())
                docs.append(c["text"])
                metas.append({"source": c["source"], "heading": c["heading"]})
        col.add(ids=ids, embeddings=embs, documents=docs, metadatas=metas)
        cols[kb] = col
    return vec, cols


def search(query: str, kb: str = "both", k: int = 3):
    """Plain vector search. Returns [{source, heading, text, score}] best first."""
    vec, cols = _index()
    q = vec.transform([query]).toarray()[0].tolist()
    if not any(q):
        return []
    hits = []
    for name in (KBS if kb == "both" else [kb]):
        r = cols[name].query(query_embeddings=[q], n_results=k)
        for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
            hits.append({"kb": name, "source": meta["source"], "heading": meta["heading"],
                         "text": doc, "score": round(1 - dist, 3)})
    return sorted(hits, key=lambda h: -h["score"])[:k]


def _grade(hits, llm=None, query=""):
    """Are the results good enough? AI grades when available, else a similarity threshold."""
    if not hits:
        return False
    if llm is not None:
        try:
            ask = (f"Question: {query}\n\nTop result:\n{hits[0]['text'][:800]}\n\n"
                   "Does this result help answer the question? Reply only yes or no.")
            return llm.invoke(ask).content.strip().lower().startswith("y")
        except Exception:
            pass
    return hits[0]["score"] >= GOOD_ENOUGH


def _rewrite(query, llm=None):
    if llm is not None:
        try:
            ask = ("Rewrite this search query for a purchasing policy and supplier knowledge base. "
                   f"Use plain keywords, one line only.\nQuery: {query}")
            return llm.invoke(ask).content.strip().splitlines()[0][:200]
        except Exception:
            pass
    extra = {"buy": "order purchase", "cheap": "price supplier score", "credit": "payment terms MSME",
             "late": "lead time", "fresh": "shelf life perishable", "boss": "manager approval"}
    words = [extra.get(w.lower(), w) for w in query.split()]
    return " ".join(words) + " policy supplier"


def agentic_search(query, kb="both", llm=None):
    """Search, grade, rewrite once, search again. Returns (hits, trace)."""
    trace = [f"search: {query}"]
    hits = search(query, kb)
    if _grade(hits, llm, query):
        trace.append("grade: relevant")
        return hits, trace
    new_q = _rewrite(query, llm)
    trace += ["grade: weak", f"rewrite: {new_q}"]
    hits2 = search(new_q, kb)
    trace.append("grade after retry: " + ("relevant" if _grade(hits2, llm, new_q) else "still weak"))
    return (hits2 or hits), trace


def answer(question, llm=None):
    """Used by the 'Ask the knowledge base' page."""
    hits, trace = agentic_search(question, "both", llm)
    if llm is not None and hits:
        ctx = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
        try:
            msg = llm.invoke("Answer from the context only, in 2-4 plain sentences, and name the source file.\n\n"
                             f"Context:\n{ctx}\n\nQuestion: {question}")
            return msg.content, hits, trace
        except Exception as e:
            trace.append(f"AI answer failed ({type(e).__name__}), showing the best passage")
    if not hits:
        return "Nothing relevant found in the knowledge bases.", hits, trace
    best = hits[0]
    return f"From {best['source']} ({best['heading']}):\n\n{best['text']}", hits, trace

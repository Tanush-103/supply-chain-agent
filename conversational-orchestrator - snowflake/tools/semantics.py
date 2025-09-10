from typing import List, Dict
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


# Very light semantic matcher for fuzzy queries → metrics/fields/topics


CANON_TOPICS = {
"fast_moving": ["fast-moving", "fast moving", "high velocity", "top sellers", "abc a"],
"inventory": ["inventory", "stock", "on hand", "soh"],
"reorder": ["reorder", "order", "purchase", "buy"],
"lead_time": ["lead time", "supplier lead"],
"demand": ["forecast", "demand"],
"transport_cost": ["transport", "shipping", "freight"],
}


class SemanticMatcher:
    def __init__(self):
        corpus = []
        self.labels = []
        for k, phrases in CANON_TOPICS.items():
            for ph in phrases:
                corpus.append(ph)
                self.labels.append(k)
        self.vectorizer = TfidfVectorizer(ngram_range=(1,2)).fit(corpus)
        self.emb = self.vectorizer.transform(corpus)


    def match(self, query: str, topk: int = 3) -> List[str]:
        q = self.vectorizer.transform([query])
        sims = (self.emb @ q.T).toarray().ravel()
        idx = np.argsort(-sims)[:topk]
        return list(dict.fromkeys([self.labels[i] for i in idx]))
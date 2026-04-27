"""Удалить старые S3-чанки → сбросить state → /admin/update/docs → проверить."""
import sys, io, json, time, uuid
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from pathlib import Path
import requests, chromadb

CHROMA_DIR = "./chroma_gigachat"
COLLECTION = "eduirk"
STATE_FILE = Path("update_state.json")
API = "http://127.0.0.1:8000"

client = chromadb.PersistentClient(path=CHROMA_DIR)
col = client.get_collection(COLLECTION)
total = col.count()
print(f"Векторов: {total}")

ids = []
BATCH = 1000
for off in range(0, total, BATCH):
    g = col.get(limit=BATCH, offset=off, include=["metadatas"])
    for cid, m in zip(g["ids"], g["metadatas"]):
        if m.get("s3_key"):
            ids.append(cid)
if ids:
    col.delete(ids=ids)
    print(f"Удалено S3-чанков: {len(ids)}. Осталось: {col.count()}")

data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
data["s3_docs"] = {}
STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("state.s3_docs очищен")

r = requests.post(f"{API}/admin/update/docs")
print(f"POST /admin/update/docs → {r.status_code}")

for _ in range(180):
    time.sleep(5)
    st = requests.get(f"{API}/admin/update/status").json()
    bg = st.get("background", {})
    p = bg.get("progress") or {}
    print(f"  running={bg.get('running')}  [{p.get('stage','—')}] {p.get('current','?')}/{p.get('total','?')}  {(p.get('detail') or '')[:70]}")
    if not bg.get("running"):
        print(f"Done. result={bg.get('result')}  error={bg.get('error')}")
        break

# Проверки
target = "О_проведении_мероприятия_МБУДО_г_Иркутска_ДДТ_№_5_27_01_2026.pdf"
post_total = col.count()
header_count = 0
for off in range(0, post_total, BATCH):
    g = col.get(limit=BATCH, offset=off, include=["metadatas"])
    for m in g["metadatas"]:
        if m.get("is_header") and m.get("s3_key") == target:
            header_count += 1
print(f"\nheader-чанков для target: {header_count}  (всего векторов: {post_total})")

# Задаём те же запросы, что раньше проваливались
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
emb = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-large",
    model_kwargs={"device":"cpu"}, encode_kwargs={"normalize_embeddings":True})
vs = Chroma(collection_name=COLLECTION, persist_directory=CHROMA_DIR, embedding_function=emb)

for q in [
    "О_проведении_мероприятия_МБУДО_г_Иркутска_ДДТ_№_5_27_01_2026",
    "мероприятие МБУДО ДДТ №5 27 января 2026",
    "расскажи о О_проведении_мероприятия_МБУДО_г_Иркутска_ДДТ_№_5_27_01_2026",
]:
    print(f"\n--- {q!r} ---")
    hits = vs.similarity_search_with_score(q, k=10)
    rank = None
    for i, (h, sc) in enumerate(hits, 1):
        is_target = h.metadata.get("s3_key") == target
        is_hdr = h.metadata.get("is_header", False)
        tag = ""
        if is_target:
            tag = "  <-- TARGET" + (" (HEADER)" if is_hdr else "")
            if rank is None: rank = i
        print(f"  {i:2}. {sc:.3f}  [{h.metadata.get('title','')[:50]}]{tag}")
    print(f"  target в топ-10: {rank}")

# Спросить ассистента
sid = f"fresh-{uuid.uuid4().hex[:8]}"
r = requests.post(f"{API}/assistant/ask",
    json={"question":"расскажи о О_проведении_мероприятия_МБУДО_г_Иркутска_ДДТ_№_5_27_01_2026","session_id":sid},
    timeout=120)
d = r.json()
print("\n=== /assistant/ask ===")
print("ANSWER:", d.get("answer")[:500])

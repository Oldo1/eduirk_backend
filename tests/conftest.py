import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.gettempdir()}/mky_pytest.db")
os.environ.setdefault("ENABLE_RAG", "false")

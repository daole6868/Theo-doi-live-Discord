"""
Thiết lập môi trường cho test: token giả + thư mục dữ liệu tạm,
để test không đụng vào data/ thật. Chạy:

    python -m unittest discover -s tests -t .
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="gachaz-test-")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ["GACHAZ_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["GACHAZ_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["GUILD_ID"] = "0"

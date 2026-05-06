import argparse
import secrets
import hashlib
import hmac
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SIS_AGENT_ROOT = Path(__file__).resolve().parent.parent
USER_STORE_DB_PATH = SIS_AGENT_ROOT / "src" / "users.db"

def _get_connection():
    """返回 SQLite 连接。"""
    ensure_user_store_exists()
    conn = sqlite3.connect(USER_STORE_DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _initialize_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

def ensure_user_store_exists() -> None:
    USER_STORE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_STORE_DB_PATH, timeout=10, check_same_thread=False)
    _initialize_db(conn)
    conn.close()

def hash_password(password: str, salt: bytes | None = None) -> str:
    """生成 PBKDF2-SHA256 密码哈希，格式为 salt$hash。"""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + digest.hex()

def verify_password(password: str, stored_value: str) -> bool:
    """验证密码是否与存储值匹配。"""
    try:
        salt_hex, hash_hex = stored_value.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False

    new_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(new_digest, expected)

def verify_user(username: str, password: str) -> bool:
    """验证用户名和密码。"""
    if not username or not password:
        return False
    conn = _get_connection()
    cursor = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return verify_password(password, row["password_hash"])

def add_user(username: str, password: str) -> bool:
    """添加或更新用户账号。"""
    if not username or not password:
        return False
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)"
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, updated_at=excluded.updated_at",
        (username, password_hash, now, now),
    )
    conn.commit()
    conn.close()
    return True


def remove_user(username: str) -> bool:
    """删除用户账号。"""
    conn = _get_connection()
    cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def list_users() -> list[str]:
    """返回当前所有用户名列表。"""
    conn = _get_connection()
    cursor = conn.execute("SELECT username FROM users ORDER BY username")
    rows = [row["username"] for row in cursor.fetchall()]
    conn.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="管理内部登录用户账号")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_add = subparsers.add_parser("add", help="新增或更新一个用户")
    parser_add.add_argument("username", help="用户名")
    parser_add.add_argument("password", help="用户密码")

    parser_remove = subparsers.add_parser("remove", help="删除一个用户")
    parser_remove.add_argument("username", help="用户名")

    parser_list = subparsers.add_parser("list", help="列出所有用户")

    args = parser.parse_args()
    if args.command == "add":
        if add_user(args.username.strip(), args.password):
            print(f"已创建/更新用户: {args.username}")
        else:
            print("用户名和密码不能为空")
    elif args.command == "remove":
        if remove_user(args.username.strip()):
            print(f"已删除用户: {args.username}")
        else:
            print(f"用户不存在: {args.username}")
    elif args.command == "list":
        for name in list_users():
            print(name)


if __name__ == "__main__":
    main()

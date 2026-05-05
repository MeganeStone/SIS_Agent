import argparse
import json
import os
import secrets
import hashlib
import hmac
from pathlib import Path

SIS_AGENT_ROOT = Path(__file__).resolve().parent.parent
USER_STORE_PATH = SIS_AGENT_ROOT / "src" / "users.json"


def ensure_user_store_exists() -> None:
    """确保用户存储文件存在，不存在时创建空 JSON。"""
    USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not USER_STORE_PATH.exists():
        USER_STORE_PATH.write_text("{}", encoding="utf-8")


def load_users() -> dict:
    """加载账号存储。"""
    ensure_user_store_exists()
    try:
        with USER_STORE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_users(users: dict) -> None:
    """保存账号存储。"""
    USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USER_STORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


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
    users = load_users()
    stored = users.get(username)
    if not stored:
        return False
    return verify_password(password, stored)


def add_user(username: str, password: str) -> bool:
    """添加或更新用户账号。"""
    if not username or not password:
        return False
    users = load_users()
    users[username] = hash_password(password)
    save_users(users)
    return True


def remove_user(username: str) -> bool:
    """删除用户账号。"""
    users = load_users()
    if username not in users:
        return False
    users.pop(username)
    save_users(users)
    return True


def list_users() -> list[str]:
    """返回当前所有用户名列表。"""
    return list(load_users().keys())


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

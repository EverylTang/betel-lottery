"""设置（或重置）后台账号口令。

启动时的 ADMIN_BOOTSTRAP_PASSWORD 只在账号不存在时创建它，改 .env 不会更新
已存在的 admin 行。ngrok 隧道会把 /api/admin/auth/login 暴露到公网，必须先用
本脚本把口令换成强值，再把同一个值写回 .env / .env.ngrok 保持一致。

用法：
    .venv/bin/python -m scripts.set_admin_password --username admin
    BETEL_ADMIN_PASSWORD='...' .venv/bin/python -m scripts.set_admin_password --password-from-env
"""

import argparse
import os
import secrets
import string

from sqlmodel import Session, select

from app.core.config import get_settings
from app.db import engine
from app.models import AdminUser
from app.security import hash_password

# 排除易混淆字符，便于口头传递与手输。
_ALPHABET = (string.ascii_lowercase + string.ascii_uppercase + string.digits).replace("l", "").replace("O", "").replace("0", "")


def generate(length: int = 20) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def main() -> None:
    parser = argparse.ArgumentParser(description="设置后台账号口令（会覆盖旧口令）")
    parser.add_argument("--username", default=get_settings().admin_bootstrap_username)
    parser.add_argument("--password", help="直接指定口令；不传则随机生成并打印一次")
    parser.add_argument("--password-from-env", action="store_true", help="从 BETEL_ADMIN_PASSWORD 环境变量读取，避免口令进入 shell 历史")
    args = parser.parse_args()

    password = args.password or (os.environ.get("BETEL_ADMIN_PASSWORD", "") if args.password_from_env else "")
    if not password:
        password = generate()
    if len(password) < 12 or password == "change-me-now":
        raise SystemExit("口令至少 12 位且不能使用默认值 change-me-now")

    with Session(engine) as session:
        admin = session.exec(select(AdminUser).where(AdminUser.username == args.username)).first()
        created = admin is None
        if created:
            admin = AdminUser(username=args.username, password_hash=hash_password(password))
            session.add(admin)
        else:
            admin.password_hash = hash_password(password)
            session.add(admin)
        session.commit()
        session.refresh(admin)
        username, role = admin.username, admin.role

    print(f"{'已创建账号' if created else '已更新口令'}：username={username} role={role}")
    if not args.password and not args.password_from_env:
        print(f"password={password}")
        print("请立即保存；本进程退出后不再显示，后续可用本脚本重置。")


if __name__ == "__main__":
    main()

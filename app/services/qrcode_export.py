import csv
import io
import zipfile

import qrcode
from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import QrCode, QrCodeBatch
from app.security import decrypt_qr_token


def export_print_package(session: Session, batch_id: int) -> tuple[str, bytes]:
    batch = session.get(QrCodeBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    codes = session.exec(select(QrCode).where(QrCode.batch_id == batch.id).order_by(QrCode.id)).all()
    if not codes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="批次没有可导出的二维码")
    if any(not code.token_ciphertext or not code.redemption_code_ciphertext for code in codes):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该批次由旧版本生成，缺少袋内兑奖码，请新建批次后导出")

    output = io.BytesIO()
    manifest = io.StringIO(newline="")
    writer = csv.writer(manifest)
    writer.writerow(["code_no", "scan_url", "redemption_code", "batch_no", "status"])
    base_url = get_settings().h5_base_url.rstrip("/")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for code in codes:
            raw_token = decrypt_qr_token(code.token_ciphertext or "")
            scan_url = f"{base_url}/?t={raw_token}"
            image = qrcode.make(scan_url)
            image_bytes = io.BytesIO()
            image.save(image_bytes, format="PNG", optimize=False)
            archive.writestr(f"qrcodes/{code.code_no}.png", image_bytes.getvalue())
            redemption_code = decrypt_qr_token(code.redemption_code_ciphertext or "")
            writer.writerow([code.code_no, scan_url, redemption_code, batch.batch_no, code.status])
        archive.writestr("manifest.csv", "\ufeff" + manifest.getvalue())
        archive.writestr("README.txt", "二维码印刷包\n每个 PNG 对应包装袋外抽奖二维码；manifest.csv 中 redemption_code 为需印在包装袋内的 4 位兑奖码。请勿修改二维码图案和四周留白。\n")
    return f"{batch.batch_no}-print-package.zip", output.getvalue()


def export_code_csv(session: Session, batch_id: int) -> tuple[str, bytes]:
    batch = session.get(QrCodeBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    codes = session.exec(select(QrCode).where(QrCode.batch_id == batch.id).order_by(QrCode.id)).all()
    if not codes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="批次没有可导出的抽奖码")
    if any(not code.token_ciphertext or not code.redemption_code_ciphertext for code in codes):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该批次由旧版本生成，缺少袋内兑奖码，请新建批次后导出")

    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(["code_no", "scan_url", "redemption_code", "batch_no", "status"])
    base_url = get_settings().h5_base_url.rstrip("/")
    for code in codes:
        token = decrypt_qr_token(code.token_ciphertext or "")
        redemption_code = decrypt_qr_token(code.redemption_code_ciphertext or "")
        writer.writerow([code.code_no, f"{base_url}/?t={token}", redemption_code, batch.batch_no, code.status])
    return f"{batch.batch_no}-codes.csv", ("\ufeff" + content.getvalue()).encode("utf-8")

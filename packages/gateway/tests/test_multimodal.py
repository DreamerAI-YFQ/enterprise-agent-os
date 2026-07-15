"""Tests for multimodal file upload + LLM message content building.

Covers:
- POST /upload (image, text file, unsupported type, oversized)
- multimodal_loader.load_attachment (image -> data URL, text file -> text_content)
- openai_adapter._build_message_content (no attachments, image, file, mixed)
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from eaos.gateway.api.routes.multimodal_loader import (
    extension_for_mime,
    load_attachment,
    resolve_upload_path,
    upload_url,
)
from eaos.infra.llm.base import Attachment, Message
from eaos.infra.llm.openai_adapter import OpenAILLMClient
from httpx import ASGITransport, AsyncClient

SECRET = "f0-t7-t9-t10-secret-32byte!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")


def _config(*, uploads_dir: str) -> AppConfig:
    config = AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]
    config.uploads.dir = uploads_dir
    return config


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


# ============================================================
# Helpers
# ============================================================


class TestMultimodalHelpers:
    def test_extension_for_mime_known(self) -> None:
        assert extension_for_mime("image/jpeg") == "jpg"
        assert extension_for_mime("image/png") == "png"
        assert extension_for_mime("application/pdf") == "pdf"
        assert extension_for_mime("text/plain") == "txt"

    def test_extension_for_mime_unknown(self) -> None:
        assert extension_for_mime("application/octet-stream") == "bin"

    def test_resolve_upload_path(self) -> None:
        path = resolve_upload_path(
            "abc-123",
            tenant_id="t-1",
            extension="png",
            base_dir="uploads",
        )
        assert path == Path("uploads") / "t-1" / "abc-123.png"

    def test_upload_url(self) -> None:
        url = upload_url("abc-123", tenant_id="t-1", extension="png")
        assert url == "/uploads/t-1/abc-123.png"


# ============================================================
# load_attachment
# ============================================================


class TestLoadAttachment:
    async def test_load_image_returns_data_url(self, tmp_path: Path) -> None:
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = tmp_path / "test.png"
        path.write_bytes(image_bytes)

        att = await load_attachment(
            str(path),
            mime_type="image/png",
            name="test.png",
            base_dir=str(tmp_path),
        )
        assert att.type == "image"
        assert att.data_url is not None
        assert att.data_url.startswith("data:image/png;base64,")
        b64_part = att.data_url.split(",", 1)[1]
        assert base64.b64decode(b64_part) == image_bytes

    async def test_load_text_file_returns_text_content(self, tmp_path: Path) -> None:
        path = tmp_path / "note.txt"
        path.write_text("hello world", encoding="utf-8")

        att = await load_attachment(
            str(path),
            mime_type="text/plain",
            name="note.txt",
            base_dir=str(tmp_path),
        )
        assert att.type == "file"
        assert att.text_content == "hello world"
        assert att.data_url is None

    async def test_load_markdown_file(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text("# Title\n\nSome content.", encoding="utf-8")

        att = await load_attachment(
            str(path),
            mime_type="text/markdown",
            name="doc.md",
            base_dir=str(tmp_path),
        )
        assert att.type == "file"
        assert "# Title" in (att.text_content or "")

    async def test_load_attachment_file_not_found(self, tmp_path: Path) -> None:
        try:
            await load_attachment(
                str(tmp_path / "missing.png"),
                mime_type="image/png",
                name="missing.png",
                base_dir=str(tmp_path),
            )
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass

    async def test_load_pdf_file(self, tmp_path: Path) -> None:
        """PDF text extraction requires pypdf; skip if not available."""
        try:
            from pypdf import PdfWriter
        except ImportError:
            return  # skip if pypdf not installed

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        path = tmp_path / "blank.pdf"
        with path.open("wb") as f:
            writer.write(f)

        att = await load_attachment(
            str(path),
            mime_type="application/pdf",
            name="blank.pdf",
            base_dir=str(tmp_path),
        )
        assert att.type == "file"
        # Blank PDF has no extractable text; text_content may be empty string.
        assert att.text_content is not None


# ============================================================
# OpenAILLMClient._build_message_content (via _message_to_dict)
# ============================================================


class TestMessageToDictMultimodal:
    def test_no_attachments_returns_str_content(self) -> None:
        m = Message(role="user", content="hello")
        d = OpenAILLMClient._message_to_dict(m)
        assert d["content"] == "hello"

    def test_image_attachment_returns_list_content(self) -> None:
        m = Message(
            role="user",
            content="describe this",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    name="img.png",
                    data_url="data:image/png;base64,abc",
                )
            ],
        )
        d = OpenAILLMClient._message_to_dict(m)
        assert isinstance(d["content"], list)
        parts = d["content"]
        assert parts[0] == {"type": "text", "text": "describe this"}
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc"},
        }

    def test_file_attachment_appends_text_to_content(self) -> None:
        m = Message(
            role="user",
            content="summarize this",
            attachments=[
                Attachment(
                    type="file",
                    mime_type="text/plain",
                    name="note.txt",
                    text_content="hello world",
                )
            ],
        )
        d = OpenAILLMClient._message_to_dict(m)
        assert isinstance(d["content"], list)
        text_part = d["content"][0]
        assert text_part["type"] == "text"
        assert "summarize this" in text_part["text"]
        assert "附件 note.txt 内容:" in text_part["text"]
        assert "hello world" in text_part["text"]

    def test_mixed_image_and_file_attachments(self) -> None:
        m = Message(
            role="user",
            content="analyze",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    name="img.png",
                    data_url="data:image/png;base64,abc",
                ),
                Attachment(
                    type="file",
                    mime_type="text/plain",
                    name="note.txt",
                    text_content="hello world",
                ),
            ],
        )
        d = OpenAILLMClient._message_to_dict(m)
        assert isinstance(d["content"], list)
        text_part = d["content"][0]
        assert text_part["text"] == "analyze\n\n附件 note.txt 内容:\nhello world"
        image_part = d["content"][1]
        assert image_part["type"] == "image_url"

    def test_multiple_images(self) -> None:
        m = Message(
            role="user",
            content="compare these",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    name="a.png",
                    data_url="data:image/png;base64,aaa",
                ),
                Attachment(
                    type="image",
                    mime_type="image/jpeg",
                    name="b.jpg",
                    data_url="data:image/jpeg;base64,bbb",
                ),
            ],
        )
        d = OpenAILLMClient._message_to_dict(m)
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 3  # 1 text + 2 images
        assert d["content"][1]["image_url"]["url"] == "data:image/png;base64,aaa"
        assert d["content"][2]["image_url"]["url"] == "data:image/jpeg;base64,bbb"

    def test_attachment_without_data_url_skipped(self) -> None:
        """An image attachment without data_url is silently skipped."""
        m = Message(
            role="user",
            content="hello",
            attachments=[
                Attachment(
                    type="image",
                    mime_type="image/png",
                    name="empty.png",
                    data_url=None,
                )
            ],
        )
        d = OpenAILLMClient._message_to_dict(m)
        # No valid multimodal content -> falls back to string.
        assert d["content"] == "hello"


# ============================================================
# POST /upload
# ============================================================


class TestUploadRoute:
    async def test_upload_image(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("test.png", b"\x89PNG\r\n\x1a\n", "image/png")},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["file_id"]
        assert data["url"].startswith("/uploads/")
        assert data["type"] == "image"
        assert data["mime_type"] == "image/png"
        assert data["name"] == "test.png"
        assert data["size_bytes"] > 0
        # File exists on disk.
        file_path = tmp_path / str(TID) / f"{data['file_id']}.png"
        assert file_path.exists()

    async def test_upload_text_file(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("note.txt", b"hello world", "text/plain")},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "file"
        assert data["mime_type"] == "text/plain"

    async def test_upload_unsupported_type_rejected(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 415

    async def test_upload_oversized_rejected(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        # Override max size to 1 byte for this test.
        app.state.config.uploads.max_size_mb = 0
        # max_size_mb=0 -> 0 bytes allowed, so even 1 byte is over.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("big.txt", b"x" * 100, "text/plain")},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 413

    async def test_upload_no_auth_returns_401(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("test.png", b"\x89PNG", "image/png")},
            )
        assert resp.status_code == 401

    async def test_upload_admin_also_works(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/upload",
                files={"file": ("doc.md", b"# Title", "text/markdown")},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        assert resp.json()["type"] == "file"


# ============================================================
# /uploads static file serving
# ============================================================


class TestUploadsStaticFiles:
    async def test_uploaded_file_accessible_via_url(self, tmp_path: Path) -> None:
        app = create_app(_config(uploads_dir=str(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            upload_resp = await client.post(
                "/upload",
                files={"file": ("test.txt", b"hello world", "text/plain")},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
            assert upload_resp.status_code == 201
            url = upload_resp.json()["url"]

            # The file should be accessible via GET.
            get_resp = await client.get(url)
        assert get_resp.status_code == 200
        assert get_resp.content == b"hello world"

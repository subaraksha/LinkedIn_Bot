"""Synthetic resume parser checks; no personal documents or provider calls."""

import tempfile
import unittest
from pathlib import Path

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

from app.services.resume_import import ResumeImportError, _parse


class ResumeImportTests(unittest.TestCase):
    def test_utf8_text_and_docx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain = root / "synthetic.txt"
            plain.write_text("Synthetic candidate built a demonstration project in Python.", encoding="utf-8")
            sections = _parse(plain, "txt")
            self.assertEqual(sections[0]["label"], "Document")
            self.assertIn("Python", sections[0]["text"])
            doc = Document()
            doc.add_paragraph("Synthetic candidate worked on a demonstration project.")
            docx = root / "synthetic.docx"
            doc.save(docx)
            sections = _parse(docx, "docx")
            self.assertEqual(sections[0]["label"], "Paragraph 1")
            pdf = root / "synthetic.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 72 720 Td (Synthetic candidate built a Python project.) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with pdf.open("wb") as output:
                writer.write(output)
            sections = _parse(pdf, "pdf")
            self.assertEqual(sections[0]["label"], "Page 1")
            self.assertIn("Python", sections[0]["text"])

    def test_rejects_mislabeled_and_empty_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake.pdf"
            path.write_text("This is not a real PDF document.")
            with self.assertRaises(ResumeImportError):
                _parse(path, "pdf")
            path.write_bytes(b"\x00" + b"a" * 30)
            with self.assertRaises(ResumeImportError):
                _parse(path, "txt")

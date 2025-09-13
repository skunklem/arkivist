import markdown
import mammoth
from pathlib import Path

# md_to_html / html_to_md, pandoc adapters, fallback

def md_to_html(text: str) -> str:
    html_body = markdown.markdown(
        text or "",
        extensions=[
            "extra",
            "sane_lists",
            "smarty",
            "toc",
            "tables",
            "fenced_code",
        ],
        output_format="html5",
    )
    css = """<style>/* same CSS as above */</style>"""
    return f"<!doctype html><meta charset='utf-8'><body>{css}{html_body}</body>"


def docx_to_markdown(path: str) -> str:
    # # 1) Pandoc (best)
    # if shutil.which("pandoc"):
    #     try:
    #         out = subprocess.check_output(
    #             ["pandoc", "-f", "docx", "-t", "gfm", "--wrap=none", path],
    #             stderr=subprocess.STDOUT
    #         )
    #         return out.decode("utf-8", errors="replace")
    #     except subprocess.CalledProcessError as e:
    #         print("Pandoc failed:", e.output.decode("utf-8", "replace"))

    # 2) Mammoth (very good)
    if mammoth:
        try:
            with open(path, "rb") as f:
                result = mammoth.convert_to_markdown(f)
            return result.value
        except Exception as e:
            print("Mammoth failed:", e)

    # 3) Fallback: rough text (bullets won’t survive)
    try:
        from docx import Document
        d = Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    except Exception as e:
        print("python-docx fallback failed:", e)
        return ""

def read_file_as_markdown(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return docx_to_markdown(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
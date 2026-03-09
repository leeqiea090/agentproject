"""提取招标和投标示例文件的内容"""
import pypdf
import os

sample_dir = r"C:\Users\lq\Desktop\新建文件夹"

def extract_pdf_text(pdf_path):
    """提取PDF文本内容"""
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = pypdf.PdfReader(file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text
    except Exception as e:
        return f"Error reading {pdf_path}: {str(e)}"

# 提取所有示例文件
files = [
    ("招标文件-1.pdf", "投标文件-1.pdf"),
    ("招标文件-2.pdf", "投标文件-2.pdf")
]

for i, (tender_file, bid_file) in enumerate(files, 1):
    print(f"\n{'='*80}")
    print(f"示例对 {i}")
    print(f"{'='*80}")

    tender_path = os.path.join(sample_dir, tender_file)
    bid_path = os.path.join(sample_dir, bid_file)

    print(f"\n--- 招标文件 {i} ---")
    tender_text = extract_pdf_text(tender_path)
    print(f"字符数: {len(tender_text)}")
    print(f"前500字符:\n{tender_text[:500]}")

    print(f"\n--- 投标文件 {i} ---")
    bid_text = extract_pdf_text(bid_path)
    print(f"字符数: {len(bid_text)}")
    print(f"前500字符:\n{bid_text[:500]}")

    # 保存完整内容到文本文件
    with open(f"招标文件-{i}.txt", "w", encoding="utf-8") as f:
        f.write(tender_text)
    with open(f"投标文件-{i}.txt", "w", encoding="utf-8") as f:
        f.write(bid_text)

    print(f"\n完整内容已保存到: 招标文件-{i}.txt 和 投标文件-{i}.txt")

"""招标文件解析服务"""
import pypdf
from pathlib import Path
from typing import Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
import json
import logging

from app.schemas import (
    TenderDocument,
    ProcurementPackage,
    CommercialTerms
)

try:
    from docx import Document as _DocxDocument
    _DOCX_AVAILABLE = True
except ImportError:
    _DOCX_AVAILABLE = False

logger = logging.getLogger(__name__)


class TenderParser:
    """招标文件解析器"""

    def __init__(self, llm: ChatOpenAI | None = None):
        """
        初始化解析器

        Args:
            llm: 语言模型实例，如果为None则使用默认配置
        """
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)

        # 信息提取Prompt
        self.extraction_prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一个专业的政府采购招标文件分析专家。你的任务是从招标文件中准确提取关键信息。

要求：
1. 确保所有数字信息准确无误（金额、数量等）
2. 完整提取技术参数，不要遗漏
3. 准确识别商务条款
4. 如果某些信息缺失，对应字段设为空字符串或空列表
5. 输出必须是有效的JSON格式

输出JSON格式示例：
{{
    "project_name": "项目名称",
    "project_number": "项目编号",
    "budget": 1000000.00,
    "purchaser": "采购单位",
    "agency": "代理机构",
    "procurement_type": "竞争性谈判/竞争性磋商/公开招标",
    "packages": [
        {{
            "package_id": "1",
            "item_name": "货物名称",
            "quantity": 1,
            "budget": 100000.00,
            "technical_requirements": {{"参数1": "要求1", "参数2": "要求2"}},
            "delivery_time": "签订合同后XX个工作日",
            "delivery_place": "采购人指定地点"
        }}
    ],
    "commercial_terms": {{
        "payment_method": "验收完成后支付",
        "validity_period": "90日历天",
        "warranty_period": "1年",
        "performance_bond": "不收取"
    }},
    "evaluation_criteria": {{"技术部分": 60, "商务部分": 20, "价格部分": 20}},
    "special_requirements": "特殊要求说明"
}}"""),
            ("user", "招标文件内容：\n\n{tender_text}\n\n请提取上述信息并返回JSON格式的结果。")
        ])

    def extract_text_from_pdf(self, pdf_path: str | Path) -> str:
        """从PDF文件中提取文本"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = pypdf.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"

                logger.info(f"成功从PDF提取文本，共 {len(text)} 字符")
                return text
        except Exception as e:
            logger.error(f"PDF文本提取失败: {str(e)}")
            raise ValueError(f"无法读取PDF文件: {str(e)}")

    def extract_text_from_docx(self, docx_path: str | Path) -> str:
        """从Word文档(.docx)中提取文本"""
        if not _DOCX_AVAILABLE:
            raise ValueError("python-docx 未安装，无法读取Word文件")
        try:
            doc = _DocxDocument(str(docx_path))
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            # 同时提取表格内容
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        paragraphs.append(row_text)
            text = "\n".join(paragraphs)
            logger.info(f"成功从Word文档提取文本，共 {len(text)} 字符")
            return text
        except Exception as e:
            logger.error(f"Word文档文本提取失败: {str(e)}")
            raise ValueError(f"无法读取Word文件: {str(e)}")

    def extract_text(self, file_path: str | Path) -> str:
        """根据文件扩展名自动选择提取方式（支持 .pdf 和 .docx）"""
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self.extract_text_from_pdf(file_path)
        elif suffix in (".docx", ".doc"):
            return self.extract_text_from_docx(file_path)
        else:
            raise ValueError(f"不支持的文件格式：{suffix}，仅支持 .pdf 和 .docx")

    def parse_tender_document(self, pdf_path: str | Path) -> TenderDocument:
        """
        解析招标文件（支持PDF和DOCX）

        Args:
            pdf_path: 文件路径（.pdf 或 .docx）

        Returns:
            结构化的招标文件数据
        """
        # 1. 提取文本（自动识别格式）
        tender_text = self.extract_text(pdf_path)

        # 限制 token 长度
        if len(tender_text) > 50000:
            logger.warning(f"招标文件过长 ({len(tender_text)} 字符)，将截取前50000字符")
            tender_text = tender_text[:50000]

        # 2. 使用LLM提取结构化信息
        chain = self.extraction_prompt | self.llm

        try:
            response = chain.invoke({"tender_text": tender_text})

            # 解析LLM返回的JSON
            # 去除可能的markdown代码块标记
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            parsed_data = json.loads(content)

            # 3. 使用Pydantic模型验证和结构化
            tender_doc = TenderDocument(**parsed_data)

            logger.info(f"成功解析招标文件: {tender_doc.project_name}")
            return tender_doc

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {str(e)}\nLLM返回内容: {response.content}")
            raise ValueError(f"LLM返回的数据格式不正确: {str(e)}")
        except Exception as e:
            logger.error(f"招标文件解析失败: {str(e)}")
            raise

    def parse_tender_text(self, tender_text: str) -> TenderDocument:
        """
        直接从文本解析招标信息（用于测试或已经提取好的文本）

        Args:
            tender_text: 招标文件文本内容

        Returns:
            结构化的招标文件数据
        """
        if len(tender_text) > 50000:
            logger.warning(f"招标文件过长 ({len(tender_text)} 字符)，将截取前50000字符")
            tender_text = tender_text[:50000]

        chain = self.extraction_prompt | self.llm

        try:
            response = chain.invoke({"tender_text": tender_text})

            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            parsed_data = json.loads(content)
            tender_doc = TenderDocument(**parsed_data)

            logger.info(f"成功解析招标文本: {tender_doc.project_name}")
            return tender_doc

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {str(e)}")
            raise ValueError(f"LLM返回的数据格式不正确: {str(e)}")
        except Exception as e:
            logger.error(f"招标文本解析失败: {str(e)}")
            raise

    def extract_technical_requirements(self, tender_text: str, package_id: str) -> dict[str, Any]:
        """
        针对特定采购包提取详细技术要求

        Args:
            tender_text: 招标文件文本
            package_id: 采购包编号

        Returns:
            技术要求字典
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是技术参数提取专家。请从招标文件中提取指定采购包的所有技术参数和要求。

输出JSON格式，例如：
{{
    "产地": "进口",
    "方法": "流式细胞术",
    "激光器": "3个独立激光器",
    "荧光通道": "≥11个",
    "其他参数": "..."
}}

如果没有找到相关信息，返回空对象 {{}}"""),
            ("user", f"请从以下招标文件中提取采购包{package_id}的技术参数：\n\n{{tender_text}}")
        ])

        chain = prompt | self.llm
        response = chain.invoke({"tender_text": tender_text})

        try:
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            return json.loads(content)
        except:
            logger.warning(f"无法解析采购包{package_id}的技术要求")
            return {}


def create_tender_parser(llm: ChatOpenAI | None = None) -> TenderParser:
    """
    工厂函数：创建招标文件解析器实例

    Args:
        llm: 可选的LLM实例

    Returns:
        TenderParser实例
    """
    return TenderParser(llm)

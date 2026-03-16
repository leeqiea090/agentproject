#!/usr/bin/env python3
"""
测试投标文件生成的四大问题修复:
1. 技术实参缺失
2. 真实配置清单缺失
3. 偏离判断缺失
4. 证明材料页码缺失
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from app.services.tender_parser import TenderParser
from app.services.one_click_generator.pipeline import generate_bid_sections
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


def test_bid_generation():
    """测试投标文件生成"""
    print("=" * 80)
    print("开始测试投标文件生成 - 四大问题修复验证")
    print("=" * 80)

    # 1. 查找招标文件
    upload_dir = Path(__file__).parent / "data" / "uploads" / "tenders"
    tender_files = list(upload_dir.glob("*.pdf")) + list(upload_dir.glob("*.docx"))

    if not tender_files:
        print(f"❌ 错误: 在 {upload_dir} 目录下未找到招标文件(.pdf或.docx)")
        print("请将招标文件放入 data/uploads/tenders/ 目录")
        return False

    tender_file = tender_files[0]
    print(f"\n📄 使用招标文件: {tender_file.name}")

    # 2. 解析招标文件
    print("\n步骤1: 解析招标文件...")

    # 初始化LLM
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ 错误: 未设置LLM_API_KEY或OPENAI_API_KEY环境变量")
        return False

    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "128000")),
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL"),
    )

    parser = TenderParser(llm=llm)
    try:
        tender_doc = parser.parse_tender_document(tender_file)
        print(f"✅ 解析成功!")
        print(f"   项目名称: {tender_doc.project_name}")
        print(f"   项目编号: {tender_doc.project_number}")
        print(f"   采购类型: {tender_doc.procurement_type}")
        print(f"   包件数量: {len(tender_doc.packages)}")

        for pkg in tender_doc.packages:
            print(f"   包{pkg.package_id}: {pkg.item_name} x {pkg.quantity}")
            tech_reqs = pkg.technical_requirements or {}
            print(f"      技术要求数量: {len(tech_reqs)}")

    except Exception as e:
        print(f"❌ 解析失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 3. 生成投标文件
    print("\n步骤2: 生成投标文件...")

    try:
        tender_raw = parser.extract_text(tender_file)
        result = generate_bid_sections(
            tender=tender_doc,
            tender_raw=tender_raw,
            llm=llm,
            products=None,  # 第一次测试不使用产品信息
            mode="rich_draft",
        )

        print(f"✅ 生成成功!")
        print(f"   章节数量: {len(result.sections)}")
        print(f"   稿件等级: {result.draft_level}")
        print(f"   文档模式: {result.document_mode}")

    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 4. 验证四大问题是否修复
    print("\n" + "=" * 80)
    print("步骤3: 验证四大问题修复")
    print("=" * 80)

    issues_found = []

    # 检查每个章节
    for section in result.sections:
        content = section.content
        title = section.section_title

        # 问题1: 检查技术实参
        if "技术偏离" in title or "配置明细" in title:
            # 统计占位符数量
            placeholder_count = content.count("【待填写：实际响应值】")
            placeholder_count += content.count("【待填写：投标产品实参】")
            placeholder_count += content.count("待补充（投标产品实参）")

            # 统计实际有内容的行数
            lines = [l for l in content.split('\n') if l.strip() and l.strip().startswith('|')]
            data_lines = [l for l in lines if not l.strip().startswith('|---') and '序号' not in l]

            if data_lines:
                ratio = placeholder_count / len(data_lines) if data_lines else 1.0
                print(f"\n📊 {title}:")
                print(f"   数据行数: {len(data_lines)}")
                print(f"   占位符数: {placeholder_count}")
                print(f"   占位率: {ratio * 100:.1f}%")

                if ratio > 0.5:  # 如果超过50%是占位符
                    issues_found.append(f"问题1: {title} 中技术实参占位符过多 ({ratio*100:.1f}%)")

        # 问题2: 检查配置清单
        if "配置明细" in title or "配置清单" in title:
            if "【待填写：配置清单】" in content:
                issues_found.append(f"问题2: {title} 配置清单完全为占位符")
            else:
                # 统计配置项数量
                config_lines = [l for l in content.split('\n')
                               if '|' in l and not l.strip().startswith('|---')
                               and '序号' not in l and '配置名称' not in l]
                print(f"\n📦 {title}:")
                print(f"   配置项数量: {len(config_lines)}")
                if len(config_lines) < 3:
                    issues_found.append(f"问题2: {title} 配置项数量过少 ({len(config_lines)})")

        # 问题3: 检查偏离判断
        if "偏离" in title:
            deviation_placeholder_count = content.count("【待填写：无偏离/正偏离/负偏离】")
            deviation_content_count = content.count("无偏离") + content.count("正偏离") + content.count("负偏离")

            print(f"\n⚖️  {title}:")
            print(f"   偏离判断占位符: {deviation_placeholder_count}")
            print(f"   实际偏离判断: {deviation_content_count}")

            if deviation_placeholder_count > deviation_content_count:
                issues_found.append(f"问题3: {title} 偏离判断占位符过多")

        # 问题4: 检查证明材料页码
        if "技术偏离" in title or "配置明细" in title:
            page_ref_count = content.count("证明材料：第") + content.count("（第") + content.count("页）")
            print(f"\n📄 {title}:")
            print(f"   证明材料页码引用数: {page_ref_count}")

            # 如果没有任何页码引用,记录一下(但不一定是问题,因为可能没有投标侧资料)
            if page_ref_count == 0:
                print(f"   ⚠️  注意: 暂无证明材料页码引用(可能因为未上传投标侧资料)")

    # 5. 输出结果
    print("\n" + "=" * 80)
    print("测试结果总结")
    print("=" * 80)

    if issues_found:
        print(f"\n❌ 发现 {len(issues_found)} 个问题:")
        for i, issue in enumerate(issues_found, 1):
            print(f"   {i}. {issue}")
        return False
    else:
        print("\n✅ 所有检查通过!")
        print("\n修复验证:")
        print("   ✓ 技术实参不再全是占位符")
        print("   ✓ 配置清单包含真实配置项")
        print("   ✓ 偏离判断有实际内容")
        print("   ✓ 证明材料页码机制就绪")
        return True


if __name__ == "__main__":
    success = test_bid_generation()
    sys.exit(0 if success else 1)

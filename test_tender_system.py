"""招投标系统测试示例

这个脚本演示如何使用招投标系统的API
"""
import requests
import json
from pathlib import Path

# API基础URL
BASE_URL = "http://localhost:8000"

def test_tender_workflow():
    """测试完整的招投标工作流"""

    print("=" * 80)
    print("招投标系统测试示例")
    print("=" * 80)

    # ==================== 步骤1：上传招标文件 ====================
    print("\n【步骤1】上传招标文件...")

    tender_pdf_path = r"C:\Users\lq\Desktop\新建文件夹\招标文件-1.pdf"

    with open(tender_pdf_path, "rb") as f:
        files = {"file": (Path(tender_pdf_path).name, f, "application/pdf")}
        response = requests.post(f"{BASE_URL}/api/tender/upload", files=files)

    if response.status_code == 200:
        upload_result = response.json()
        tender_id = upload_result["tender_id"]
        print(f"✓ 上传成功！Tender ID: {tender_id}")
        print(f"  上传时间: {upload_result['upload_time']}")
    else:
        print(f"✗ 上传失败: {response.text}")
        return

    # ==================== 步骤2：解析招标文件 ====================
    print("\n【步骤2】解析招标文件...")

    response = requests.post(f"{BASE_URL}/api/tender/parse/{tender_id}")

    if response.status_code == 200:
        parse_result = response.json()
        parsed_data = parse_result["parsed_data"]
        print(f"✓ 解析成功！")
        print(f"  项目名称: {parsed_data['project_name']}")
        print(f"  项目编号: {parsed_data['project_number']}")
        print(f"  总预算: {parsed_data['budget']} 元")
        print(f"  采购人: {parsed_data['purchaser']}")
        print(f"  采购包数量: {len(parsed_data['packages'])}")

        for pkg in parsed_data['packages']:
            print(f"    - 包{pkg['package_id']}: {pkg['item_name']}, 预算 {pkg['budget']} 元")
    else:
        print(f"✗ 解析失败: {response.text}")
        return

    # ==================== 步骤3：创建企业信息 ====================
    print("\n【步骤3】创建企业信息...")

    company_data = {
        "name": "黑龙江鑫圣瑞医学科技有限公司",
        "legal_representative": "国云贺",
        "address": "黑龙江省哈尔滨市南岗区红旗大街242号福思特大厦23层06号房",
        "phone": "19969688899",
        "licenses": [
            {
                "license_type": "营业执照",
                "license_number": "91230103MA1XXXXXX",
                "valid_until": "长期"
            },
            {
                "license_type": "医疗器械经营许可证",
                "license_number": "黑哈食药监械经营许20XXXXXX",
                "valid_until": "2026-12-31"
            }
        ],
        "staff": [
            {
                "name": "国云贺",
                "position": "项目负责人",
                "education": "专科",
                "phone": "19969688899"
            }
        ]
    }

    response = requests.post(f"{BASE_URL}/api/tender/company/profile", json=company_data)

    if response.status_code == 200:
        company_result = response.json()
        company_id = company_result["company_id"]
        print(f"✓ 企业信息创建成功！Company ID: {company_id}")
        print(f"  企业名称: {company_result['name']}")
    else:
        print(f"✗ 企业信息创建失败: {response.text}")
        return

    # ==================== 步骤4：添加产品信息 ====================
    print("\n【步骤4】添加产品信息...")

    product_data = {
        "product_name": "FACSLyric流式细胞分析仪",
        "manufacturer": "碧迪医疗器械（上海）有限公司",
        "origin": "美国",
        "model": "FACSLyric",
        "specifications": {
            "方法": "流式细胞术",
            "激光器": "3个独立激光器(405nm, 488nm, 638nm)",
            "荧光通道": "≥11个",
            "样本体积": "5-500μL",
            "分析速度": "≤10,000个事件/秒"
        },
        "price": 1960000.00,
        "certifications": ["医疗器械注册证", "CE认证", "FDA认证"],
        "registration_number": "国械注进20163221645"
    }

    response = requests.post(f"{BASE_URL}/api/tender/products", json=product_data)

    if response.status_code == 200:
        product_result = response.json()
        product_id = product_result["product_id"]
        print(f"✓ 产品添加成功！Product ID: {product_id}")
        print(f"  产品名称: {product_result['product_name']}")
        print(f"  参考价格: {product_result['price']} 元")
    else:
        print(f"✗ 产品添加失败: {response.text}")
        return

    # ==================== 步骤5：生成投标文件 ====================
    print("\n【步骤5】生成投标文件...")
    print("  (这可能需要几分钟时间，LLM正在生成各章节内容...)")

    bid_request = {
        "tender_id": tender_id,
        "company_profile_id": company_id,
        "selected_packages": ["6"],  # 选择包6（进口流式细胞分析仪）
        "product_ids": {
            "6": product_id
        },
        "discount_rate": 0.95,  # 95折
        "add_performance_cases": True,
        "custom_service_plan": "提供7x24小时技术支持，2小时内响应，24小时内现场服务"
    }

    response = requests.post(f"{BASE_URL}/api/tender/bid/generate", json=bid_request)

    if response.status_code == 200:
        bid_result = response.json()
        bid_id = bid_result["bid_id"]
        print(f"✓ 投标文件生成成功！Bid ID: {bid_id}")
        print(f"  状态: {bid_result['status']}")
        print(f"  章节数量: {len(bid_result['sections'])}")

        print("\n  章节列表:")
        for section in bid_result['sections']:
            print(f"    - {section['section_title']}")

        print(f"\n  下载地址: {BASE_URL}{bid_result['download_url']}?format=markdown")
    else:
        print(f"✗ 投标文件生成失败: {response.text}")
        return

    # ==================== 步骤6：下载投标文件 ====================
    print("\n【步骤6】下载投标文件...")

    response = requests.get(f"{BASE_URL}/api/tender/bid/download/{bid_id}?format=markdown")

    if response.status_code == 200:
        output_file = f"投标文件_{bid_id}.md"
        with open(output_file, "wb") as f:
            f.write(response.content)
        print(f"✓ 投标文件已下载: {output_file}")
    else:
        print(f"✗ 下载失败: {response.text}")

    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)


def test_product_list():
    """测试获取产品列表"""
    print("\n获取产品列表...")
    response = requests.get(f"{BASE_URL}/api/tender/products")

    if response.status_code == 200:
        products = response.json()
        print(f"共 {len(products)} 个产品:")
        for product in products:
            print(f"  - {product['product_name']} ({product['manufacturer']}): {product['price']} 元")
    else:
        print(f"获取失败: {response.text}")


def test_api_status():
    """测试API状态"""
    print("\n检查API状态...")
    response = requests.get(f"{BASE_URL}/api/status")

    if response.status_code == 200:
        print(f"✓ API运行正常")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    else:
        print(f"✗ API异常: {response.text}")


if __name__ == "__main__":
    import sys

    # 检查服务是否运行
    try:
        test_api_status()
    except requests.exceptions.ConnectionError:
        print("\n✗ 无法连接到服务器，请确保FastAPI服务正在运行！")
        print("  启动命令: python -m app.main")
        sys.exit(1)

    # 运行完整测试
    test_tender_workflow()

    # 查看产品列表
    test_product_list()

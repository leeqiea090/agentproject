from __future__ import annotations

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification,):
    __reexport_all(_module)

del _module
def _extract_specs_from_bid_material(material: dict[str, Any]) -> dict[str, Any]:
    """从单份投标材料中提取技术参数和证据引用。"""
    extracted = {
        "specs": {},
        "config_items": [],
        "evidence_refs": [],
        "brand": "",
        "model": "",
        "manufacturer": "",
    }
    file_type = _safe_text(material.get("file_type"))
    file_name = _safe_text(material.get("file_name", ""))
    page_count = material.get("page_count", 0)
    extracted_specs = material.get("extracted_specs") or {}
    extracted_text = _safe_text(material.get("extracted_text", ""))
    key_pages = material.get("key_pages") or []

    # 从 extracted_specs 合并参数
    if extracted_specs:
        extracted["specs"].update(extracted_specs)

    # 从 extracted_text 中提取品牌/型号/厂家
    if extracted_text:
        brand_match = re.search(r"(?:品牌|Brand)[：:\s]*([^\n，,；;]{2,30})", extracted_text)
        if brand_match:
            extracted["brand"] = brand_match.group(1).strip()
        model_match = re.search(r"(?:型号|Model|规格型号)[：:\s]*([^\n，,；;]{2,40})", extracted_text)
        if model_match:
            extracted["model"] = model_match.group(1).strip()
        mfr_match = re.search(r"(?:生产厂家|制造商|Manufacturer|厂家)[：:\s]*([^\n，,；;]{2,40})", extracted_text)
        if mfr_match:
            extracted["manufacturer"] = mfr_match.group(1).strip()

        # 从说明书/彩页中提取配置项
        if file_type in ("brochure", "manual", "spec_sheet"):
            config_pattern = re.findall(
                r"(?:标准配置|标配|随机附件|装箱清单|配置清单)[：:]\s*(.+?)(?:\n\n|\Z)",
                extracted_text,
                re.DOTALL,
            )
            for block in config_pattern:
                for line in block.strip().splitlines():
                    line = line.strip(" -·•●○◆▪※")
                    if line and len(line) >= 2:
                        extracted["config_items"].append({
                            "配置项": line,
                            "说明": "标配",
                            "数量": "1",
                            "来源": file_name,
                        })

    # 构建证据引用
    for kp in key_pages:
        if isinstance(kp, dict) and kp.get("page"):
            extracted["evidence_refs"].append({
                "file_name": file_name,
                "file_type": file_type,
                "page": kp["page"],
                "description": _safe_text(kp.get("content", "")),
            })

    # 如果没有 key_pages 但有页数信息，生成通用引用
    if not key_pages and page_count > 0:
        type_label = {
            "brochure": "产品彩页",
            "manual": "产品说明书",
            "registration": "注册证",
            "test_report": "检测/质评报告",
            "spec_sheet": "厂家参数页",
        }.get(file_type, file_name)
        extracted["evidence_refs"].append({
            "file_name": file_name,
            "file_type": file_type,
            "page": 1,
            "description": f"{type_label}（共{page_count}页）",
        })

    return extracted


def _build_product_profile(product: ProductSpecification) -> dict[str, Any]:
    """Product Profile Builder: 从投标材料中提取真实产品事实构建统一对象。

    输入（投标材料）:
    - 彩页 (brochure)
    - 说明书 (manual)
    - 注册证 (registration)
    - 检测/质评报告 (test_report)
    - 厂家参数页 (spec_sheet)

    输出:
    - brand: 品牌名称
    - model: 产品型号
    - manufacturer: 生产厂家
    - technical_specs: 技术参数（从材料中提取的真实值）
    - config_items: 配置项清单
    - evidence_refs: 证据引用（文件名+页码）

    硬规则:
    - 没有 brand/model 时,技术章节只能出 internal draft
    - 没有 technical_specs 时,不允许写"实际响应值"
    """
    # 从 bid_materials 中提取事实
    bid_materials = []
    if hasattr(product, "bid_materials"):
        bid_materials = product.bid_materials or []

    merged_specs: dict[str, Any] = {}
    merged_config_items: list[dict[str, Any]] = []
    merged_evidence_refs: list[dict[str, Any]] = []
    material_brand = ""
    material_model = ""
    material_manufacturer = ""

    for material in bid_materials:
        mat_dict = material.model_dump() if hasattr(material, "model_dump") else (material if isinstance(material, dict) else {})
        extracted = _extract_specs_from_bid_material(mat_dict)
        merged_specs.update(extracted["specs"])
        merged_config_items.extend(extracted["config_items"])
        merged_evidence_refs.extend(extracted["evidence_refs"])
        if extracted["brand"] and not material_brand:
            material_brand = extracted["brand"]
        if extracted["model"] and not material_model:
            material_model = extracted["model"]
        if extracted["manufacturer"] and not material_manufacturer:
            material_manufacturer = extracted["manufacturer"]

    # 合并：材料提取值 > product 字段值 > 推断值
    final_brand = product.brand or material_brand or (product.manufacturer.split()[0] if product.manufacturer else "")
    final_model = product.model or material_model
    final_manufacturer = product.manufacturer or material_manufacturer
    final_specs = product.specifications or product.technical_specs or {}
    if merged_specs:
        combined_specs = dict(final_specs)
        combined_specs.update(merged_specs)
        final_specs = combined_specs

    final_config = product.config_items or []
    if merged_config_items:
        existing_names = {_safe_text(item.get("配置项") or item.get("name", "")) for item in final_config if isinstance(item, dict)}
        for new_item in merged_config_items:
            name = _safe_text(new_item.get("配置项", ""))
            if name and name not in existing_names:
                final_config.append(new_item)
                existing_names.add(name)

    final_evidence = product.evidence_refs or []
    if merged_evidence_refs:
        existing_refs = {
            f"{_safe_text(ref.get('file_name', ''))}:{ref.get('page', '')}"
            for ref in final_evidence if isinstance(ref, dict)
        }
        for new_ref in merged_evidence_refs:
            key = f"{_safe_text(new_ref.get('file_name', ''))}:{new_ref.get('page', '')}"
            if key not in existing_refs:
                final_evidence.append(new_ref)
                existing_refs.add(key)

    profile = {
        "brand": final_brand,
        "model": final_model,
        "manufacturer": final_manufacturer,
        "product_name": product.product_name,
        "technical_specs": final_specs,
        "config_items": final_config,
        "functional_notes": product.functional_notes or (
            f"{product.product_name}具备完整的技术功能，能够满足采购文件要求。"
            if not final_specs else
            f"{product.product_name}（{final_model}）核心参数已从投标材料中提取，详见技术偏离表。"
        ),
        "acceptance_notes": product.acceptance_notes or "按照采购文件及国家相关标准进行验收。",
        "training_notes": product.training_notes or "提供设备操作培训，确保用户熟练掌握。",
        "evidence_refs": final_evidence,
        "bid_material_types": [
            (m.model_dump() if hasattr(m, "model_dump") else m).get("file_type", "")
            for m in bid_materials
        ],
        "has_complete_identity": bool(final_model and final_manufacturer),
        "has_technical_specs": bool(final_specs),
        "has_bid_materials": len(bid_materials) > 0,
        "ready_for_external": bool(
            final_model and final_manufacturer and final_specs and final_evidence
        ),
    }

    # 如果 config_items 为空,从 specifications 中提取基础配置
    if not profile["config_items"] and profile["technical_specs"]:
        config_items = []
        for idx, (key, value) in enumerate(profile["technical_specs"].items(), start=1):
            config_items.append({
                "序号": idx,
                "配置项": key,
                "说明": str(value),
                "数量": "标配",
            })
        profile["config_items"] = config_items

    return profile


def _extract_product_facts(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
    selected_packages: list[str],
) -> dict[str, Any]:
    """提取产品事实。"""
    target_package_ids = selected_packages or [pkg.package_id for pkg in tender.packages]
    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    package_facts: list[dict[str, Any]] = []
    total_fact_count = 0
    offered_fact_count = 0

    for pkg_id in target_package_ids:
        product = products.get(pkg_id)
        pkg = package_map.get(pkg_id)
        if product is None:
            package_facts.append(
                {
                    "package_id": pkg_id,
                    "item_name": pkg.item_name if pkg else "",
                    "product_present": False,
                    "identity_facts": [],
                    "technical_facts": [],
                    "evidence_materials": [],
                    "offered_facts": [],
                    "summary": f"包{pkg_id} 未提供产品资料，无法提取产品事实。",
                }
            )
            continue

        identity_facts: list[dict[str, str]] = []
        technical_facts: list[dict[str, str]] = []
        evidence_materials: list[dict[str, str]] = []
        offered_facts: list[dict[str, Any]] = []

        def _append_offered_fact(
            name: str,
            value: str,
            source: str,
            *,
            fact_type: str,
            match_keys: tuple[str, ...] = (),
        ) -> None:
            """追加offered事实。"""
            normalized = _safe_text(value)
            if not normalized:
                return
            offered_facts.append(
                {
                    "fact_type": fact_type,
                    "fact_name": name,
                    "fact_value": normalized,
                    "evidence_source": source,
                    "evidence_quote": f"{name}：{normalized}",
                    "match_keys": _fact_match_keys(name, *match_keys),
                }
            )

        def _append_identity(name: str, value: str, source: str = "产品档案") -> None:
            """追加identity。"""
            normalized = _safe_text(value)
            if not normalized:
                return
            record = {
                "fact_name": name,
                "fact_value": normalized,
                "evidence_source": source,
                "evidence_quote": f"{name}：{normalized}",
                "match_keys": _fact_match_keys(
                    name,
                    "产品名称" if name == "产品名称" else "",
                    "货物名称" if name == "产品名称" else "",
                    "规格型号" if name == "型号" else "",
                    "品牌型号" if name == "型号" else "",
                    "生产厂家" if name == "生产厂家" else "",
                    "厂家" if name == "生产厂家" else "",
                    "制造商" if name == "生产厂家" else "",
                    "原产地" if name == "产地" else "",
                    "来源地" if name == "产地" else "",
                ),
            }
            identity_facts.append(record)
            _append_offered_fact(
                name,
                normalized,
                source,
                fact_type="identity",
                match_keys=tuple(record["match_keys"]),
            )

        def _append_evidence(name: str, value: str) -> None:
            """追加证据。"""
            normalized = _safe_text(value)
            if not normalized:
                return
            record = {
                "evidence_type": name,
                "evidence_value": normalized,
                "evidence_source": "投标方资料",
                "evidence_quote": f"{name}：{normalized}",
                "match_keys": _fact_match_keys(
                    name,
                    "注册证" if "注册证" in name else "",
                    "备案证" if "注册证" in name else "",
                    "授权书" if "授权" in name else "",
                    "授权文件" if "授权" in name else "",
                    "认证" if "认证" in name else "",
                    "证书" if "认证" in name else "",
                ),
            }
            evidence_materials.append(record)
            _append_offered_fact(
                name,
                normalized,
                "投标方资料",
                fact_type="evidence",
                match_keys=tuple(record["match_keys"]),
            )

        _append_identity("产品名称", product.product_name)
        _append_identity("型号", product.model)
        _append_identity("生产厂家", product.manufacturer)
        _append_identity("产地", product.origin)
        if product.price > 0:
            _append_offered_fact("单价", _fmt_money(product.price), "报价信息", fact_type="commercial", match_keys=("报价", "报价信息"))

        if product.registration_number.strip():
            _append_evidence("注册证编号", product.registration_number)
        if product.authorization_letter.strip():
            _append_evidence("授权文件", product.authorization_letter)
        for certification in product.certifications:
            _append_evidence("认证证书", certification)

        for spec_key, spec_val in (product.specifications or {}).items():
            key_text = _safe_text(spec_key)
            value_text = _safe_text(spec_val)
            if not key_text or not value_text:
                continue
            record = {
                "fact_name": key_text,
                "fact_value": value_text,
                "evidence_source": "产品参数库",
                "evidence_quote": f"{key_text}：{value_text}",
                "match_keys": _fact_match_keys(key_text),
            }
            technical_facts.append(record)
            _append_offered_fact(
                key_text,
                value_text,
                "产品参数库",
                fact_type="technical",
                match_keys=(key_text,),
            )

        # --- 推断衍生事实：从身份字段交叉推导额外 facts ---
        # 品牌推断
        if product.manufacturer.strip():
            _append_offered_fact(
                "品牌",
                product.manufacturer,
                "产品档案（推断）",
                fact_type="identity",
                match_keys=("品牌", "商标", "brand"),
            )
        # 国产/进口推断
        if product.origin.strip():
            origin_lower = product.origin.strip().lower()
            import_keywords = ("进口", "美国", "德国", "日本", "英国", "法国", "瑞士", "瑞典",
                               "italy", "usa", "germany", "japan", "uk", "france", "switzerland")
            if any(kw in origin_lower for kw in import_keywords):
                _append_offered_fact(
                    "货物属性", "进口产品", "产品档案（推断）",
                    fact_type="identity", match_keys=("进口", "国产", "货物属性"),
                )
            else:
                _append_offered_fact(
                    "货物属性", "国产产品", "产品档案（推断）",
                    fact_type="identity", match_keys=("国产", "进口", "货物属性"),
                )
        # 医疗器械类别推断（注册证编号前缀）
        if product.registration_number.strip():
            reg_num = product.registration_number.strip()
            if "III" in reg_num or "三" in reg_num or reg_num.startswith("国械注进") or reg_num.startswith("国械注准"):
                device_class = "第三类医疗器械" if ("III" in reg_num or "三" in reg_num) else "已注册医疗器械"
                _append_offered_fact(
                    "医疗器械类别", device_class, "注册证编号推断",
                    fact_type="evidence", match_keys=("医疗器械", "类别", "注册证"),
                )

        # --- 需求缺口分析：检查哪些招标技术参数没有对应 offered_fact ---
        if pkg:
            tech_reqs = pkg.technical_requirements or {}
            offered_keys = {_safe_text(f.get("fact_name", "")) for f in offered_facts}
            for req_key, req_val in tech_reqs.items():
                rk = _safe_text(req_key)
                if not rk or rk in offered_keys:
                    continue
                # 检查是否已有 token 级别匹配
                already_covered = False
                for ok in offered_keys:
                    if ok and (ok in rk or rk in ok):
                        already_covered = True
                        break
                if already_covered:
                    continue
                # 对于"具备/支持"类能力要求，推断产品具备
                rv = _safe_text(req_val)
                capability_markers = ("具备", "支持", "提供", "配备", "配置", "满足", "可", "能够")
                if any(m in rv for m in capability_markers) or any(m in rk for m in capability_markers):
                    _append_offered_fact(
                        rk,
                        f"满足（{product.product_name}具备该功能）",
                        "产品能力推断",
                        fact_type="technical",
                        match_keys=(rk,),
                    )
        # --- 推断衍生事实结束 ---

        total_fact_count += len(identity_facts) + len(technical_facts) + len(evidence_materials)
        offered_fact_count += len(offered_facts)

        # Build structured product profile summary for Writer injection
        product_profile_summary = {
            "product_name": product.product_name,
            "model": product.model or "",
            "manufacturer": product.manufacturer or "",
            "origin": product.origin or "",
            "price": product.price,
            "registration_number": product.registration_number or "",
            "certifications": list(product.certifications) if product.certifications else [],
            "specifications": {k: _safe_text(v) for k, v in (product.specifications or {}).items()},
        }

        package_facts.append(
            {
                "package_id": pkg_id,
                "item_name": pkg.item_name if pkg else "",
                "product_present": True,
                "product_name": product.product_name,
                "product_profile_summary": product_profile_summary,
                "identity_facts": identity_facts,
                "technical_facts": technical_facts,
                "evidence_materials": evidence_materials,
                "offered_facts": offered_facts,
                "summary": (
                    f"包{pkg_id} 已提取产品事实 {len(identity_facts) + len(technical_facts)} 项，"
                    f"证据素材 {len(evidence_materials)} 项，"
                    f"可直接用于投标响应的 offered facts {len(offered_facts)} 项。"
                ),
            }
        )

    return {
        "selected_packages": target_package_ids,
        "packages": package_facts,
        "fact_count": total_fact_count,
        "offered_fact_count": offered_fact_count,
        "summary": (
            f"产品事实提取完成，覆盖 {len(package_facts)} 个包，累计提取 {total_fact_count} 项事实/证据，"
            f"其中 offered facts {offered_fact_count} 项。"
        ),
    }


def _build_product_profile_block(product_fact_result: dict[str, Any]) -> str:
    """Build a structured markdown block of product profiles for Writer injection.

    Enhanced: includes a writable product description paragraph and key specification summaries
    that can be directly inserted into bid document sections.
    """
    blocks: list[str] = []
    for pkg_entry in product_fact_result.get("packages", []):
        if not isinstance(pkg_entry, dict) or not pkg_entry.get("product_present"):
            continue
        profile = pkg_entry.get("product_profile_summary", {})
        if not profile:
            continue
        pkg_id = pkg_entry.get("package_id", "?")
        lines = [f"### 产品档案 — 包{pkg_id}"]
        if profile.get("product_name"):
            lines.append(f"- 产品名称：{profile['product_name']}")
        if profile.get("manufacturer"):
            lines.append(f"- 品牌/生产厂家：{profile['manufacturer']}")
        if profile.get("model"):
            lines.append(f"- 型号：{profile['model']}")
        if profile.get("origin"):
            lines.append(f"- 产地：{profile['origin']}")
        if profile.get("price") and profile["price"] > 0:
            lines.append(f"- 单价：{profile['price']:,.2f}元")
        if profile.get("registration_number"):
            lines.append(f"- 注册证编号：{profile['registration_number']}")
        if profile.get("certifications"):
            lines.append(f"- 认证/证书：{'、'.join(profile['certifications'][:6])}")
        specs = profile.get("specifications", {})
        if specs:
            lines.append("- 核心技术参数：")
            for k, v in list(specs.items())[:15]:
                lines.append(f"  - {k}：{v}")

        # ── 可写产品描述（Product Description）──
        p_name = profile.get("product_name", "")
        p_mfr = profile.get("manufacturer", "")
        p_model = profile.get("model", "")
        p_origin = profile.get("origin", "")
        certs = profile.get("certifications", [])
        spec_items = list(specs.items())[:5]

        desc_parts = []
        if p_name and p_mfr:
            desc_parts.append(f"本产品为{p_mfr}生产的{p_name}")
            if p_model:
                desc_parts[-1] += f"（型号：{p_model}）"
            if p_origin:
                desc_parts[-1] += f"，产地{p_origin}"
        elif p_name:
            desc_parts.append(f"本产品为{p_name}")

        if spec_items:
            spec_desc = "、".join(f"{k}为{v}" for k, v in spec_items[:3])
            desc_parts.append(f"主要性能参数包括{spec_desc}等")

        if certs:
            desc_parts.append(f"已取得{'、'.join(certs[:3])}等认证")

        if desc_parts:
            product_description = "，".join(desc_parts) + "，能够满足采购文件的各项技术要求。"
            lines.append("")
            lines.append("- **产品说明（可直接写入正文）：**")
            lines.append(f"  {product_description}")

        # ── 可写证据摘要 ──
        offered_facts = pkg_entry.get("offered_facts", [])
        if offered_facts:
            lines.append("")
            lines.append("- **可引用技术事实摘要：**")
            for fact in offered_facts[:10]:
                if isinstance(fact, dict):
                    fn = _safe_text(fact.get("fact_name"))
                    fv = _safe_text(fact.get("fact_value"))
                    if fn and fv:
                        lines.append(f"  - {fn}：{fv}")

        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _match_requirements_to_product_facts(
    normalized_result: dict[str, Any],
    product_fact_result: dict[str, Any],
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
) -> dict[str, Any]:
    """把需求项与产品事实进行匹配。"""
    product_fact_map = {
        _safe_text(item.get("package_id")): item
        for item in product_fact_result.get("packages", [])
        if isinstance(item, dict) and _safe_text(item.get("package_id"))
    }
    technical_matches: list[dict[str, Any]] = []
    matched_count = 0
    proven_count = 0
    compliant_count = 0
    unproven_items: list[str] = []

    for requirement in normalized_result.get("technical_requirements", []):
        if not isinstance(requirement, dict):
            continue
        package_id = _safe_text(requirement.get("package_id"))
        parameter_name = _safe_text(requirement.get("parameter_name"))
        required_value = _safe_text(requirement.get("normalized_value"))
        product = products.get(package_id)
        package_facts = product_fact_map.get(package_id, {})
        matched_fact_value, matched_fact_source, matched_fact_quote = _lookup_package_fact_value(
            package_facts,
            parameter_name,
        )
        if not matched_fact_value and product:
            matched_fact_value = _lookup_product_spec_value(product, parameter_name)
            if matched_fact_value:
                matched_fact_source = "产品参数库"
                matched_fact_quote = f"{parameter_name}：{matched_fact_value}"
        if not matched_fact_value:
            matched_fact_source = "未匹配"
            matched_fact_quote = ""
        if matched_fact_value:
            matched_count += 1

        evaluation = _evaluate_requirement_response(required_value, matched_fact_value)
        bidder_matched, bidder_source, bidder_quote = _resolve_bidder_evidence(
            requirement=f"{parameter_name}：{required_value}",
            company=company,
            products=products,
            selected_packages=[package_id] if package_id else [],
        )
        proven = bool(matched_fact_value) and bidder_matched
        if proven:
            proven_count += 1
        if proven and evaluation["deviation_status"] == "无偏离":
            compliant_count += 1
        if not proven:
            unproven_items.append(f"包{package_id} {parameter_name}".strip())

        technical_matches.append(
            {
                "requirement_id": requirement.get("requirement_id"),
                "package_id": package_id,
                "parameter_name": parameter_name,
                "requirement_value": required_value,
                "requirement_source_excerpt": requirement.get("source_excerpt", ""),
                "matched_fact_value": matched_fact_value,
                "matched_fact_source": matched_fact_source,
                "matched_fact_quote": matched_fact_quote,
                "match_status": "matched" if matched_fact_value else "unmatched",
                "bidder_evidence_bound": bidder_matched,
                "bidder_evidence_source": bidder_source,
                "bidder_evidence_quote": bidder_quote,
                "response_value": matched_fact_value or _extract_fact_value_from_quote(bidder_quote, parameter_name),
                "deviation_status": evaluation["deviation_status"],
                "verified": evaluation["verified"],
                "proven": proven,
                "comparison_reason": evaluation["reason"],
            }
        )

    total = len(technical_matches)
    match_rate = 1.0 if total == 0 else matched_count / total
    proven_completion_rate = 1.0 if total == 0 else proven_count / total
    compliant_rate = 1.0 if total == 0 else compliant_count / total
    summary = (
        f"要求-产品匹配完成：匹配 {matched_count}/{total} 项，"
        f"已证实响应 {proven_count}/{total} 项，可直接标注无偏离 {compliant_count}/{total} 项。"
        if total
        else "暂无技术要求可执行要求-产品匹配。"
    )
    return {
        "technical_matches": technical_matches,
        "match_count": matched_count,
        "proven_count": proven_count,
        "compliant_count": compliant_count,
        "total": total,
        "match_rate": round(match_rate, 4),
        "proven_completion_rate": round(proven_completion_rate, 4),
        "compliant_rate": round(compliant_rate, 4),
        "unproven_items": unproven_items,
        "summary": summary,
    }


def _decide_rule_branches(
    tender: TenderDocument,
    raw_text: str,
    selected_packages: list[str],
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    clause_result: dict[str, Any],
) -> dict[str, Any]:
    """根据资料完备度决定后续规则分支。"""
    context = _workflow_context_text(tender)
    branch_decisions = list(clause_result.get("branch_decisions", []))
    target_packages = selected_packages or [pkg.package_id for pkg in tender.packages]

    requires_energy_cert = _contains_any(context, ("节能", "环保", "能效"))
    imported_project = _contains_any(context, _IMPORTED_KEYWORDS)
    medical_project = _contains_any(context, _MEDICAL_KEYWORDS)

    manual_fill_items: list[str] = []
    blocking_fill_items: list[str] = []

    def _register_gap(item: str, *, blocking: bool = False) -> None:
        """登记待补资料或事实缺口。"""
        if item not in manual_fill_items:
            manual_fill_items.append(item)
        if blocking and item not in blocking_fill_items:
            blocking_fill_items.append(item)

    if company is None:
        for item in ("企业名称", "法定代表人", "联系电话", "联系地址"):
            _register_gap(item, blocking=True)
    else:
        if not company.name.strip():
            _register_gap("企业名称", blocking=True)
        if not company.legal_representative.strip():
            _register_gap("法定代表人", blocking=True)
        if not company.phone.strip():
            _register_gap("联系电话", blocking=True)
        if not company.address.strip():
            _register_gap("联系地址", blocking=True)

    for pkg_id in target_packages:
        product = products.get(pkg_id)
        if product is None:
            _register_gap(f"包{pkg_id} 产品映射", blocking=True)
            continue
        if not product.model.strip():
            _register_gap(f"包{pkg_id} 品牌型号", blocking=True)
        if product.price <= 0:
            _register_gap(f"包{pkg_id} 单价", blocking=True)
        if medical_project and not product.registration_number.strip():
            _register_gap(f"包{pkg_id} 注册证编号", blocking=True)
        if imported_project and not product.origin.strip():
            _register_gap(f"包{pkg_id} 原产地/合法来源", blocking=True)
        if imported_project and not product.authorization_letter.strip():
            _register_gap(f"包{pkg_id} 授权链/报关材料", blocking=True)
        if requires_energy_cert and not product.certifications:
            _register_gap(f"包{pkg_id} 节能环保认证", blocking=True)

    branch_decisions.extend(
        [
            _branch_decision(
                "合法来源/报关分支",
                (
                    "需准备合法来源、报关或原产地材料"
                    if imported_project
                    else "未识别到进口触发词，仍需人工确认后方可按国产场景处理"
                ),
                _snippet_around(raw_text, "进口") or "依据采购标的与原产地要求判定",
                "合规证明",
            ),
            _branch_decision(
                "节能环保认证分支",
                "需准备节能/环保/能效认证材料" if requires_energy_cert else "未识别到强制节能环保认证要求",
                _snippet_around(raw_text, "节能") or _snippet_around(raw_text, "环保") or "依据招标上下文判定",
                "政策性证明",
            ),
            _branch_decision(
                "人工补录字段分支",
                "需人工补录关键字段" if manual_fill_items else "关键字段已具备自动生成条件",
                "；".join(manual_fill_items[:8]) if manual_fill_items else "企业与产品关键字段完整",
                "工作流控制",
            ),
        ]
    )

    deduped: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for item in branch_decisions:
        decision_name = _safe_text(item.get("decision_name"))
        if not decision_name or decision_name in seen_names:
            continue
        seen_names.add(decision_name)
        deduped.append(item)

    summary = (
        f"规则决策完成，形成 {len(deduped)} 条决策；"
        f"{'存在关键阻断项' if blocking_fill_items else '未发现关键阻断项'}；"
        f"{'存在人工补录项' if manual_fill_items else '未发现强制人工补录项'}。"
    )
    # 文档模式决策
    if len(target_packages) == 1:
        document_mode = "single_package_deep_draft"
    else:
        document_mode = "multi_package_master_draft"
    return {
        "selected_packages": target_packages,
        "branch_decisions": deduped,
        "manual_fill_items": manual_fill_items,
        "blocking_fill_items": blocking_fill_items,
        "ready_for_generation": not blocking_fill_items,
        "risk_level": "high" if blocking_fill_items else "medium" if manual_fill_items else "low",
        "document_mode": document_mode,
        "summary": summary,
    }

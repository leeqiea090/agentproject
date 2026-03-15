"""公开招标格式驱动章节生成。"""
from __future__ import annotations

from typing import Any

from app.services.one_click_generator.config_tables import (
    _classify_config_item,
    _extract_configuration_items,
    _profile_config_items,
)
from app.services.one_click_generator.response_tables import (
    _build_pending_response_guidance,
    _build_requirement_rows,
    _display_bidder_response,
    _has_real_bidder_response,
    _normalize_deviation_status,
    _recommended_evidence_label,
    _split_requirement_text,
)

from .common import *  # noqa: F401,F403

def _title_semantic_key(title: str) -> str:
    """提取标题的语义匹配键。"""
    t = _normalized_title_key(title)
    t = (
        t.replace("“", "")
        .replace("”", "")
        .replace('"', "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("．", ".")
    )
    t = re.sub(r"[()]", "", t)
    return t


def _title_matches_any(title: str, candidates: list[str]) -> bool:
    """判断标题是否命中任一候选标题。"""
    src = _title_semantic_key(title)
    for c in candidates:
        ck = _title_semantic_key(c)
        if ck in src or src in ck:
            return True
    return False
def _normalized_title_key(title: str) -> str:
    """返回标题键。"""
    s = re.sub(r"\s+", "", title or "")
    s = s.replace("（", "(").replace("）", ")").replace("．", ".")
    s = s.replace("（格式）", "").replace("(格式)", "")
    return s


def _dedupe_zb_entries(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """去重ZB 格式entries。"""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for title, raw in entries:
        key = _normalized_title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((title, raw))
    return out


def _looks_like_safe_zb_template_title(title: str) -> bool:
    """判断like安全ZB 格式模板标题。"""
    s = _normalized_title_key(title)
    if not s:
        return False

    if re.match(r"^格式\s*\d+(?:-\d+)?(?:\.\d+)?", s):
        return True

    positive = (
        "投标函",
        "开标一览表",
        "投标分项报价表",
        "投标保证金说明函",
        "授权书",
        "投标人一般情况表",
        "类似项目业绩表",
        "中小企业声明函",
        "残疾人福利性单位声明函",
        "节能环保材料",
        "采购需求响应及偏离表",
        "技术要求响应及偏离表",
        "技术支持资料",
        "其他技术方案",
        "制造商授权书",
        "售后服务承诺书",
        "招标代理服务费承诺",
    )
    if any(word in s for word in positive):
        return True

    if _is_bad_zb_template_title(s):
        return False

    if re.match(r"^(?:7|8|9)\.\d+(?:\.\d+)?", s) and any(
        k in s for k in ("声明函", "业绩表", "授权书", "响应及偏离表", "技术方案", "证明文件", "承诺")
    ):
        return True

    return False


def _strip_leading_compound_index(text: str) -> str:
    """剥离leadingcompound首页。"""
    s = _clean_text(text or "")
    if not s:
        return ""
    return re.sub(r"^(?:\d+(?:\.\d+)?\s*/\s*){1,6}", "", s).strip()


def _extract_invalid_clauses(text: str) -> list[str]:
    """提取无效条款。"""
    s = _strip_leading_compound_index(_clean_text(text or ""))
    if not s:
        return []
    s = re.sub(r"\s+", " ", s)

    patterns = [
        r"未按上述要求[^。；\n]{0,140}(?:投标无效|视为未响应[^。；\n]{0,40}|被拒绝|不予认可)",
        r"未按[^。；\n]{0,140}(?:投标无效|无效投标|被拒绝|不予认可|废标)",
        r"否则[^。；\n]{0,140}(?:投标无效|无效投标|被拒绝|不予认可|废标)",
        r"任何一条不满足[^。；\n]{0,120}(?:废标|无效)",
        r"最低得分[^。；\n]{0,120}(?:无效响应处理|予以拒绝|无效)",
        r"报价明显低于[^。；\n]{0,180}(?:无效投标|予以拒绝|不再进入后续评审)",
        r"以可调整价格[^。；\n]{0,120}(?:无效投标|予以拒绝)",
        r"不符合上述合格投标人资格要求[^。；\n]{0,80}(?:无效投标|拒绝)",
        r"没有根据[^。；\n]{0,140}(?:无效投标|拒绝)",
        r"投标有效期不满足要求[^。；\n]{0,60}(?:无效投标|拒绝)",
        r"提交或参与了一个以上投标[^。；\n]{0,100}(?:无效)",
        r"未提供[^。；\n]{0,120}(?:无效投标|视为无效投标|被视为无效投标)",
        r"其投标无效",
        r"不予认可",
    ]

    out: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, s):
            frag = _clean_text(m.group(0)).strip("；;。 ")
            if not frag:
                continue
            if frag == "其投标无效":
                continue
            if frag == "不予认可":
                continue
            out.append(frag)

    # 技术评分段常见独立否决句补抓
    for sent in re.split(r"[。；\n]+", s):
        sent = _clean_text(sent)
        if not sent:
            continue
        if any(k in sent for k in ("任何一条不满足将导致废标", "最低得分为 0 分时将按照无效响应处理", "最低得分为0分时将按照无效响应处理")):
            out.append(sent)

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in out:
        item = re.sub(r"\s+", " ", item).strip("；;。 ")
        if not item or len(item) < 8:
            continue
        if item in seen:
            continue
        seen.add(item)
        cleaned.append(item[:180] + ("…" if len(item) > 180 else ""))
    return cleaned


def _looks_like_invalid_reason(text: str) -> bool:
    """判断like无效reason。"""
    s = _clean_text(text or "")
    if not s or len(s) < 8:
        return False
    noise = ("评分办法索引", "资格性检查索引", "符合性检查索引", "项目编号", "预算金额", "联系方式", "通讯地址", "设备配置及参数清单")
    if any(x in s for x in noise):
        return False
    return any(k in s for k in ("投标无效", "无效投标", "废标", "被拒绝", "不予认可", "视为未响应", "视为无效", "予以拒绝"))


def _extract_invalid_candidate_sentences(tender_raw: str) -> list[str]:
    """提取无效candidatesentences。"""
    text = tender_raw or ""
    if not text:
        return []

    anchor_patterns = [
        r"2\.1[^\n。；]{0,80}无效投标",
        r"3\.5[^\n。；]{0,40}合格的货物及其有关服务",
        r"10\.1[^\n。；]{0,40}提交或参与了一个以上投标",
        r"11\.5[^\n。；]{0,40}可调整",
        r"15\.3[^\n。；]{0,40}投标保证金",
        r"16\.1[^\n。；]{0,40}投标有效期",
        r"26\.5[^\n。；]{0,40}其他无效投标情况",
        r"技术规格要求的响应程度",
        r"腐败和欺诈行为",
    ]

    blocks: list[str] = []
    for pat in anchor_patterns:
        for m in re.finditer(pat, text):
            blocks.append(text[m.start(): m.start() + 1200])

    out: list[str] = []
    for block in blocks:
        out.extend(_extract_invalid_clauses(block))

    seen: set[str] = set()
    cleaned: list[str] = []
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned



def _is_headerish_review_text(text: str) -> bool:
    """判断headerish评审文本。"""
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return True

    bad_markers = (
        "序号/审查内容/合格条件",
        "序号/内容/评分因素分项/评审标准",
        "序号/无效投标情形/自检结果/备注",
        "序号/审查项/采购文件要求/响应文件对应内容/是否满足/备注",
        "资格性检查",
        "资格性审查",
        "符合性检查",
        "符合性审查",
        "评分办法索引",
        "评审方法前附表",
        "投标文件所在页码",
        "投标文件对应页码",
    )
    if any(x in s for x in bad_markers):
        return True

    if s in {"序号", "审查内容", "合格条件", "评分标准", "评审标准", "内容"}:
        return True

    return False


def _split_compound_fields(text: str) -> list[str]:
    """切分compoundfields。"""
    s = _clean_text(text or "")
    if not s:
        return []

    parts = [p.strip() for p in re.split(r"\s*/\s*|[｜|]", s) if p.strip()]

    # 去掉前置序号，如：1 / 投标人名称 / 与营业执照一致
    while parts and re.fullmatch(r"\d+(?:\.\d+)?", parts[0]):
        parts = parts[1:]

    return parts


def _looks_like_detailed_review_item(text: str) -> bool:
    """判断like详细评审项。"""
    s = _clean_text(text or "")
    if not s:
        return False

    noise = (
        "品名",
        "规格型号",
        "生产厂家",
        "成交总价",
        "设备配置及参数清单",
        "设备所属科室",
        "项目编号",
        "数量",
        "单价",
        "合计（元）",
        "含税价",
    )
    if any(x in s for x in noise):
        return False

    good = (
        "价格",
        "商务",
        "技术",
        "业绩",
        "包装运输",
        "售后",
        "质量保障",
        "服务保障",
        "节能",
        "环保",
        "评分",
        "评标价格",
    )
    return ("分" in s) or any(x in s for x in good)


def _looks_like_detailed_review_rule(text: str) -> bool:
    """判断like详细评审rule。"""
    s = _clean_text(text or "")
    if not s:
        return False
    if s in {"单位", "数量", "1台"}:
        return False

    noise = (
        "品名",
        "规格型号",
        "生产厂家",
        "成交总价",
        "设备配置及参数清单",
        "设备所属科室",
        "项目编号",
    )
    if any(x in s for x in noise) and "分" not in s:
        return False

    good = ("得", "分", "扣", "评分", "评审", "不得分", "满分", "响应", "废标", "否决")
    return any(x in s for x in good)


def _normalize_detailed_review_pair(item_name: str, rule: str) -> tuple[str, str] | None:
    """归一化详细评审键值对。"""
    item_name = _clean_text(item_name or "")
    rule = _clean_text(rule or "")
    parts = _split_compound_fields(item_name)

    # 例：1 / 价格部分 / 评标价格（30分） / 具体评分规则
    if len(parts) >= 3:
        candidate_name = " / ".join(parts[:2])
        candidate_rule = " / ".join(parts[2:])
        if not rule or rule in {"单位", "数量", "1台"} or len(rule) < 6:
            rule = candidate_rule
        item_name = candidate_name
    elif len(parts) >= 2 and (not rule or len(rule) < 6):
        item_name = parts[0]
        rule = " / ".join(parts[1:])

    item_name = re.sub(r"^\d+\s*/\s*", "", item_name).strip()

    if not _looks_like_detailed_review_item(item_name):
        return None
    if not _looks_like_detailed_review_rule(rule):
        return None
    return item_name, rule


def _pick_review_item_rule(item: dict) -> tuple[str, str] | None:
    """挑选评审项rule。"""
    raw_left = _clean_text(
        item.get("review_item")
        or item.get("审查项")
        or item.get("评审项目")
        or item.get("评分项目")
        or item.get("_source_text")
        or ""
    )
    raw_right = _clean_text(
        item.get("tender_requirement")
        or item.get("采购文件要求")
        or item.get("招标文件要求")
        or item.get("合格条件")
        or item.get("score_rule")
        or item.get("评审标准")
        or item.get("评分标准")
        or item.get("采购文件评分要求")
        or ""
    )

    if _is_headerish_review_text(raw_left) or _is_headerish_review_text(raw_right):
        return None

    # 先按“详细评审表”专用规则尝试标准化
    detailed_pair = _normalize_detailed_review_pair(raw_left, raw_right)
    if detailed_pair:
        return detailed_pair

    # 再走普通资格/符合性表逻辑
    if raw_left and raw_right and raw_left != raw_right:
        return raw_left, raw_right

    parts = _split_compound_fields(raw_left or raw_right)
    if len(parts) >= 2:
        left = " / ".join(parts[:-1])
        right = parts[-1]
        if _is_headerish_review_text(left) or _is_headerish_review_text(right):
            return None
        return left, right

    return None


def _normalize_delivery_requirement_text(text: str) -> str:
    """归一化交付需求文本。"""
    s = _clean_text(text or "")
    if not s:
        return ""
    s = re.sub(r"^(?:采购项目（标的）)?交付(?:的)?时间[：:]\s*", "", s)
    s = re.sub(r"^(?:交货期(?:限)?|交付期限)[：:]\s*", "", s)
    return s.strip("；;。 ")


def _build_delivery_commitment_text(packages: list, tender_raw: str) -> str:
    """构建交付承诺文本。"""
    if not packages:
        return "确保交货期限满足招标文件要求"

    normalized: list[tuple[str, str]] = []
    for pkg in packages:
        raw = _normalize_delivery_requirement_text(_extract_delivery_time(pkg, tender_raw))
        if raw in {"", "按采购文件要求完成交货", "按招标文件要求完成交货", "招标文件要求的交货期限"}:
            raw = "招标文件要求"
        normalized.append((str(getattr(pkg, "package_id", "") or "【待填写】"), raw))

    def _render_single_requirement(requirement: str) -> str:
        """渲染single需求。"""
        if requirement == "招标文件要求":
            return "确保交货期限满足招标文件要求"
        if requirement.endswith("交货"):
            base = requirement[:-2]
            if base.endswith("内"):
                return f"确保在{base}完成交货"
            return f"确保在{base}内完成交货"
        if re.search(r"(日|天|周|月)内$", requirement):
            return f"确保在{requirement}完成交货"
        return f"确保交货期限满足{requirement}要求"

    if len(normalized) == 1:
        return _render_single_requirement(normalized[0][1])

    parts: list[str] = []
    for package_id, requirement in normalized:
        if requirement == "招标文件要求":
            parts.append(f"包{package_id}交货期限满足招标文件要求")
        elif requirement.endswith("交货"):
            base = requirement[:-2]
            if base.endswith("内"):
                parts.append(f"包{package_id}在{base}完成交货")
            else:
                parts.append(f"包{package_id}在{base}内完成交货")
        elif re.search(r"(日|天|周|月)内$", requirement):
            parts.append(f"包{package_id}在{requirement}完成交货")
        else:
            parts.append(f"包{package_id}交货期限满足{requirement}要求")
    return "；".join(parts)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """去重keeporder。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = _clean_text(item)
        key = re.sub(r"\s+", "", value)
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _collect_zb_service_requirement_points(tender_raw: str) -> dict[str, list[str]]:
    """提取 ZB 原文中的服务与验收要点。"""
    blocks = [
        _extract_anchor_block(
            tender_raw,
            anchor_patterns=[r"四、采购标的需满足的服务标准[、,，期限效率等要求]*", r"四、采购标的需满足的服务标准"],
            stop_patterns=[r"五、采购标的的验收标准", r"第六章\s*投标文件格式", r"第六章"],
            max_chars=5000,
        ),
        _extract_anchor_block(
            tender_raw,
            anchor_patterns=[r"五、采购标的的验收标准"],
            stop_patterns=[r"六、采购标的的其他技术、服务等要求", r"第六章\s*投标文件格式", r"第六章"],
            max_chars=2500,
        ),
        _extract_anchor_block(
            tender_raw,
            anchor_patterns=[r"六、采购标的的其他技术、服务等要求"],
            stop_patterns=[r"第六章\s*投标文件格式", r"第六章"],
            max_chars=3200,
        ),
        _extract_anchor_block(
            tender_raw,
            anchor_patterns=[r"售后服务及要求[：:]?"],
            stop_patterns=[r"易损件及耗材", r"其他要求", r"第六章\s*投标文件格式", r"第六章"],
            max_chars=1200,
        ),
    ]

    service: list[str] = []
    install: list[str] = []
    acceptance: list[str] = []
    packaging: list[str] = []
    commitment: list[str] = []

    for block in blocks:
        for item in _merge_bullet_lines(block):
            value = _clean_text(item)
            if not value or len(value) < 8:
                continue
            if any(token in value for token in ("采购标的需满足的服务标准", "采购标的的验收标准", "采购标的的其他技术、服务等要求")):
                continue
            if any(token in value for token in ("包装", "运输", "发运", "防潮", "防锈", "配货单", "装箱单")):
                packaging.append(value)
            if any(token in value for token in ("安装", "调试", "培训", "试运行")):
                install.append(value)
            if any(token in value for token in ("验收", "说明书", "手册", "资料", "测试报告", "计量", "中文", "技术支持资料", "证明材料")):
                acceptance.append(value)
            if any(token in value for token in ("售后", "维修", "维护", "保养", "备件", "零件", "升级", "响应", "反馈", "现场", "备用设备", "保修", "质保")):
                service.append(value)
            if any(token in value for token in ("工作条件", "插头", "电源", "条件", "交货地点", "交付的时间")):
                commitment.append(value)

    return {
        "service": _dedupe_keep_order(service),
        "install": _dedupe_keep_order(install),
        "acceptance": _dedupe_keep_order(acceptance),
        "packaging": _dedupe_keep_order(packaging),
        "commitment": _dedupe_keep_order(commitment),
    }


def _project_goods_name(packages: list) -> str:
    """汇总项目货物名称。"""
    names = _dedupe_keep_order([getattr(pkg, "item_name", "") or "" for pkg in (packages or [])])
    return "、".join(names) if names else "【待填写：货物名称】"


def _profile_payload_for_package(
    package_id: str,
    product_profiles: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """返回指定包件的画像字典。"""
    if not isinstance(product_profiles, dict):
        return None
    profile = product_profiles.get(package_id)
    if profile is None:
        return None
    if isinstance(profile, dict):
        return profile
    if hasattr(profile, "model_dump"):
        return profile.model_dump()
    return None


def _product_for_package(
    package_id: str,
    products: dict[str, Any] | None,
):
    """返回指定包件的产品对象。"""
    if not isinstance(products, dict):
        return None
    return products.get(package_id)


def _product_identity_text(
    product=None,
    product_profile: dict[str, Any] | None = None,
) -> str:
    """返回品牌/型号识别文本。"""
    product_profile = product_profile or {}
    brand = _clean_text(
        getattr(product, "brand", "")
        or product_profile.get("brand")
        or getattr(product, "manufacturer", "")
        or product_profile.get("manufacturer")
    )
    model = _clean_text(
        getattr(product, "model", "")
        or product_profile.get("model")
    )
    manufacturer = _clean_text(
        getattr(product, "manufacturer", "")
        or product_profile.get("manufacturer")
    )
    identity_parts = [part for part in (brand, model) if part]
    if identity_parts:
        return " / ".join(identity_parts)
    if manufacturer and model:
        return f"{manufacturer} / {model}"
    return manufacturer or model


def _normalize_recommended_evidence_text(req_key: str, requirement: str = "") -> str:
    """把待补证提示转成可直接落表的建议资料文本。"""
    label = _recommended_evidence_label(req_key, requirement)
    normalized = _clean_text(label.replace("【待补证：", "").replace("】", ""))
    return normalized or "产品说明书/彩页/厂家参数表"


def _build_zb_pending_response_text(
    req_key: str,
    req_val: str,
    model_identity: str = "",
) -> str:
    """生成 ZB 格式技术表的非空待补响应文本。"""
    guidance = _clean_text(_build_pending_response_guidance(req_key, req_val))
    prefix = f"拟投产品（{model_identity}）" if model_identity else "拟投产品"
    return f"{prefix}将对照本条逐项响应；{guidance or '请补充实际响应值和对应证明材料页码。'}"


def _normalize_zb_requirement_fields(requirement: str) -> tuple[str, str]:
    """把 ZB 原子条款拆成参数名和要求值。"""
    normalized = _clean_text(requirement)
    key, value = _split_requirement_text("", normalized)
    key = _clean_text(key)
    value = _clean_text(value)
    if key and value and key != value:
        return key, value
    for sep in ("：", ":"):
        if sep in normalized:
            left, right = [part.strip() for part in normalized.split(sep, 1)]
            if left and right:
                return _clean_text(left), _clean_text(right)
    return normalized, normalized


def _zb_requirement_tokens(text: str) -> set[str]:
    """返回用于模糊匹配的 token 集合。"""
    return {
        token
        for token in re.split(r"[，,、；;：:（）()\[\]\s/]+", _clean_text(text))
        if len(token) >= 2
    }


def _zb_requirement_match_score(
    requirement: str,
    row: dict[str, Any],
) -> int:
    """为原子条款与结构化需求行计算匹配分值。"""
    req_key, req_val = _normalize_zb_requirement_fields(requirement)
    row_key = _clean_text(row.get("key") or row.get("parameter_name") or "")
    row_req = _clean_text(row.get("requirement") or row.get("value") or row.get("requirement_value") or "")
    row_full = _clean_text(" ".join(part for part in (row_key, row_req) if part))
    requirement_full = _clean_text(requirement)
    if not row_key and not row_req:
        return 0

    score = 0
    if row_full and row_full == requirement_full:
        score += 12
    if row_key and req_key and row_key == req_key:
        score += 8
    if row_req and req_val and row_req == req_val:
        score += 6
    if row_key and req_key and (row_key in req_key or req_key in row_key):
        score += 4
    if row_req and req_val and (row_req in req_val or req_val in row_req):
        score += 3

    req_tokens = _zb_requirement_tokens(req_key) | _zb_requirement_tokens(req_val)
    row_tokens = _zb_requirement_tokens(row_key) | _zb_requirement_tokens(row_req)
    overlap = len(req_tokens & row_tokens)
    score += min(overlap, 4)
    return score


def _match_requirement_row_for_zb(
    requirement: str,
    requirement_rows: list[dict[str, Any]],
    used_signatures: set[str],
) -> dict[str, Any] | None:
    """为 ZB 原子条款匹配最合适的结构化需求行。"""
    best_row: dict[str, Any] | None = None
    best_signature = ""
    best_score = 0
    for row in requirement_rows:
        signature = _clean_text(
            str(row.get("requirement_id") or f"{row.get('key', '')}::{row.get('requirement', '')}")
        )
        if signature and signature in used_signatures:
            continue
        score = _zb_requirement_match_score(requirement, row)
        if score > best_score:
            best_score = score
            best_row = row
            best_signature = signature
    if best_row is None or best_score < 3:
        return None
    if best_signature:
        used_signatures.add(best_signature)
    return best_row


def _fallback_atomic_rows_from_requirement_rows(
    requirement_rows: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """当未抽出 ZB 原子条款时，从结构化需求行回退生成表格行。"""
    rows: list[tuple[str, str]] = []
    for idx, row in enumerate(requirement_rows, start=1):
        key = _clean_text(row.get("key") or "")
        requirement = _clean_text(row.get("requirement") or row.get("value") or "")
        if not key and not requirement:
            continue
        if key and requirement and key != requirement:
            rows.append((str(idx), f"{key}：{requirement}"))
        else:
            rows.append((str(idx), key or requirement))
    return rows


def _format_zb_evidence_text(
    req_key: str,
    requirement: str,
    row: dict[str, Any] | None,
) -> str:
    """格式化 ZB 技术支持资料说明列。"""
    if row:
        sources = _dedupe_keep_order(
            [
                _clean_text(row.get("bidder_evidence_source") or ""),
                _clean_text(row.get("bid_evidence_file") or ""),
                _clean_text(row.get("evidence_source") or ""),
            ]
        )
        page = row.get("bidder_evidence_page") or row.get("bid_evidence_page")
        if sources:
            source_text = " / ".join(sources[:2])
            if page not in (None, "", 0):
                return f"{source_text}（P{page}）"
            if _has_real_bidder_response(row.get("response")):
                return f"{source_text}（页码待补）"

    recommended = _normalize_recommended_evidence_text(req_key, requirement)
    return f"{recommended}（页码待补）"


def _build_zb_config_items(
    pkg,
    tender_raw: str,
    *,
    product=None,
    product_profile: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
) -> list[tuple[str, str, str, str]]:
    """优先用产品画像，否则从招标文件中提取配置项。"""
    items = _profile_config_items(product_profile) if product_profile else []
    if items:
        return items
    return _extract_configuration_items(pkg, tender_raw, normalized_result=normalized_result)


def _build_zb_packaging_sample_rows(
    packages: list,
    tender_raw: str,
    *,
    products: dict[str, Any] | None = None,
    product_profiles: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
) -> list[list[str]]:
    """生成配货单样本行。"""
    rows: list[list[str]] = []
    seq = 1
    for pkg in packages:
        product = _product_for_package(pkg.package_id, products)
        profile = _profile_payload_for_package(pkg.package_id, product_profiles)
        identity = _product_identity_text(product, profile) or "按投标品牌/型号填写"
        config_items = _build_zb_config_items(
            pkg,
            tender_raw,
            product=product,
            product_profile=profile,
            normalized_result=normalized_result,
        )
        total_boxes = 3 if len(config_items) >= 4 else 2

        accessory_names = [
            name
            for name, _, _, remark in config_items
            if _classify_config_item(name) in {"核心模块", "标准附件", "配套软件", "初始耗材"}
            and pkg.item_name not in name
        ]
        doc_names = [
            name
            for name, _, _, remark in config_items
            if _classify_config_item(name) in {"随机文件", "安装/培训资料"}
        ]
        accessory_text = "、".join(accessory_names[:4]) or "随机附件、专用工具"
        doc_text = "、".join(doc_names[:4]) or "说明书、合格证、装箱单"

        rows.append(
            [
                str(seq),
                f"{pkg.item_name}主机",
                identity,
                f"{_extract_package_quantity(pkg, tender_raw)}/台",
                f"1/共{total_boxes}箱",
                doc_text,
                "到货时核对主机型号、序列号、外观及核心配置",
            ]
        )
        seq += 1
        rows.append(
            [
                str(seq),
                f"{pkg.item_name}配套附件",
                "与主机配套",
                "1批",
                f"2/共{total_boxes}箱",
                accessory_text,
                "按装箱清单逐项点验数量和状态",
            ]
        )
        seq += 1
        if doc_text:
            rows.append(
                [
                    str(seq),
                    f"{pkg.item_name}随机资料/授权文件",
                    "与主机配套",
                    "1批",
                    f"{total_boxes}/共{total_boxes}箱",
                    doc_text,
                    "随货移交，用于到货验收、安装调试和培训留档",
                ]
            )
            seq += 1
    return rows or [
        ["1", "设备主机", "按投标品牌/型号填写", "1台", "1/共2箱", "说明书、合格证、装箱单", "到货时核对品牌、型号和外观"],
        ["2", "配套附件", "与主机配套", "1批", "2/共2箱", "随机附件/资料", "按装箱清单逐项点验"],
    ]


def _build_zb_acceptance_doc_rows(tender_raw: str) -> list[list[str]]:
    """生成验收资料清单行。"""
    service_details = _collect_zb_service_requirement_points(tender_raw)
    rows: list[list[str]] = [
        ["1", "产品合格证/出厂检验资料", "是，随货提供", "用于到货验收和合规留档"],
        ["2", "装箱清单/配货单", "是，随货提供", "用于到货开箱、数量核对和附件点验"],
        ["3", "安装调试记录", "是，安装调试完成后形成", "记录安装、调试、试运行和异常处理情况"],
        ["4", "培训记录", "是，培训完成后形成", "记录培训时间、对象、内容和签到信息"],
        ["5", "使用说明书/维护手册", "是，随货提供", "用于操作、维护保养和后续复核"],
        ["6", "售后服务联系方式", "是，交付时提供", "明确报修渠道、联系人和响应方式"],
        ["7", "验收报告/功能确认记录", "是，验收阶段形成", "结合功能测试结果完成签署"],
    ]

    acceptance_text = " ".join(service_details.get("acceptance", []))
    if any(token in acceptance_text for token in ("计量", "检测", "测试报告")):
        rows.append(["8", "计量检测/测试报告", "按采购要求提供", "属于计量设备或验收要求明确提出时提交"])
    if any(token in acceptance_text for token in ("注册证", "备案凭证", "技术支持资料", "证明材料")):
        rows.append(["9", "注册证/备案凭证及技术支持资料", "按验收节点提交", "与投标型号、附件资料保持一致"])
    return rows


def _build_zb_technical_doc_index_rows(
    pkg,
    tender_raw: str,
    *,
    product=None,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profile: dict[str, Any] | None = None,
) -> list[list[str]]:
    """生成格式9里的技术支持资料与条款对应索引。"""
    requirement_rows, _ = _build_requirement_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
    )
    atomic_rows = (
        _extract_zb_atomic_technical_rows(pkg, tender_raw)
        or _extract_zb_rows_from_pkg_requirements(pkg)
        or _fallback_atomic_rows_from_requirement_rows(requirement_rows)
    )

    rows: list[list[str]] = []
    used_signatures: set[str] = set()
    for idx, (clause_no, requirement) in enumerate(atomic_rows, start=1):
        matched_row = _match_requirement_row_for_zb(requirement, requirement_rows, used_signatures)
        req_key, _ = _normalize_zb_requirement_fields(requirement)
        evidence_text = _format_zb_evidence_text(req_key, requirement, matched_row)
        page = None
        if matched_row:
            page = matched_row.get("bidder_evidence_page") or matched_row.get("bid_evidence_page")
        location = f"对应格式8第{idx}行"
        if page not in (None, "", 0):
            location = f"{location}；证据页码P{page}"
        else:
            location = f"{location}；页码待补"
        rows.append([str(idx), clause_no, requirement, evidence_text, location])
    return rows or [["1", "1", "核心技术条款", "产品说明书/彩页/厂家参数表（页码待补）", "对应格式8第1行；页码待补"]]


def _build_zb_supporting_material_rows(
    pkg,
    tender_raw: str,
    *,
    product=None,
    product_profile: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
) -> list[list[str]]:
    """生成配置清单、随机附件和专用工具清单。"""
    config_items = _build_zb_config_items(
        pkg,
        tender_raw,
        product=product,
        product_profile=product_profile,
        normalized_result=normalized_result,
    )
    rows: list[list[str]] = []
    for idx, (name, unit, qty, remark) in enumerate(config_items, start=1):
        rows.append(
            [
                str(idx),
                _classify_config_item(name),
                name,
                f"{qty}{unit}",
                remark or _classify_config_item(name),
            ]
        )
    return rows or [[
        "1",
        "核心模块",
        f"{pkg.item_name}主机",
        f"{_extract_package_quantity(pkg, tender_raw)}台",
        "结合投标型号补充主机、附件、随机文件和专用工具明细",
    ]]


def _build_zb_service_material_rows(tender_raw: str) -> list[list[str]]:
    """生成安装调试、培训和验收资料清单。"""
    service_details = _collect_zb_service_requirement_points(tender_raw)
    install_digest = "；".join(service_details.get("install", [])[:2]) or "覆盖开箱点验、安装、调试、试运行和问题处理。"
    training_digest = "；".join(
        [item for item in service_details.get("install", []) if "培训" in item][:2]
    ) or "覆盖设备操作、日常维护、注意事项和培训签到记录。"
    acceptance_digest = "；".join(service_details.get("acceptance", [])[:2]) or "包括到货验收、安装验收、功能验收及资料移交。"
    rows = [
        ["1", "安装调试方案", "设备到货后实施", install_digest],
        ["2", "培训方案", "安装调试完成后实施", training_digest],
        ["3", "验收资料清单", "到货验收/安装验收阶段", acceptance_digest],
        ["4", "随货技术资料移交清单", "随货提供", "包括说明书、合格证、装箱单、维护手册等随机资料。"],
    ]
    if any(token in acceptance_digest for token in ("计量", "检测", "测试报告")):
        rows.append(["5", "计量检测/测试报告", "按采购要求提供", "涉及计量或测试要求时与验收资料同步提交。"])
    return rows


def _build_zb_authorization_rows(
    pkg,
    tender_raw: str,
    *,
    product=None,
    product_profile: dict[str, Any] | None = None,
) -> list[list[str]]:
    """生成合法来源及授权证明清单。"""
    _ = (product, product_profile)
    requirement_text = " ".join(
        [tender_raw, " ".join(str(v) for v in (getattr(pkg, "technical_requirements", None) or {}).values())]
    )
    rows = [
        ["1", "产品合法来源证明", "制造商授权书/合法来源说明", "代理投标或招标文件要求授权时提供。"],
        ["2", "医疗器械注册证/备案凭证", "注册证/备案凭证复印件", "名称、型号、适用范围应与拟投产品一致。"],
        ["3", "软件/硬件授权证明", "软件授权文件/许可证明", "涉及应用软件、工作站软件、接口模块时提供。"],
        ["4", "其他合法性文件", "按第五章要求补充的节能环保/检测/能力验证资料", "结合项目评审项和采购需求补充。"],
    ]
    if "逐级授权" in requirement_text:
        rows.append(["5", "进口产品逐级授权", "逐级授权文件", "招标文件明确要求逐级授权时逐级提供。"])
    return rows

def _single_package_id(packages: list) -> str:
    """在单包场景下返回唯一包号。"""
    if len(packages or []) == 1:
        return str(getattr(packages[0], "package_id", "") or "")
    return ""


def _autofill_zb_raw_template(title: str, raw_block: str, tender, packages: list, tender_raw: str) -> str:
    """返回ZB 格式raw模板。"""
    raw = (raw_block or "").strip()
    if not raw:
        return ""

    project_name = getattr(tender, "project_name", "") or "【待填写：项目名称】"
    project_no = getattr(tender, "project_number", "") or "【待填写：项目编号】"
    purchaser = getattr(tender, "purchaser", "") or "【待填写：采购人】"
    agency = getattr(tender, "agency", "") or "【待填写：代理机构】"
    goods = _project_goods_name(packages)
    package_id = _single_package_id(packages) or "【待填写】"

    replacements = [
        (r"[（(]\s*项目名称\s*[）)]", f"（{project_name}）"),
        (r"[（(]\s*项目编号\s*[）)]", f"（{project_no}）"),
        (r"[（(]\s*招标编号\s*[）)]", f"（{project_no}）"),
        (r"[（(]\s*单位名称\s*[）)]", f"（{purchaser}）"),
        (r"[（(]\s*采购人\s*[）)]", f"（{purchaser}）"),
        (r"[（(]\s*项目名称\s*的\s*[）)]", f"（{project_name}的）"),
    ]
    for pattern, value in replacements:
        raw = re.sub(pattern, value, raw)

    raw = re.sub(r"(?m)^(\s*项目名称[：:])\s*$", rf"\1{project_name}", raw)
    raw = re.sub(r"(?m)^(\s*招标编号[：:])[_＿\-\s]*$", rf"\1{project_no}", raw)
    raw = re.sub(r"(?m)^(\s*项目编号[：:])[_＿\-\s]*$", rf"\1{project_no}", raw)
    raw = re.sub(r"(?m)^(\s*品目号[：:])\s*$", rf"\1{package_id}", raw)
    raw = re.sub(r"项目名称[:：]\s*项目编号[:：]\s*$", f"项目名称：{project_name}    项目编号：{project_no}", raw, flags=re.M)
    raw = re.sub(r"招标编号[:：]\s*_{2,}", f"招标编号：{project_no}", raw)
    raw = re.sub(r"项目编号[:：]\s*_{2,}", f"项目编号：{project_no}", raw)
    raw = re.sub(r"设备名称[:：]\s*_{2,}", f"设备名称：{goods}", raw)
    raw = re.sub(r"货物名称[:：]\s*_{2,}", f"货物名称：{goods}", raw)
    raw = re.sub(r"致[:：]\s*采购人", f"致：{purchaser}", raw)
    raw = re.sub(r"致[:：]\s*采购代理机构", f"致：{agency}", raw)

    if "开标一览表" in title:
        delivery_values = _dedupe_keep_order(
            [
                _normalize_delivery_requirement_text(_extract_delivery_time(pkg, tender_raw))
                for pkg in packages
            ]
        )
        delivery = "；".join(delivery_values) if delivery_values else _build_delivery_commitment_text(packages, tender_raw)
        warranty = getattr(getattr(tender, "commercial_terms", None), "warranty_period", "") or ""
        raw = re.sub(r"(?m)^(合同履行期限)\s*$", rf"\1\n{delivery}", raw)
        if warranty:
            raw = re.sub(r"(?m)^(质保期)\s*$", rf"\1\n{warranty}", raw)

    if "售后服务承诺书" in title:
        raw = re.sub(r"本项目（项目编号：\s*）", f"本项目（项目编号：{project_no}）", raw)
        raw = re.sub(r"设备（设备名称：\s*）", f"设备（设备名称：{goods}）", raw)
        if purchaser and "致：" in raw and "吉林大学中日联谊医院" not in raw:
            raw = re.sub(r"致[:：][^\n]*", f"致：{purchaser}", raw, count=1)

    return raw.strip()


def _is_title_only_zb_template(raw_block: str) -> bool:
    """判断标题onlyZB 格式模板。"""
    lines = [line.strip() for line in (raw_block or "").splitlines() if line.strip()]
    if not lines:
        return True
    if len(lines) == 1:
        return True
    meaningful = [line for line in lines[1:] if not line.startswith("|")]
    return not meaningful


def _zb_template_guidance_text(title: str) -> str:
    """返回模板guidance文本。"""
    compact = re.sub(r"\s+", "", title or "")
    if "身份证正反面复印件" in compact:
        return "本页用于粘贴对应身份证正反面复印件，并加盖投标人公章。"
    if "制造商授权书" in compact:
        return "本页用于放置制造商授权文件原件或复印件，并按招标文件要求签章。"
    if "类似项目业绩表" in compact:
        return "请按招标文件要求填写类似项目业绩，并在表后附业绩证明材料。"
    if "声明函" in compact:
        return "请按招标文件原格式填写声明内容，并完成签字盖章。"
    if "承诺书" in compact:
        return "请按招标文件原格式填写承诺内容，并完成签字盖章。"
    if any(token in compact for token in ("偏离表", "响应表", "响应及偏离表", "响应对照表")):
        return "请按招标文件要求逐项填写，不得漏项、缺项或仅复制招标要求原文。"
    if any(token in compact for token in ("明细表", "报价表", "一览表", "申请表")):
        return "请按招标文件原格式填写本表内容，并保留原有列项。"
    return "请按招标文件原格式填写本节内容。"


def _is_zb_tech_block_start(line: str) -> bool:
    """判断ZB 格式tech文本块start。"""
    s = _clean_text(line or "")
    if not s or s.startswith("|"):
        return False
    return bool(
        re.match(r"^(?:采购标的需满足的技术规格要求|技术参数|主要技术参数|主要配置、?功能)[：:]?", s)
        or re.match(r"^[★▲]?\s*\d+(?:\.\d+)*[、.]?\s*", s)
    )


def _extract_zb_technical_scope_block(tender_raw: str) -> str:
    """提取ZB 格式技术范围文本块。"""
    lines = [line.rstrip() for line in (tender_raw or "").splitlines()]
    if not lines:
        return ""

    blocks: list[str] = []
    current: list[str] = []
    started = False

    for raw_line in lines:
        line = _clean_text(raw_line)
        if not line:
            continue
        if line.startswith("|"):
            if started and current:
                blocks.append("\n".join(current).strip())
                current = []
                started = False
            continue

        if not started:
            if re.match(r"^(?:采购标的需满足的技术规格要求|技术参数|主要技术参数)[：:]?$", line):
                started = True
                current = [line]
                continue
            if re.match(r"^(?:\d+[、.]?)?主要配置、?功能[：:].*", line):
                started = True
                current = ["技术参数：", line]
                continue
            continue

        if any(token in line for token in ("售后服务及要求", "易损件及耗材", "其他要求", "第六章 投标文件格式", "第六章投标文件格式")):
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            started = False
            continue

        if re.match(r"^(?:一、商务部分|二、技术文件部分|格式\s*\d+)", line):
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            started = False
            continue

        if _is_zb_tech_block_start(line) or not current:
            current.append(line)
        else:
            current[-1] += (" " if not current[-1].endswith(("：", ":")) else "") + line

    if current:
        blocks.append("\n".join(current).strip())

    if not blocks:
        return ""

    def _score(block: str) -> tuple[int, int]:
        """为技术块候选文本计算优先级分数。"""
        numbered = len(re.findall(r"(?:^|\n)[★▲]?\s*\d+(?:\.\d+){1,}[、.]?\s*", block))
        stars = block.count("★") * 4 + block.count("▲") * 2
        return numbered + stars, len(block)

    return max(blocks, key=_score)


def _extract_zb_atomic_technical_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    """提取ZB 格式原子技术行。"""
    block = _extract_zb_technical_scope_block(tender_raw)
    if not block:
        return []

    merged_lines: list[str] = []
    for raw_line in block.splitlines():
        line = _clean_text(raw_line)
        if not line or line.startswith("|"):
            continue
        if _is_zb_tech_block_start(line):
            merged_lines.append(line)
        elif merged_lines:
            merged_lines[-1] += (" " if not merged_lines[-1].endswith(("：", ":")) else "") + line
        else:
            merged_lines.append(line)

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for line in merged_lines:
        normalized = _clean_text(line)
        if not normalized:
            continue
        if re.match(r"^(?:采购标的需满足的技术规格要求|技术参数|主要技术参数)[：:]?$", normalized):
            continue

        config_match = re.match(r"^(?P<num>\d+)[、.]?\s*(?P<body>主要配置、?功能[：:].+)$", normalized)
        if config_match:
            clause_no = config_match.group("num")
            requirement = config_match.group("body")
        else:
            match = re.match(
                r"^(?P<mark>[★▲]?)\s*(?P<num>\d+(?:\.\d+)*)(?:[、.]?)\s*(?P<body>.+)$",
                normalized,
            )
            if not match:
                continue
            clause_no = f"{match.group('mark')}{match.group('num')}"
            requirement = match.group("body").strip()
            depth = match.group("num").count(".") + 1
            if depth <= 2 and requirement.endswith(("：", ":", "；", ";")) and len(requirement.rstrip("：:；;")) <= 18:
                continue

        requirement = requirement.strip("；; ")
        if not requirement or requirement in {"主要技术参数", "技术参数"}:
            continue

        dedupe_key = f"{clause_no}::{re.sub(r'\\s+', '', requirement)}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append((clause_no, requirement))

    return rows


def _extract_zb_rows_from_pkg_requirements(pkg) -> list[tuple[str, str]]:
    """提取包件需求中的ZB 格式行。"""
    requirements = getattr(pkg, "technical_requirements", None) or {}
    if not requirements:
        return []

    rows: list[tuple[str, str]] = []
    for idx, (key, value) in enumerate(requirements.items(), start=1):
        key_text = _clean_text(key)
        value_text = _clean_text(value)
        clause_no = key_text if re.fullmatch(r"[★▲]?\d+(?:\.\d+)*", key_text) else str(idx)
        if value_text and key_text and key_text != clause_no:
            requirement = f"{key_text}：{value_text}"
        else:
            requirement = value_text or key_text
        requirement = requirement.strip("；; ")
        if requirement:
            rows.append((clause_no, requirement))
    return rows


def _build_zb_service_section(
    tender,
    tender_raw: str = "",
    package_index: int = 1,
    *,
    packages: list | None = None,
    products: dict[str, Any] | None = None,
    product_profiles: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
) -> str:
    """构建ZB 格式服务章节。"""
    _ = package_index
    project_name = getattr(tender, "project_name", "") or "本项目"
    packages = packages or list(getattr(tender, "packages", None) or [])
    delivery_requirement = _build_delivery_commitment_text(packages, tender_raw)
    delivery_places = "；".join(
        _dedupe_keep_order([_extract_delivery_place(pkg, tender_raw) for pkg in packages])
    ) or "采购人指定地点"
    service_details = _collect_zb_service_requirement_points(tender_raw)
    packaging_points = service_details["packaging"][:4]
    install_points = service_details["install"][:5]
    acceptance_points = service_details["acceptance"][:5]
    service_points = service_details["service"][:6]
    extra_commitments = service_details["commitment"][:3]

    parts = []

    parts.append(
        "一、供货组织实施方案\n"
        f"针对{project_name}，我方拟成立专项供货实施小组，设置项目负责人、商务联络、物流协调、安装调试工程师、培训工程师、售后支持人员等岗位，形成“合同签订—备货验货—发运配送—到货验收—安装调试—培训交付—质保服务”全流程闭环。\n\n"
        "项目实施原则：\n"
        "1. 按招标文件及合同要求组织供货，确保供货内容、品牌、型号、配置与投标响应一致；\n"
        "2. 对关键节点实行责任到人、进度到天、问题到单的过程管理；\n"
        f"3. 对运输、安装、调试、验收、培训、售后服务实行全过程留痕管理，便于采购人复核；\n"
        f"4. 交货地点按招标文件要求执行，为：{delivery_places}。"
    )

    parts.append(
        "二、供货进度计划表\n\n"
        + _md_table(
            ["阶段", "工作内容", "计划时长", "输出成果", "责任人"],
            [
                ["1", "合同签订、项目启动", "1日", "项目启动确认", "项目负责人"],
                ["2", "备货、出厂检验、资料整理", "3-7日", "设备/随机资料/清单", "供货负责人"],
                ["3", "包装、运输、保险、发运", "3-5日", "发货通知、物流单据", "物流协调人员"],
                ["4", "到货开箱点验", "1日", "到货验收记录", "项目负责人/采购人"],
                ["5", "安装调试", "1-3日", "安装调试记录", "安装工程师"],
                ["6", "操作培训", "1日", "培训签到及培训记录", "培训工程师"],
                ["7", "试运行及履约验收", "按采购人要求", "验收资料、验收单", "项目负责人"],
            ],
        )
    )

    packaging_extra = "\n".join(f"{idx + 6}. {point}" for idx, point in enumerate(packaging_points))
    parts.append(
        "三、包装与运输方案\n"
        "1. 包装要求：设备采用原厂标准包装或不低于原厂标准的加固包装，做到防潮、防震、防撞、防雨、防锈；\n"
        "2. 外包装标识：外包装清晰标注项目名称、设备名称、型号规格、数量、收货单位、收货地址、重心方向、轻放防潮等运输标识；\n"
        "3. 发运管理：发货前再次复核设备名称、型号、数量、附件、随机文件，确保账、物、单一致；\n"
        "4. 运输保障：根据设备特性合理选择运输工具及路线，对关键设备采取重点防护措施；\n"
        "5. 到货交接：到货后由双方共同进行外观检查、数量核对、箱单核对、附件点验。"
        + (f"\n{packaging_extra}" if packaging_extra else "")
    )

    parts.append(
        "四、配货单样本\n\n"
        + _md_table(
            ["序号", "货物名称", "品牌/型号", "数量", "包装箱号", "随机附件/资料", "备注"],
            _build_zb_packaging_sample_rows(
                packages,
                tender_raw,
                products=products,
                product_profiles=product_profiles,
                normalized_result=normalized_result,
            ),
        )
    )

    parts.append(
        "五、安装调试方案\n"
        "1. 安装前确认：提前与采购人确认安装场地、电源条件、环境条件、网络条件及配套要求；\n"
        "2. 到货开箱：按装箱清单逐项核对设备、附件、资料；\n"
        "3. 安装实施：由具备经验的技术工程师完成设备安装、连接、初始化配置；\n"
        "4. 调试测试：完成开机测试、功能测试、性能测试，并记录调试结果；\n"
        "5. 问题处理：安装调试中发现异常时，第一时间定位原因并提出整改措施，确保设备具备正式交付条件。"
        + ("".join(f"\n{idx + 6}. {point}" for idx, point in enumerate(install_points)) if install_points else "")
    )

    parts.append(
        "六、质量保障措施及方案\n"
        "我方针对本项目建立全过程质量控制机制，覆盖备货、出库、包装、运输、安装、调试、培训、验收等环节。\n\n"
        "质量控制要点：\n"
        "1. 供货前复核设备配置、技术参数及随机资料，确保与投标文件一致；\n"
        "2. 出库前进行外观检查、数量核对、资料校验；\n"
        "3. 安装调试后进行功能验证并形成记录；\n"
        "4. 配合采购人进行履约验收，并按照要求提交完整验收资料。"
        + ("".join(f"\n{idx + 5}. {point}" for idx, point in enumerate(acceptance_points)) if acceptance_points else "")
    )

    parts.append(
        "七、验收资料清单\n\n"
        + _md_table(
            ["序号", "资料名称", "是否提供", "备注"],
            _build_zb_acceptance_doc_rows(tender_raw),
        )
    )

    parts.append(
        "八、售后及其他伴随服务方案\n"
        "1. 在质保期内提供技术支持、维修维护、故障响应、备件保障等服务；\n"
        "2. 提供现场安装培训和后续技术咨询服务；\n"
        "3. 对采购人提出的使用问题及时响应并形成处理闭环；\n"
        "4. 对需要返修、更换的部件，按合同及承诺要求执行。"
        + ("".join(f"\n{idx + 5}. {point}" for idx, point in enumerate(service_points)) if service_points else "")
    )

    commitment_lines = [
        f"1. {delivery_requirement}；",
        "2. 严格按照投标文件承诺的配置、参数和服务内容履约；",
        "3. 在接到采购人通知后及时安排专业人员响应；",
        "4. 配合采购人完成安装、调试、培训、验收及后续服务工作；",
        "5. 对项目实施全过程形成书面记录，便于追溯和复核；",
        "6. 质保期内提供持续、稳定、可执行的售后服务。",
    ]
    for point in extra_commitments:
        commitment_lines.append(f"{len(commitment_lines) + 1}. {point}；")
    for point in service_points[:2]:
        commitment_lines.append(f"{len(commitment_lines) + 1}. {point}；")
    parts.append(
        "九、服务保障承诺\n"
        + "\n".join(commitment_lines)
    )

    return "\n\n".join(parts)


def _normalize_procurement_terms_for_zb(text: str) -> str:
    """归一化ZB 格式的采购terms。"""
    if not text:
        return text
    text = text.replace("谈判文件", "招标文件")
    text = text.replace("磋商文件", "招标文件")
    text = text.replace("响应文件", "投标文件")
    text = text.replace("磋商小组", "评标委员会")
    return text


def _build_zb_technical_response_table(
    tender,
    pkg,
    tender_raw: str,
    raw_block: str = "",
    *,
    product=None,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profile: dict[str, Any] | None = None,
) -> str:
    """构建ZB 格式技术响应表。"""
    headers = _extract_table_headers_from_raw_block(raw_block) or [
        "招标文件条目号",
        "招标文件采购需求的内容与数值",
        "投标人的技术响应内容与数值",
        "技术响应偏差说明",
        "技术支持资料（或证明材料）说明",
    ]

    project_name = getattr(tender, "project_name", "") or "【待填写：项目名称】"
    project_no = (
        getattr(tender, "project_number", "")
        or getattr(tender, "project_no", "")
        or "【待填写：招标编号】"
    )

    structured_rows, _ = _build_requirement_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
    )
    atomic_rows = (
        _extract_zb_atomic_technical_rows(pkg, tender_raw)
        or _extract_zb_rows_from_pkg_requirements(pkg)
        or _fallback_atomic_rows_from_requirement_rows(structured_rows)
    )

    rows: list[list[str]] = []
    used_signatures: set[str] = set()
    model_identity = _product_identity_text(product, product_profile)

    for clause_no, requirement in atomic_rows:
        req_key, req_val = _normalize_zb_requirement_fields(requirement)
        matched_row = _match_requirement_row_for_zb(requirement, structured_rows, used_signatures)

        raw_response = matched_row.get("response") if matched_row else ""
        has_real_response = bool(matched_row) and _has_real_bidder_response(raw_response)
        if has_real_response:
            response_text = _display_bidder_response(raw_response)
        else:
            response_text = _build_zb_pending_response_text(req_key, req_val, model_identity)

        if has_real_response:
            deviation_text = _normalize_deviation_status(
                (matched_row or {}).get("deviation_status"),
                has_real=True,
            )
            if "【待填写" in deviation_text:
                deviation_text = "拟无偏离，待证据复核"
        elif matched_row and _clean_text(raw_response):
            deviation_text = "响应值已提取，待证据复核"
        else:
            deviation_text = "待结合投标型号确认"

        evidence_text = _format_zb_evidence_text(req_key, requirement, matched_row)
        rows.append([clause_no, requirement, response_text, deviation_text, evidence_text])

    if not rows:
        rows = [[
            "1",
            "请按招标文件第五章逐条补录采购需求条款",
            "拟投产品将对照每条技术要求逐项响应，并补充实际参数值、功能实现方式和配置情况。",
            "待结合投标型号确认",
            "产品说明书/彩页/厂家参数表（页码待补）",
        ]]

    return "\n".join([
        "格式 8.采购需求响应及偏离表（按招标文件格式填写）",
        f"项目名称：{project_name}",
        f"招标编号：{project_no}",
        f"品目号：{getattr(pkg, 'package_id', '【待填写】')}",
        _md_table(headers, rows),
        "",
        "注：",
        "1、投标人应对招标文件第五章采购需求的内容给予逐条响应，以投标产品和服务所能达到的内容予以填写，而不应复制招标的技术要求作为响应内容；有具体参数的应填写具体参数。",
        "2、投标人应按照招标文件第五章采购需求中要求提供投标产品技术支持资料（或证明材料），并在采购需求响应及偏离表中给予文件名称、所处投标文件页码或位置等必要说明。",
        "投标人名称：                        （加盖单位公章）",
        "法定代表人：                        (签字盖章)",
        "授权代表:                           (签字)",
        "日期：                              ",
    ])


def _title_has_any(title: str, keywords: tuple[str, ...]) -> bool:
    """判断是否存在标题any。"""
    s = re.sub(r"\s+", "", title or "")
    return any(k in s for k in keywords)


def _is_bad_zb_template_title(title: str) -> bool:
    """判断异常ZB 格式模板标题。"""
    s = re.sub(r"\s+", "", title or "")
    if not s:
        return True

    bad_words = (
        "项目基本情况",
        "招标公告",
        "投标人须知",
        "评标办法",
        "合同条款",
        "采购需求",
        "目录",
        "评审索引",
        "资格性检查索引",
        "符合性检查索引",
        "评分办法索引",
    )
    return any(x in s for x in bad_words)


def _zb_default_entries() -> list[tuple[str, str]]:
    """返回默认ZB 格式entries。"""
    return [
        ("一、投标函", ""),
        ("二、开标一览表", ""),
        ("三、投标分项报价表", ""),
        ("四、法定代表人授权书", ""),
        ("五、资格证明文件", ""),
        ("六、商务条款响应及偏离表", ""),
        ("七、技术要求响应及偏离表", ""),
        ("八、供货、安装调试、质量保障及售后服务方案", ""),
        ("九、资格性审查响应对照表", ""),
        ("十、符合性审查响应对照表", ""),
        ("十一、详细评审响应对照表", ""),
        ("十二、无效投标情形自检表", ""),
    ]


def _build_zb_bid_letter(tender) -> str:
    """构建ZB 格式投标letter。"""
    project_name = getattr(tender, "project_name", "") or "【待填写：项目名称】"
    project_no = getattr(tender, "project_no", "") or "【待填写：项目编号】"

    return "\n".join([
        "致：北京典方建设工程咨询有限公司",
        f"根据贵方为 {project_name} 的投标邀请（招标编号 {project_no}），签字代表经正式授权并代表投标人提交投标文件。",
        "提交文件包括：",
        "1. 商务文件部分",
        "（1）投标函",
        "（2）开标一览表",
        "（3）投标分项报价表",
        "（4）投标保证金说明函/投标保证金凭证",
        "（5）法定代表人授权书",
        "（6）商务条款响应及偏离表",
        "（7）投标人一般情况表及相关证明文件",
        "（8）类似项目业绩表",
        "2. 技术文件部分",
        "（1）采购需求响应及偏离表",
        "（2）招标文件第五章“采购需求”规定的其他技术响应文件",
        "据此函，签字代表宣布同意如下：",
        "1. 投标人递交了投标文件，即意味着接受开标前的招标程序和招标的相应安排。",
        "2. 后附“开标一览表”中所涉及的货物和服务为我方参加此次投标响应的全部范围。",
        "3. 投标人将按招标文件的规定履行合同责任和义务。",
        "4. 我方已详细审阅全部招标文件及有关澄清、修改文件。",
        "5. 我方保证所提交的全部资料真实、准确、完整。",
        "",
        "投标人名称：________________（盖章）",
        "法定代表人或授权代表：________________（签字）",
        "日期：________________",
    ])


def _zb_required_template_titles() -> list[str]:
    """返回required模板标题。"""
    return [
        "格式 3投标分项报价表（格式）",
        "格式 4.投标保证金说明函（格式）",
        "格式 5.法定代表人授权书(格式)",
        "格式 6.商务条款偏离表",
        "格式 7.投标人一般情况表（格式）",
        "7.11类似项目业绩表（格式自拟）",
        "7.12制造商授权书（格式自拟）",
        "格式 8.采购需求响应及偏离表（格式）",
        "格式 9.第五章规定的证明文件和其他技术方案",
    ]

def _get_zb_template_entries(tender) -> list[tuple[str, str]]:
    """获取ZB 格式模板entries。"""
    templates = getattr(tender, "response_section_templates", []) or []
    entries: list[tuple[str, str]] = []

    for tpl in templates:
        title = (getattr(tpl, "title", "") or "").strip()
        raw_block = (getattr(tpl, "raw_block", "") or "").strip()

        if not title:
            continue
        if not _looks_like_safe_zb_template_title(title):
            continue

        entries.append((title, raw_block))

    entries = _dedupe_zb_entries(entries)

    # 公开招标：只要已经抽到至少 3 个真实模板标题，就以原模板为主；
    # 缺失的评审表/方案章节由 _build_zb_sections() 末尾补齐，不再整包回退默认十二章。
    if len(entries) >= 3:
        return entries

    return _zb_default_entries()


def _zb_render_template_content(title: str, raw_block: str, tender, packages: list, tender_raw: str) -> str:
    """渲染ZB 格式模板内容。"""
    raw = _autofill_zb_raw_template(title, raw_block, tender, packages, tender_raw)
    if not raw:
        return ""

    if _is_title_only_zb_template(raw):
        if "类似项目业绩表" in title:
            return _build_zb_performance_table(tender)
        if "制造商授权书" in title:
            return _build_zb_manufacturer_authorization(tender, packages)
        if "身份证正反面复印件" in title:
            return f"{raw}\n\n{_zb_template_guidance_text(title)}"
        return f"{raw}\n\n{_zb_template_guidance_text(title)}"

    return raw.strip()

def _build_zb_quote_summary_table(tender, packages, tender_raw: str) -> str:
    """构建ZB 格式报价汇总表。"""
    headers = ["包号", "货物名称", "数量", "投标报价（元）", "交货期", "交货地点"]
    rows: list[list[str]] = []

    for pkg in packages:
        rows.append([
            str(pkg.package_id),
            pkg.item_name,
            _extract_package_quantity(pkg, tender_raw),
            "【待填写】",
            _extract_delivery_time(pkg, tender_raw),
            _extract_delivery_place(pkg, tender_raw),
        ])

    return (
        f"项目名称：{tender.project_name}\n"
        f"项目编号：{tender.project_number}\n\n"
        f"{_md_table(headers, rows)}\n\n"
        "供应商全称：【待填写：投标人名称】\n"
        "日期：【待填写：年 月 日】"
    )


def _suggest_zb_detailed_response_location(item_name: str, rule: str = "") -> str:
    """生成建议ZB 格式详细响应location。"""
    haystack = _clean_text(f"{item_name} {rule}")
    if any(token in haystack for token in ("评标价格", "价格", "报价")):
        return "二、开标一览表；三、投标分项报价表；中小企业声明函（如适用）"
    if "业绩" in haystack:
        return "7.11类似项目业绩表及后附业绩证明材料"
    if any(token in haystack for token in ("技术规格", "技术参数", "技术要求", "响应程度")):
        return "格式8.采购需求响应及偏离表；格式9.技术支持资料及其他技术方案"
    if any(token in haystack for token in ("包装运输", "配货单", "运输方案")):
        return "供货、安装调试、质量保障及售后服务方案 / 三、包装与运输方案 / 四、配货单样本"
    if any(token in haystack for token in ("售后", "伴随服务")):
        return "售后服务承诺书；供货、安装调试、质量保障及售后服务方案 / 八、售后及其他伴随服务方案"
    if any(token in haystack for token in ("质量保障", "验收")):
        return "供货、安装调试、质量保障及售后服务方案 / 六、质量保障措施及方案 / 七、验收资料清单"
    if "服务保障承诺" in haystack:
        return "供货、安装调试、质量保障及售后服务方案 / 九、服务保障承诺"
    if any(token in haystack for token in ("节能", "环保")):
        return "7.7.3节能、环境标志产品证明材料"
    return "【待填写：对应章节/材料】"


def _suggest_zb_detailed_response_note(item_name: str, rule: str = "") -> str:
    """生成建议ZB 格式详细响应备注。"""
    haystack = _clean_text(f"{item_name} {rule}")
    if any(token in haystack for token in ("技术规格", "技术参数", "技术要求", "响应程度")):
        return "按格式8逐条响应技术参数，并在格式9补齐证明材料页码。"
    if any(token in haystack for token in ("包装运输", "配货单", "运输方案")):
        return "对照包装、运输、交接、配货单样本逐项说明满足情况。"
    if any(token in haystack for token in ("售后", "伴随服务")):
        return "结合售后承诺书、售后机构、响应时效、培训方案说明满足情况。"
    if any(token in haystack for token in ("质量保障", "验收")):
        return "围绕交货进度、安装调试、验收资料、质量控制措施进行对应说明。"
    if "服务保障承诺" in haystack:
        return "逐条列明高于或满足采购要求的服务承诺。"
    if "业绩" in haystack:
        return "列示业绩名称、签订时间、合同金额，并对应后附证明材料。"
    return "【待填写：如何满足该评分项】"


def _suggest_zb_qualification_response(item_name: str, requirement: str = "") -> str:
    """生成建议ZB 格式资格审查响应。"""
    haystack = _clean_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("营业执照", "境内注册")):
        return "格式7.投标人一般情况表后附营业执照复印件"
    if any(token in haystack for token in ("授权委托书", "授权书")):
        return "四、法定代表人授权书；格式5-1/5-2身份证复印件"
    if any(token in haystack for token in ("财务", "资信证明", "商业信誉")):
        return "格式7后附财务审计报告或银行资信证明"
    if any(token in haystack for token in ("设备和专业技术能力", "履行合同所必需")):
        return "格式7后附履约能力书面声明或相关证明材料"
    if any(token in haystack for token in ("社保", "纳税")):
        return "格式7后附纳税证明及社会保障资金缴纳证明"
    if any(token in haystack for token in ("重大违法记录", "声明书②")):
        return "7.5参加本政府采购项目前3年内无重大违法记录声明函"
    if any(token in haystack for token in ("信用中国", "税收违法黑名单", "声明书③")):
        return "7.6无失信行为和税收违法黑名单声明函及查询截图"
    if any(token in haystack for token in ("控股", "管理关系", "声明书④")):
        return "7.8与投标人无关联关系书面声明函"
    if any(token in haystack for token in ("整体设计", "监理", "检测", "声明书⑤")):
        return "7.9前期工作未提供过服务声明函"
    if any(token in haystack for token in ("医疗器械", "特定资格")):
        return "7.10.1/7.10.2特定资格证明文件"
    return "格式7及后附资格证明材料"


def _suggest_zb_compliance_response(item_name: str, requirement: str = "") -> str:
    """生成建议ZB 格式符合性审查响应。"""
    haystack = _clean_text(f"{item_name} {requirement}")
    if "投标人名称" in haystack:
        return "格式1投标函；格式7投标人一般情况表"
    if any(token in haystack for token in ("签字盖章", "格式")):
        return "全册投标文件签字盖章页；格式1-格式9及相关声明函"
    if any(token in haystack for token in ("报价唯一", "采购预算")):
        return "二、开标一览表；三、投标分项报价表"
    if any(token in haystack for token in ("合同履行期限", "交货")):
        return "二、开标一览表；八、供货、安装调试、质量保障及售后服务方案"
    if "投标有效期" in haystack:
        return "格式1投标函"
    if "投标保证金" in haystack:
        return "格式4投标保证金说明函及投标保证金凭证"
    if "采购需求" in haystack:
        return "格式8采购需求响应及偏离表；格式9技术支持资料"
    return "对应章节及后附证明材料"

def _build_zb_detailed_review_section(tender, tender_raw: str = "", package_index: int = 1) -> str:
    """构建ZB 格式详细评审章节。"""
    tpl = getattr(tender, "detailed_review_table", None)
    headers, tpl_rows = _tpl_rows_with_headers(tpl)

    if headers and tpl_rows:
        if _same_headers(headers, ["序号", "内容", "评分因素分项", "评审标准", "投标文件对应页码"]):
            rows = [[row[0], row[1], row[2], row[3], "【待填写：页码】"] for row in tpl_rows]
            return "详细评审响应对照表\n" + _render_table_with_headers(headers, rows)

        if _same_headers(headers, ["序号", "评分项目", "评审标准", "响应内容对应位置", "响应说明", "页码"]):
            rows = [
                [
                    row[0],
                    row[1],
                    row[2],
                    _suggest_zb_detailed_response_location(row[1], row[2]),
                    _suggest_zb_detailed_response_note(row[1], row[2]),
                    "【待填写：页码】",
                ]
                for row in tpl_rows
            ]
            return "详细评审响应对照表\n" + _render_table_with_headers(headers, rows)

    rows_data = [
        ["1", "价格", "评标价格（30分）", "小型和微型企业产品的投标报价在计算报价得分时, 投标报价下浮10%进入报价分计算。(需提供声明函加盖公章并符合相关规定）（小、微企业须按照财库﹝2020﹞46号和财库〔2022〕19号文件的规定提供加盖公章的《中小企业声明函》）\n\n报价得分=（评标基准价/投标报价）*30*100%（即满足招标文件要求且最终报价最低的为评标基准价，其价格分为满分）"],
        ["2", "商务", "业绩（2分）", "近三年（2022年至今，投标截止日为期，以签订时间为准）类似项目业绩，每有一项得1分，满分2分。（投标文件内附合同或中标通知书复印件并加盖公章）"],
        ["3", "技术", "对招标文件技术规格要求的响应程度（29分）", "投标文件技术规格响应全部满足招标文件技术要求的得29分，其中有1项重要条款（“▲”号条款）不满足的扣2分；有1项一般条款（非★条款或者“▲”号条款）不满足的，扣1分。采购需求中标注“★”号技术参数为实质性条款，必须逐条进行响应，任何一条不满足将导致废标。"],
        ["4", "技术", "包装运输方案（12分）", "运输方案完整、全面，包装严密、防水，外层包装标记清晰、详细，并配有详细配货单（提供配货单样本）的得12分；运输方案较为完整、全面，包装较为严密，防水，外层包装标记简单，配有相应的配货单但内容简单的得6分；运输方案一般，包装简单、不能防水，外层包装没有标记，配有相应的配货单但内容不全或未配有配货单的得2分；未提供包装运输方案的不得分。"],
        ["5", "技术", "售后及其他伴随服务（8分）", "根据投标人针对本项目提供的售后及其他伴随服务方案进行综合评比。"],
        ["6", "技术", "质量保障措施及方案（12分）", "结合本项目具体情况，拟定质量保障措施及方案，包括但不限于交货进度、安装调试、履约验收的相关内容。根据各投标人提供方案的完整性、合理性、可行性，横向比较。"],
        ["7", "技术", "服务保障承诺（6分）", "对各投标人针对本项目提供的服务保障承诺进行综合评比，各项服务保障承诺满足或高于本项目要求且切实可行，每提供一条得1分，满分6分。"],
        ["8", "节能环保", "政府采购节约能源、环境保护评分（1分）", "政府采购的强制采购产品除外：（1）节能产品认证证书得0.5分；（2）环境标志产品认证证书得0.5分。"],
    ]

    headers = ["序号", "评分项目", "评审标准", "响应内容对应位置", "响应说明", "页码"]
    rows = []
    for i, r in enumerate(rows_data, start=1):
        review_item = f"{r[1]} 部分 / {r[2]}"
        rows.append([
            str(i),
            review_item,
            r[3],
            _suggest_zb_detailed_response_location(review_item, r[3]),
            _suggest_zb_detailed_response_note(review_item, r[3]),
            "【待填写：页码】",
        ])
    return "详细评审响应对照表\n" + _render_table_with_headers(headers, rows)

def _build_zb_itemized_quote_table(tender, packages, tender_raw: str) -> str:
    """构建ZB 格式itemized报价表格。"""
    headers = ["包号", "货物名称", "数量", "单价（元）", "总价（元）", "备注"]
    rows: list[list[str]] = []

    for pkg in packages:
        rows.append([
            str(pkg.package_id),
            pkg.item_name,
            _extract_package_quantity(pkg, tender_raw),
            "【待填写】",
            "【待填写】",
            "【待填写】",
        ])

    return (
        f"项目名称：{tender.project_name}\n"
        f"项目编号：{tender.project_number}\n\n"
        f"{_md_table(headers, rows)}\n\n"
        "说明：如招标文件第六章有更细分项，应继续按原格式补列，不得删列。\n"
        "供应商全称：【待填写：投标人名称】\n"
        "日期：【待填写：年 月 日】"
    )


def _build_zb_business_deviation_table(tender, packages, tender_raw: str) -> str:
    """构建ZB 格式business偏离表。"""
    headers = ["序号", "商务条款", "招标文件要求", "响应情况", "偏离说明"]
    ct = getattr(tender, "commercial_terms", None)

    rows = [
        ["1", "交货期", "；".join(f"包{p.package_id}：{_extract_delivery_time(p, tender_raw)}" for p in packages), "按招标文件约定执行", "无偏离"],
        ["2", "交货地点", "；".join(f"包{p.package_id}：{_extract_delivery_place(p, tender_raw)}" for p in packages), "按招标文件约定执行", "无偏离"],
        ["3", "投标有效期", getattr(ct, "validity_period", "") or "按招标文件要求执行", "按招标文件要求执行", "无偏离"],
        ["4", "付款方式", getattr(ct, "payment_method", "") or "按招标文件及合同约定执行", "按招标文件及合同约定执行", "无偏离"],
        ["5", "质保期", getattr(ct, "warranty_period", "") or "按招标文件要求执行", "按招标文件要求执行", "无偏离"],
        ["6", "履约保证金", getattr(ct, "performance_bond", "") or "按招标文件要求执行", "按招标文件要求执行", "无偏离"],
    ]

    return _md_table(headers, rows)


def _build_zb_bid_bond_letter(tender) -> str:
    """构建ZB 格式投标bondletter。"""
    agency_name = (
        getattr(tender, "agency", "")
        or getattr(tender, "purchaser", "")
        or "采购代理机构"
    )
    project_no = getattr(tender, "project_number", "") or "【待填写】"

    return f"""
格式 4.投标保证金说明函（格式）

致：{agency_name}
招标编号：{project_no}

1、投标保证金金额（大写）：【待填写】元，以【待填写：电汇/保函等】方式支付。
2、在担保期内，采购人或采购代理机构可依据招标文件关于投标保证金没收情形的规定处理本保证金。
3、后附投标保证金递交凭据及基本账户开户许可证（或《基本存款账户信息》）复印件并加盖公章。

投标人名称：                       （加盖单位公章）
法定代表人或授权代表：              （签字）
日期：                              
""".strip()

def _tpl_header_titles(tpl) -> list[str]:
    """返回模板中的表头标题列表。"""
    cols = getattr(tpl, "columns", None) or []
    out = []
    for c in cols:
        title = str(getattr(c, "title", "") or "").strip()
        if title:
            out.append(title)
    return out


def _tpl_rows_with_headers(tpl) -> tuple[list[str], list[list[str]]]:
    """返回带表头的行。"""
    headers = _tpl_header_titles(tpl)
    columns = list(getattr(tpl, "columns", None) or [])
    raw_rows = list(getattr(tpl, "rows", None) or [])
    if not headers or not columns or not raw_rows:
        return headers, []

    normalized_headers = [_norm_title(item) for item in headers]
    rows: list[list[str]] = []

    for row in raw_rows:
        values: list[str] = []
        cells = getattr(row, "cells", {}) or {}

        for idx, col in enumerate(columns):
            value = cells.get(col.key, "")
            if idx == 0 and not value:
                value = getattr(row, "seq", "")
            values.append(_clean_text(value))

        if not any(values):
            continue

        current = [_norm_title(item) for item in values[: len(headers)]]
        if current == normalized_headers:
            continue

        if len(values) < len(headers):
            values += [""] * (len(headers) - len(values))
        elif len(values) > len(headers):
            values = values[:len(headers)]

        rows.append(values)

    return headers, rows


def _extract_table_headers_from_raw_block(raw_block: str) -> list[str]:
    """提取raw文本块中的表格表头。"""
    for line in (raw_block or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        cells = [_clean_text(cell) for cell in cells if _clean_text(cell)]
        if len(cells) >= 2:
            return cells
    return []


def _norm_title(s: str) -> str:
    """规范化标题。"""
    return "".join(str(s or "").split())


def _same_headers(actual: list[str], expected: list[str]) -> bool:
    """判断两组表头是否完全一致。"""
    if len(actual) != len(expected):
        return False
    return [_norm_title(x) for x in actual] == [_norm_title(x) for x in expected]


def _render_table_with_headers(headers: list[str], rows: list[list[str]]) -> str:
    """渲染带表头的表格。"""
    return _md_table(headers, rows)




def _build_zb_general_info_table(tender) -> str:
    """构建ZB 格式generalinfo表格。"""
    headers = ["项目", "内容"]
    rows = [
        ["投标人全称", "【待填写】"],
        ["供应商性质", "【待填写，并附相关证明材料】"],
        ["法定代表人或负责人姓名", "【待填写】"],
        ["联系人、联系方式、办公地址", "【待填写】"],
        ["基本开户银行名称", "【待填写】"],
        ["良好的商业信誉和健全的财务会计制度", "【待填写，并附财务审计报告或资信证明】"],
        ["具有履行合同所必需的设备和专业技术能力", "【待填写，并附证明材料或说明】"],
        ["依法缴纳税收和社会保障资金", "【待填写，并附证明材料】"],
        ["参加政府采购活动前3年内在经营活动中没有重大违法记录", "【待填写，并附书面声明】"],
    ]
    return "\n".join([
        "格式 7.投标人一般情况表（格式）",
        f"招标编号：{tender.project_number or '【待填写】'}",
        _md_table(headers, rows),
        "注：表后须按招标文件要求附营业执照、财务/资信、纳税社保、信用声明及特定资格证明文件。",
    ])


def _build_zb_performance_table(tender) -> str:
    """构建ZB 格式performance表格。"""
    headers = ["序号", "项目名称", "用户单位", "供货内容", "签订时间", "合同金额", "证明材料页码"]
    rows = [["1", "【待填写】", "【待填写】", "【待填写】", "【待填写】", "【待填写】", "【待填写】"]]
    return "\n".join([
        "7.11类似项目业绩表（格式自拟）",
        _md_table(headers, rows),
        "注：表后须附业绩证明文件（中标通知书或合同复印件），否则不予认可。",
    ])


def _build_zb_manufacturer_authorization(tender, packages) -> str:
    """构建ZB 格式厂家authorization。"""
    goods = "、".join(getattr(p, "item_name", "") or "【待填写：货物名称】" for p in (packages or [])) or "【待填写：货物名称】"
    return f"""
7.12制造商授权书（格式自拟）

致：{tender.purchaser or '采购人'}

作为【待填写：制造商名称】，现授权【待填写：投标人名称】参加 {tender.project_name or '【待填写：项目名称】'}
（项目编号：{tender.project_number or '【待填写】'}）投标，就 {goods} 提供投标、供货、安装调试、售后服务等相关支持。

制造商名称（盖章）：【待填写】
法定代表人或授权代表：【待填写】
日期：【待填写】
""".strip()


def _build_zb_other_technical_docs_section(
    tender,
    packages,
    tender_raw: str,
    *,
    products: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, Any] | None = None,
) -> str:
    """构建ZB 格式other技术docs章节。"""
    pkg = packages[0] if packages else None
    goods = getattr(pkg, "item_name", "【待填写：货物名称】") if pkg else "【待填写：货物名称】"
    if pkg is None:
        return "\n".join([
            "格式 9.第五章规定的证明文件和其他技术方案",
            f"项目名称：{tender.project_name or '【待填写：项目名称】'}",
            f"项目编号：{tender.project_number or '【待填写】'}",
            "对应货物：请根据实际投标包件补充",
            "",
            "请补充产品彩页、配置清单、安装调试方案、培训方案、验收资料清单及合法来源证明。",
        ])

    product = _product_for_package(pkg.package_id, products)
    product_profile = _profile_payload_for_package(pkg.package_id, product_profiles)
    technical_doc_rows = _build_zb_technical_doc_index_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
    )
    supporting_rows = _build_zb_supporting_material_rows(
        pkg,
        tender_raw,
        product=product,
        product_profile=product_profile,
        normalized_result=normalized_result,
    )
    service_rows = _build_zb_service_material_rows(tender_raw)
    authorization_rows = _build_zb_authorization_rows(
        pkg,
        tender_raw,
        product=product,
        product_profile=product_profile,
    )

    return "\n".join([
        "格式 9.第五章规定的证明文件和其他技术方案",
        f"项目名称：{tender.project_name or '【待填写：项目名称】'}",
        f"项目编号：{tender.project_number or '【待填写】'}",
        f"对应货物：{goods}",
        "",
        "技术支持资料通常包括产品彩页/技术白皮书/官网参数页，并应与格式8逐条对应。",
        "",
        "一、技术支持资料与技术响应对应索引",
        _md_table(
            ["序号", "对应条款", "采购需求摘要", "建议提供资料/已绑定证据", "页码/位置说明"],
            technical_doc_rows,
        ),
        "",
        "二、配置清单、随机附件、备件及专用工具清单",
        _md_table(
            ["序号", "类别", "名称", "数量", "说明"],
            supporting_rows,
        ),
        "",
        "三、安装调试、培训与验收资料清单",
        _md_table(
            ["序号", "资料/方案", "形成时点", "关键内容"],
            service_rows,
        ),
        "",
        "四、软件/硬件合法来源及授权证明",
        _md_table(
            ["序号", "证明事项", "建议文件", "适用说明"],
            authorization_rows,
        ),
        "",
        "说明：技术支持资料应与格式8逐条对应；最终成稿时请将实际证据页码、型号对应关系和授权文件编号补齐。",
    ])

def _build_zb_section_content(
    title: str,
    raw_block: str,
    tender,
    tender_raw: str,
    packages: list,
    *,
    products: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, Any] | None = None,
) -> str:
    """构建ZB 格式章节内容。"""
    raw = (raw_block or "").strip()

    if _title_has_any(title, ("资格性审查",)):
        return _build_zb_qualification_review_section(tender, tender_raw)

    if _title_has_any(title, ("符合性审查", "符合性检查", "符合性审核")):
        return _build_zb_compliance_review_section(tender, tender_raw)

    if _title_has_any(title, ("详细评审", "评分因素", "评分标准", "评审索引")):
        return _build_zb_detailed_review_section(tender, tender_raw)

    if _title_has_any(title, ("无效投标", "否决投标", "废标情形", "无效情形")):
        return _build_zb_invalid_bid_checklist(tender, tender_raw)

    if _title_has_any(title, ("第五章", "其他技术响应文件", "其他技术方案", "技术支持资料")):
        return _build_zb_other_technical_docs_section(
            tender,
            packages,
            tender_raw,
            products=products,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
        )

    if (
        _title_has_any(title, ("供货", "安装", "调试", "质量保障", "服务方案", "技术方案", "保障承诺"))
        or (_title_has_any(title, ("售后服务",)) and not _title_has_any(title, ("售后服务承诺书",)))
    ):
        content = _build_zb_service_section(
            tender,
            tender_raw,
            packages=packages,
            products=products,
            product_profiles=product_profiles,
            normalized_result=normalized_result,
        )
        return _normalize_procurement_terms_for_zb(content)

    if _title_has_any(title, ("技术要求响应", "技术响应", "技术偏离", "采购需求响应")):
        pkg = packages[0] if packages else None
        if pkg is None:
            return "【待补：技术要求响应及偏离表】"
        return _build_zb_technical_response_table(
            tender,
            pkg,
            tender_raw,
            raw_block=raw,
            product=_product_for_package(pkg.package_id, products),
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profile=_profile_payload_for_package(pkg.package_id, product_profiles),
        )

    # 第六章原格式优先：只有标题本身像“真实模板标题”，且未命中特殊生成章节时，才直接按原模板落。
    if raw and _looks_like_safe_zb_template_title(title):
        return _zb_render_template_content(title, raw, tender, packages, tender_raw)

    if _title_has_any(title, ("投标保证金说明函",)):
        return _build_zb_bid_bond_letter(tender)

    if _title_has_any(title, ("制造商授权书",)):
        return _build_zb_manufacturer_authorization(tender, packages)

    if _title_has_any(title, ("投标人一般情况表",)):
        return _build_zb_general_info_table(tender)

    if _title_has_any(title, ("类似项目业绩表",)):
        return _build_zb_performance_table(tender)

    if _title_has_any(title, ("投标函", "投标书", "报价书")):
        return f"""
致：{tender.purchaser or "采购人"}

根据贵方 {tender.project_name}（项目编号：{tender.project_number}）招标文件，我方正式提交投标文件，并声明如下：

1. 我方已详细审阅全部招标文件及有关澄清、修改文件，愿意按照招标文件要求参加投标。
2. 我方承诺对招标文件提出的商务、技术、服务及合同条款作出实质性响应。
3. 我方保证所提交的全部资料真实、准确、完整。
4. 如我方中标，将严格按照招标文件、投标文件及合同约定履行义务。

供应商全称：【待填写：投标人名称】
法定代表人或授权代表：【待填写】
日期：【待填写：年 月 日】
""".strip()

    if _title_has_any(title, ("开标一览表", "报价一览表")):
        return _build_zb_quote_summary_table(tender, packages, tender_raw)

    if _title_has_any(title, ("分项报价表", "投标分项报价表")):
        return _build_zb_itemized_quote_table(tender, packages, tender_raw)

    if _title_has_any(title, ("授权书", "法定代表人")):
        return f"""
法定代表人（单位负责人）授权书

兹授权【待填写：授权代表姓名】作为我方合法代表，参加 {tender.project_name}
（项目编号：{tender.project_number}）的投标活动，并全权处理投标、澄清、签约等相关事宜。

法定代表人/单位负责人签字或盖章：【待填写】
授权代表签字：【待填写】
供应商全称（公章）：【待填写：投标人名称】
日期：【待填写：年 月 日】
""".strip()

    if _title_has_any(title, ("资格证明", "资格文件", "资格材料")):
        if raw and _looks_like_safe_zb_template_title(title):
            return raw + "\n\n【请逐项补齐对应证明文件，并标注页码。】"
        return "【待按招标文件要求逐项提供资格证明文件、资信材料、许可证/备案凭证、财务/纳税/社保证明等。】"

    if _title_has_any(title, ("商务条款", "商务偏离", "商务响应")):
        return _build_zb_business_deviation_table(tender, packages, tender_raw)

    # 兜底时严禁把普通正文条款直接灌入成品文档。
    if raw and _looks_like_safe_zb_template_title(title):
        return _zb_render_template_content(title, raw, tender, packages, tender_raw)

    return "【待按招标文件第六章原格式填写本章节内容】"

def _clean_invalid_reason_text(text: str) -> str:
    """清理无效reason文本。"""
    cleaned = _clean_text(text or "")
    cleaned = re.sub(r"^\d+\.\d+\s*", "", cleaned)
    cleaned = re.sub(r"^[（(]?\d+[）)]\s*", "", cleaned)
    return cleaned.strip("；;。 ")


def _normalize_invalid_item_key(text: str) -> str:
    """归一化无效项键。"""
    s = re.sub(r"\s+", "", text or "")
    s = re.sub(r"[，,。；;、“”\"'（）()《》<>【】\[\]]", "", s)
    s = s.replace("无效响应", "无效投标")
    s = s.replace("视为无效投标", "无效投标")
    s = s.replace("按无效投标处理", "无效投标")
    s = s.replace("投标无效", "无效投标")
    return s


def _append_invalid_item(items: list[str], item: str | None) -> None:
    """追加无效项。"""
    if not item:
        return
    key = _normalize_invalid_item_key(item)
    if not key:
        return
    for existing in items:
        existing_key = _normalize_invalid_item_key(existing)
        if key == existing_key or key in existing_key or existing_key in key:
            return
    items.append(item)


def _standardize_invalid_reason(text: str) -> str | None:
    """规范化无效投标原因表述。"""
    s = _clean_invalid_reason_text(text)
    if not s:
        return None
    s = s.replace("|", " ")
    s = re.sub(r"\s+", " ", s).strip("；;。 ")

    replacements = [
        (r"不符合上述合格投标人资格要求", "不具备招标文件规定的合格投标人资格要求的"),
        (r"没有根据投标人须知第\s*15\.1.*?投标保证金", "未按招标文件要求提交投标保证金的"),
        (r"投标有效期不满足(?:招标文件)?要求", "投标有效期不满足招标文件要求的"),
        (r"未按上述要求提供进口产品逐级授权", "未按要求提供进口产品逐级授权的"),
        (r"提供了选择方案或选择报价", "提供了选择方案或选择报价（包括交叉折扣）的"),
        (r"提交了转包或分包要求", "提交了转包或分包要求的"),
        (r"以可调整价格投标报价", "以可调整价格投标报价的"),
        (r"投标文件技术规格响应最低得分为\s*0\s*分", "投标文件技术规格响应评分为0分的"),
        (r"最低得分为\s*0\s*分", "投标文件技术规格响应评分为0分的"),
        (r"投标文件未提供商务条款响应及偏离表和技术要求响应及偏离表", "投标文件未提供商务条款响应及偏离表和技术要求响应及偏离表的"),
        (r"投标报价超过分包预算金额或最高限价", "投标报价超过分包预算金额或最高限价的"),
        (r"若未提供，将导致投标被视为无效投标", "本采购内容涉及政府强制采购节能产品而未按要求提供证明材料的"),
        (r"报价明显低于其他通过符合性审查投标人的报价.*?不能证明其报价合理性", "投标报价明显低于其他通过符合性审查投标人的报价且不能证明其合理性的"),
        (r"投标人不能证明其报价合理性", "投标报价明显低于其他通过符合性审查投标人的报价且不能证明其合理性的"),
        (r"投标文件不完整导致不能实质性响应招标文件要求", "投标文件不完整或者未对招标文件作出实质性、完整响应的"),
        (r"不具备招标文件中规定的合格货物及其相关服务要求", "货物及相关服务来源不符合招标文件规定要求的"),
        (r"不符合招标文件第四章、第五章所列带[“\"]?★[”\"]?号条款要求", "采购需求中标注“★”号的实质性技术参数任一条不满足的"),
        (r"任何一条不满足将导致废标", "采购需求中标注“★”号的实质性技术参数任一条不满足的"),
        (r"技术偏离表中仅作出应答而未提供要求的有效技术支持资料（或证明材料）", "技术偏离表未按要求提供有效技术支持资料（或证明材料）的"),
        (r"投标没有对招标文件在各方面都做出实质性响应", "投标文件不完整或者未对招标文件作出实质性、完整响应的"),
        (r"投标文件须对招标文件中的内容做出实质性和完整的响应", "投标文件不完整或者未对招标文件作出实质性、完整响应的"),
        (r"投标及合同中提供的所有货物及其有关服务的原产地", "货物及相关服务来源不符合招标文件规定要求的"),
        (r"恶意地提供错误事实", "恶意提供货物合法来源或授权错误事实的"),
        (r"影响或试图影响采购人.*?评标.*?授予合同", "投标人在开标后到中标结果确定期间，影响或试图影响采购人、采购代理机构、评标委员会工作的"),
        (r"投标人在本项目的竞争中有腐败或欺诈行为", "投标人在本项目的竞争中有腐败或欺诈行为的"),
        (r"腐败或欺诈行为.*?被拒绝", "投标人在本项目的竞争中有腐败或欺诈行为的"),
        (r"不符合法律、法规和招标文件.*?其他无效投标情形", "不符合法律、法规和招标文件规定的其他无效投标情形的"),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, s):
            s = replacement
            break
    else:
        s = re.sub(
            r"(?:其投标将被视为无效投标被拒绝|其投标无效|投标将被视为无效投标而予以拒绝|投标将被视为无效投标被拒绝|被视为无效投标而予以拒绝|被视为无效投标被拒绝|将按照无效投标处理，予以拒绝|按无效投标处理，予以拒绝|将按照无效响应处理，予以拒绝|按无效响应处理，予以拒绝|按无效投标处理|视为无效投标|导致废标)",
            "",
            s,
        ).strip("，,；;。 ")

    if not s:
        return None

    if any(token in s for token in ("招标文件和本", "法律、法规和招标文件和本")):
        return None

    if len(s) > 48 and not any(token in s for token in ("未", "不", "不能", "超过", "虚假", "恶意", "选择", "转包", "分包", "缺少")):
        return None

    return f"{s}，按无效投标处理。"


def _extract_zb_invalid_items(text: str) -> list[str]:
    """提取ZB 格式无效项。"""
    source = text or ""
    items: list[str] = []

    block_patterns = [
        r"26\.5[^\n。；]{0,120}(?:无效投标情况|无效投标处理)[：:]?(?P<body>[\s\S]{0,2200}?)(?=(?:\|\s*\d+\s*\||26\.6\b|27\.\d\b|投标人须知|三、\s*投标文件的编制|第六章|$))",
        r"本项目规定的其他无效投标情况[：:]?(?P<body>[\s\S]{0,2200}?)(?=(?:\|\s*\d+\s*\||26\.6\b|27\.\d\b|投标人须知|三、\s*投标文件的编制|第六章|$))",
    ]
    enum_pat = re.compile(r"[（(]\s*\d+\s*[）)]\s*(.+?)(?=(?:[（(]\s*\d+\s*[）)])|$)", re.S)

    for pattern in block_patterns:
        for match in re.finditer(pattern, source):
            block = match.group("body")
            for enum_match in enum_pat.finditer(block):
                _append_invalid_item(items, _standardize_invalid_reason(enum_match.group(1)))

    direct_patterns = [
        r"2\.2[^。；\n]{0,160}(?:无效投标|被拒绝)",
        r"3\.2[\s\S]{0,260}?不符合这些来源要求的货物和服务[\s\S]{0,40}?(?:无效投标|被拒绝)",
        r"3\.5[\s\S]{0,260}?(?:恶意地提供错误事实|其投标将被视为无效投标|无效投标|被拒绝)",
        r"8\.3[^。；\n]{0,220}(?:无效标|无效投标|被拒绝)",
        r"15\.3[^。；\n]{0,220}(?:无效投标|予以拒绝)",
        r"16\.1[^。；\n]{0,220}(?:无效投标|予以拒绝)",
        r"26\.6[\s\S]{0,260}?投标人不能证明其报价合理性[\s\S]{0,40}?(?:无效投标处理|无效投标)",
        r"报价明显低于其他通过符合性审查投标人的报价[\s\S]{0,160}?投标人不能证明其报价合理性[\s\S]{0,40}?(?:无效投标处理|无效投标)",
        r"未按上述要求提供进口产品逐级授权[^。；\n]{0,120}(?:其投标无效|无效投标)",
        r"最低得分为\s*0\s*分[^。；\n]{0,80}(?:无效响应处理|予以拒绝)",
        r"任何一条不满足将导致废标",
        r"技术偏离表中仅作出应答而未提供要求的有效技术支持资料（或证明材料）[^。；\n]{0,120}(?:否决|废标)",
        r"29\.2[^。；\n]{0,240}(?:无效投标|被拒绝)",
        r"腐败或欺诈行为[^。；\n]{0,120}(?:无效投标|被拒绝)",
    ]
    for pattern in direct_patterns:
        for match in re.finditer(pattern, source):
            _append_invalid_item(items, _standardize_invalid_reason(match.group(0)))

    if not items:
        fallback = [
            "不具备招标文件规定的合格投标人资格要求的，按无效投标处理。",
            "未按招标文件要求提交投标保证金的，按无效投标处理。",
            "投标有效期不满足招标文件要求的，按无效投标处理。",
            "投标报价明显低于其他通过符合性审查投标人的报价且不能证明其合理性的，按无效投标处理。",
        ]
        for item in fallback:
            _append_invalid_item(items, item)

    return items


def _extract_zb_explicit_invalid_items(text: str) -> list[str]:
    """优先提取 26.5/“其他无效投标情况”中明示列出的无效条款。"""
    source = text or ""
    items: list[str] = []

    block_patterns = [
        r"26\.5[^\n。；]{0,120}(?:无效投标情况|无效投标处理|其他无效投标情况)[：:]?(?P<body>[\s\S]{0,1800}?)(?=(?:\|\s*\d+\s*\||26\.6\b|27\.\d\b|投标人须知|第六章|$))",
        r"本项目规定的其他无效投标情况[：:]?(?P<body>[\s\S]{0,1800}?)(?=(?:\|\s*\d+\s*\||26\.6\b|27\.\d\b|投标人须知|第六章|$))",
    ]
    enum_pat = re.compile(r"[（(]\s*\d+\s*[）)]\s*(.+?)(?=(?:[（(]\s*\d+\s*[）)])|$)", re.S)

    for pattern in block_patterns:
        for match in re.finditer(pattern, source):
            block = match.group("body")
            for enum_match in enum_pat.finditer(block):
                _append_invalid_item(items, _standardize_invalid_reason(enum_match.group(1)))

    return items


def _build_zb_invalid_bid_checklist(tender, tender_raw: str = "") -> str:
    """构建ZB 格式无效投标checklist。"""
    text = tender_raw or getattr(tender, "source_text", "") or ""
    invalid_tpl = getattr(tender, "invalid_bid_table", None)
    template_block = getattr(invalid_tpl, "raw_block", "") or ""
    explicit_items = _extract_zb_explicit_invalid_items(template_block) or _extract_zb_explicit_invalid_items(text)
    if len(explicit_items) >= 5:
        items = explicit_items
    elif explicit_items:
        items = list(explicit_items)
        for item in _extract_zb_invalid_items(text):
            _append_invalid_item(items, item)
    else:
        items = _extract_zb_invalid_items(text)
    rows = [
        [str(i), item, "【待填写：符合/不符合】", "【待填写】"]
        for i, item in enumerate(items, start=1)
    ]
    return "无效投标情形自检表\n" + _md_table(
        ["序号", "无效投标情形", "自检结果", "备注"],
        rows,
    )

def _build_zb_compliance_review_section(tender, tender_raw: str = "", package_index: int = 1) -> str:
    """构建ZB 格式符合性审查章节。"""
    tpl = getattr(tender, "compliance_review_table", None)
    headers, tpl_rows = _tpl_rows_with_headers(tpl)

    if headers and tpl_rows:
        if _same_headers(headers, ["序号", "审查内容", "合格条件", "投标文件所在页码"]):
            rows = [[row[0], row[1], row[2], "【待填写：页码】"] for row in tpl_rows]
            return "符合性审查响应对照表\n" + _render_table_with_headers(headers, rows)

        if _same_headers(headers, ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]):
            rows = [
                [
                    row[0],
                    row[1],
                    row[2],
                    _suggest_zb_compliance_response(row[1], row[2]),
                    "【待填写：满足/不满足】",
                    "【待填写】",
                ]
                for row in tpl_rows
            ]
            return "符合性审查响应对照表\n" + _render_table_with_headers(headers, rows)

    rows_data = [
        ["1", "投标人名称", "与营业执照一致"],
        ["2", "投标文件签字盖章", "投标文件签字、盖章齐全"],
        ["3", "投标文件格式", "符合投标文件内容及格式要求"],
        ["4", "报价唯一", "只能有一个有效报价且不超过采购预算"],
        ["5", "合同履行期限", "合同签订后30日内交货"],
        ["6", "投标有效期", "90日历日"],
        ["7", "投标保证金", "满足招标文件要求"],
        ["8", "采购需求", "满足招标文件“第五章★条款”"],
        ["9", "其他", "满足招标文件的实质性内容"],
    ]

    headers = ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]
    rows = [
        [r[0], r[1], r[2], _suggest_zb_compliance_response(r[1], r[2]), "【待填写：满足/不满足】", "【待填写】"]
        for r in rows_data
    ]
    return "符合性审查响应对照表\n" + _render_table_with_headers(headers, rows)

def _build_zb_qualification_review_section(tender, tender_raw: str = "", package_index: int = 1) -> str:
    """构建ZB 格式资格审查章节。"""
    tpl = getattr(tender, "qualification_review_table", None)
    headers, tpl_rows = _tpl_rows_with_headers(tpl)

    if headers and tpl_rows:
        if _same_headers(headers, ["序号", "审查内容", "合格条件", "投标文件对应页码"]):
            rows = [[row[0], row[1], row[2], "【待填写：页码】"] for row in tpl_rows]
            return "资格性审查响应对照表\n" + _render_table_with_headers(headers, rows)

        if _same_headers(headers, ["序号", "审查内容", "合格条件", "投标文件所在页码"]):
            rows = [[row[0], row[1], row[2], "【待填写：页码】"] for row in tpl_rows]
            return "资格性审查响应对照表\n" + _render_table_with_headers(headers, rows)

        if _same_headers(headers, ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]):
            rows = [
                [
                    row[0],
                    row[1],
                    row[2],
                    _suggest_zb_qualification_response(row[1], row[2]),
                    "【待填写：满足/不满足】",
                    "【待填写】",
                ]
                for row in tpl_rows
            ]
            return "资格性审查响应对照表\n" + _render_table_with_headers(headers, rows)

    rows_data = [
        ["1", "营业执照", "投标人须在中华人民共和国境内注册，具备有效营业执照，并在人员、设备、资金等方面具有相应能力，提供营业执照复印件。"],
        ["2", "授权委托书", "提供由法定代表人及委托代理人签字并加盖公章的授权书。"],
        ["3", "良好的商业信誉和健全的财务会计制度", "提供近一年度（2024年）财务审计报告（经会计师事务所审计）或基本开户银行出具的开标日前三个月内资信证明。"],
        ["4", "声明书①", "具有履行合同所必需的设备和专业技术能力，附相关证明材料或书面声明。"],
        ["5", "社保及纳税证明", "提供近半年内任意连续三个月依法缴纳税收和社会保障资金的相关证明材料。"],
        ["6", "声明书②", "提供参与本采购活动前三年内在经营活动中没有重大违法记录的书面声明。"],
        ["7", "声明书③", "提供信用中国、中国政府采购网查询截图及书面声明。"],
        ["8", "声明书④", "提供单位负责人为同一人或存在控股、管理关系声明。"],
        ["9", "声明书⑤", "提供未为本采购项目提供整体设计、规范编制、项目管理、监理、检测等服务声明。"],
        ["10", "特定资格要求", "提供医疗器械生产/经营许可证或备案凭证、产品注册证或备案凭证等证明材料。"],
        ["11", "法律法规规定及招标文件规定的其他要求", "投标人符合法律、行政法规及招标文件规定的其他要求。"],
    ]

    headers = ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]
    rows = [
        [r[0], r[1], r[2], _suggest_zb_qualification_response(r[1], r[2]), "【待填写：满足/不满足】", "【待填写】"]
        for r in rows_data
    ]
    return "资格性审查响应对照表\n" + _render_table_with_headers(headers, rows)


def _build_zb_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
    *,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profiles: dict | None = None,
) -> list:
    """构建ZB 格式章节。"""
    packages = active_packages or tender.packages
    entries = _get_zb_template_entries(tender)

    existing_keys = {_normalized_title_key(title) for title, _ in entries}
    existing_titles_for_match = [title for title, _ in entries]

    need_format9 = not any(
        _title_matches_any(title, [
            "格式9.招标文件第五章采购需求规定的投标人需要提供的投标产品相关证明文件和其他技术方案",
            "格式9.第五章规定的证明文件和其他技术方案",
            "招标文件第五章采购需求规定的投标产品技术支持资料或证明材料",
        ])
        for title in existing_titles_for_match
    )

    for title in _zb_required_template_titles():
        if _title_matches_any(title, [
            "格式9.招标文件第五章采购需求规定的投标人需要提供的投标产品相关证明文件和其他技术方案",
            "格式9.第五章规定的证明文件和其他技术方案",
            "招标文件第五章采购需求规定的投标产品技术支持资料或证明材料",
        ]):
            if not need_format9:
                continue

        key = _normalized_title_key(title)
        if key not in existing_keys:
            entries.append((title, ""))
            existing_keys.add(key)

    existing_titles = [title for title, _ in entries]

    def _has_any(*keywords: str) -> bool:
        """判断是否存在any。"""
        return any(_title_has_any(title, tuple(keywords)) for title in existing_titles)

    sections: list[BidDocumentSection] = []

    prelude_titles: list[str] = []
    if not _has_any("资格性审查", "资格性检查", "资格审查", "资格性检查索引"):
        prelude_titles.append("资格性审查响应对照表")
    if not _has_any("符合性审查", "符合性检查", "符合性审核", "符合性检查索引"):
        prelude_titles.append("符合性审查响应对照表")
    if not _has_any("详细评审", "评分因素", "评分标准", "评分办法", "评分办法索引"):
        prelude_titles.append("详细评审响应对照表")

    for title in prelude_titles:
        sections.append(
            BidDocumentSection(
                section_title=title,
                content=_build_zb_section_content(
                    title,
                    "",
                    tender,
                    tender_raw,
                    packages,
                    products=products,
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profiles=product_profiles,
                ),
            )
        )

    for title, raw_block in entries:
        sections.append(
            BidDocumentSection(
                section_title=title,
                content=_build_zb_section_content(
                    title,
                    raw_block,
                    tender,
                    tender_raw,
                    packages,
                    products=products,
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profiles=product_profiles,
                ),
            )
        )

    trailing_titles: list[str] = []
    if not _has_any("供货", "安装", "调试", "质量保障", "服务方案", "包装运输方案"):
        trailing_titles.append("供货、安装调试、质量保障及售后服务方案")
    if not _has_any("无效投标", "否决投标", "废标情形", "无效情形", "其他无效投标情况"):
        trailing_titles.append("无效投标情形自检表")

    for title in trailing_titles:
        sections.append(
            BidDocumentSection(
                section_title=title,
                content=_build_zb_section_content(
                    title,
                    "",
                    tender,
                    tender_raw,
                    packages,
                    products=products,
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profiles=product_profiles,
                ),
            )
        )

    return sections

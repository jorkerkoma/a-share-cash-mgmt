import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple


# 常见银行简称 -> 标准全称映射
BANK_ALIAS_MAP = {
    "工行": "中国工商银行",
    "工商银行": "中国工商银行",
    "农行": "中国农业银行",
    "农业银行": "中国农业银行",
    "建行": "中国建设银行",
    "建设银行": "中国建设银行",
    "中行": "中国银行",
    "交行": "交通银行",
    "邮储": "中国邮政储蓄银行",
    "邮储银行": "中国邮政储蓄银行",
    "邮政储蓄银行": "中国邮政储蓄银行",
    "招行": "招商银行",
    "浦发": "上海浦东发展银行",
    "浦发银行": "上海浦东发展银行",
    "浦东发展银行": "上海浦东发展银行",
    "兴业": "兴业银行",
    "中信": "中信银行",
    "光大": "中国光大银行",
    "光大银行": "中国光大银行",
    "民生": "中国民生银行",
    "民生银行": "中国民生银行",
    "华夏": "华夏银行",
    "广发": "广发银行",
    "平安": "平安银行",
    "浙商": "浙商银行",
    "渤海": "渤海银行",
    "恒丰": "恒丰银行",
    "汇丰": "汇丰银行",
    "渣打": "渣打银行",
    "花旗": "花旗银行",
    "东亚": "东亚银行",
    "星展": "星展银行",
    "国开行": "国家开发银行",
    "农发行": "中国农业发展银行",
    "进出口银行": "中国进出口银行",
    "沪农商行": "上海农村商业银行",
    "苏农银行": "江苏苏州农村商业银行",
    "常熟银行": "江苏常熟农村商业银行",
    "张家港行": "江苏张家港农村商业银行",
    "江阴银行": "江苏江阴农村商业银行",
    "无锡银行": "无锡农村商业银行",
    "紫金银行": "江苏紫金农村商业银行",
    "瑞丰银行": "浙江绍兴瑞丰农村商业银行",
    "青农商行": "青岛农村商业银行",
    "渝农商行": "重庆农村商业银行",
}

BROKER_KEYWORDS = (
    "证券", "中金公司", "中国国际金融", "东方财富", "申万宏源", "国泰君安", "国泰海通",
    "海通", "广发证券", "华泰证券", "招商证券", "国信证券", "银河证券", "中信建投",
    "中泰证券", "光大证券", "兴业证券", "方正证券", "长江证券", "安信证券", "国投证券",
    "财通证券", "浙商证券", "东吴证券", "东兴证券", "东北证券", "西南证券", "西部证券",
    "华西证券", "国元证券", "国海证券", "国金证券", "国联证券", "国联民生", "南京证券",
    "华安证券", "第一创业", "太平洋证券", "长城证券", "山西证券", "红塔证券", "中原证券",
    "财达证券", "首创证券", "华创证券", "华福证券", "财信证券", "万联证券", "中航证券",
    "信达证券", "华鑫证券", "东莞证券", "开源证券", "世纪证券", "中邮证券", "中银国际",
    "瑞银证券", "高盛高华", "摩根士丹利",
)

WEALTH_SUB_KEYWORDS = (
    "理财有限责任公司", "理财有限公司", "招银理财", "工银理财", "建信理财", "农银理财",
    "中银理财", "交银理财", "中邮理财", "兴银理财", "信银理财", "光大理财", "浦银理财",
    "平安理财", "民生理财", "华夏理财", "广银理财", "杭银理财", "宁银理财", "苏银理财",
    "南银理财", "上银理财", "青银理财", "徽银理财", "渝农商理财", "北银理财", "汇华理财",
    "贝莱德建信理财", "施罗德交银理财", "法巴农银理财", "高盛工银理财",
)

FLEXIBLE_KEYWORDS = (
    "随时", "无固定", "开放式", "开放", "活期", "T+0", "T+1", "t+0", "t+1",
    "工作日可赎", "每日可赎", "可转让", "按日", "滚动", "不定期", "通知存款",
    "协定存款", "无期限", "永久", "长期", "视情况",
)


def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = str(s).replace("\u3000", " ").replace("\xa0", " ").replace("\r", "")
    s = re.sub(r"\s+", "", s)
    return s.strip()


def clean_keep_spaces(s: Optional[str]) -> str:
    """
    清理 PDF 换行带来的中文词间断行空格，同时保留纯英文/数字词之间的单个空格。
    例：'人民 币结 构性 存款' -> '人民币结构性存款'，'2026219 期单位大 额存单' -> '2026219期单位大额存单'
    """
    if not s:
        return ""
    s = str(s).replace("\u3000", " ").replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # 去掉与中文或中文标点相邻的多余空格
    s = re.sub(r"(?<=[\u4e00-\u9fff（）()、，。：；《》“”\-/])\s+", "", s)
    s = re.sub(r"\s+(?=[\u4e00-\u9fff（）()、，。：；《》“”\-/%])", "", s)
    # 去掉连续单个数字间的异常空格（如 '3 6 6 号' -> '366号'）
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    return s.strip()


def infer_market(sec_code: str) -> str:
    code = (sec_code or "").strip()
    if code.startswith(("60", "68", "90")):
        return "沪市"
    if code.startswith(("00", "30", "20")):
        return "深市"
    if code.startswith(("4", "8", "92")):
        return "北交所"
    return "A股"


def normalize_institution(raw: str, body_text: str = "") -> Tuple[str, str, str]:
    """
    返回 (institution_type, institution_main, branch_name)
    """
    text = clean_text(raw)
    if not text or text in ("-", "--", "/", "无", "不适用"):
        return ("其他", "", "未披露")

    # 去掉前缀序号或括号备注
    text_clean = re.sub(r"^[\d一二三四五六七八九十]+[、.．)]", "", text)
    # 去掉“（以下简称...）”
    text_clean = re.sub(r"[（(]以下简称[^）)]*[）)]", "", text_clean)

    # 1. 判断机构类型（兼容中文及常见外资银行英文名）
    upper_raw = clean_keep_spaces(raw).upper()
    inst_type = "其他"
    if any(k in text_clean for k in WEALTH_SUB_KEYWORDS) or ("理财" in text_clean and "银行" not in text_clean):
        inst_type = "银行理财子"
    elif "证券" in text_clean or any(k in text_clean for k in BROKER_KEYWORDS) or "SECURITIES" in upper_raw:
        inst_type = "券商"
    elif "信托" in text_clean or "TRUST" in upper_raw:
        inst_type = "信托"
    elif any(k in text_clean for k in ("基金", "资产管理", "资管", "期货")):
        inst_type = "基金/资管"
    elif (
        any(k in text_clean for k in ("银行", "农商行", "信用社", "信用联社", "农信", "行"))
        or any(text_clean.startswith(a) for a in BANK_ALIAS_MAP)
        or any(eb in upper_raw for eb in ("BANK", "BNP PARIBAS", "HSBC", "CITIBANK", "JPMORGAN", "STANDARD CHARTERED", "DBS", "OCBC", "UOB"))
    ):
        inst_type = "银行"

    # 若是英文外资银行名称，保留英文单词空格便于阅读
    if inst_type == "银行" and re.search(r"[A-Za-z]{4,}", upper_raw) and not re.search(r"[\u4e00-\u9fff]{2,}", text_clean):
        en_name = clean_keep_spaces(raw)
        br = "未披露"
        m_en = re.search(r"([A-Za-z\s]+BRANCH|HONG KONG|SINGAPORE|NEW YORK)$", en_name, re.I)
        if m_en and len(en_name) > len(m_en.group(1)) + 4:
            br = m_en.group(1).strip(" ,-")
            en_name = en_name[: m_en.start()].strip(" ,-")
        return ("银行", en_name, br)

    # 2. 提取主机构 + 分支行/营业部
    main = text_clean
    branch = "未披露"

    if inst_type == "银行":
        # 优先匹配：...银行(股份有限公司|有限责任公司|有限公司)? + (XXX分行/支行/营业部/营业室/总行营业部)
        m = re.match(
            r"^(.*?(?:农村商业银行|城市商业银行|村镇银行|商业银行|发展银行|储蓄银行|农业银行|工商银行|建设银行|中国银行|交通银行|招商银行|兴业银行|中信银行|光大银行|民生银行|华夏银行|广发银行|平安银行|浙商银行|渤海银行|恒丰银行|农商行|银行|信用合作联社|信用联社|信用社))(?:股份有限公司|有限责任公司|有限公司)?(?:（中国）|\(中国\))?(?:有限公司)?(.*)$",
            text_clean,
        )
        if m:
            main_part = m.group(1)
            rest = m.group(2).strip(" -—、，,（）()")
            # 有时候银行名后紧跟“（中国）有限公司XX分行”
            rest = re.sub(r"^(?:股份有限公司|有限责任公司|有限公司|（中国）|\(中国\))+", "", rest)
            main = main_part
            if rest:
                # 仅提取真实分支行/营业部片段，严防银行名后紧跟产品名称（如“苏州分行单位人民币定制型结构性存款”或“企业金融人民币结构性存款产品”）混入分行字段
                m_br = re.match(
                    r"^([\u4e00-\u9fa5A-Za-z0-9·()（）]{1,28}?(?:分行营业部|支行营业部|分行|支行|营业部|营业室|营业厅|分理处|储蓄所|网点|中心))",
                    rest,
                )
                if m_br:
                    cand_br = m_br.group(1).strip(" -—、，,（）()")
                    if not any(
                        bad in cand_br
                        for bad in ("存款", "理财", "存单", "产品", "收益", "结构", "系列", "财富", "计划", "基金", "信托")
                    ):
                        branch = cand_br
                elif (
                    len(rest) >= 2
                    and len(rest) <= 14
                    and not any(
                        bad in rest
                        for bad in (
                            "股份", "有限", "公司", "存款", "理财", "存单", "产品", "结构", "收益",
                            "系列", "挂钩", "财富", "添利", "智信", "金芙蓉", "保本", "浮动", "通知",
                            "协定", "定期", "对公", "企业金融", "单位", "人民币", "看涨", "看跌",
                            "区间", "凭证", "计划", "基金", "信托", "代码", "编号", "类型", "金额",
                            "期限", "利率", "：", ":", "“", "”",
                        )
                    )
                    and not re.search(r"\d{2,}|[（(][一二三四五六七八九十\d]+[）)]", rest)
                ):
                    branch = rest
                    if branch.endswith(("支", "分")) and len(branch) >= 3:
                        branch = branch + "行"
        else:
            # 可能是简称开头，例如 “工行苏州分行” / “农行XX支行”
            for alias, full_name in sorted(BANK_ALIAS_MAP.items(), key=lambda x: -len(x[0])):
                if text_clean.startswith(alias):
                    main = full_name
                    rest = text_clean[len(alias):].strip(" -—、（）()")
                    if rest:
                        branch = rest
                    break

        # 标准化主银行名称
        main = re.sub(r"(?:股份有限公司|有限责任公司|有限公司)$", "", main)
        main = re.sub(r"[（(]中国[）)]$", "", main)
        if main in BANK_ALIAS_MAP:
            main = BANK_ALIAS_MAP[main]
        else:
            # 去掉城市前缀外的多余词，保持干净
            for alias, full_name in BANK_ALIAS_MAP.items():
                if main == alias:
                    main = full_name
                    break

    elif inst_type == "券商":
        m = re.match(
            r"^(.*?(?:证券|中金公司|中国国际金融))(?:股份有限公司|有限责任公司|有限公司)?(.*)$",
            text_clean,
        )
        if m:
            main = m.group(1)
            rest = re.sub(r"^(?:股份有限公司|有限责任公司|有限公司)+", "", m.group(2)).strip(" -—（）()")
            if rest and any(k in rest for k in ("营业部", "分公司", "分支机构", "中心")):
                branch = rest
        else:
            # 可能是产品名里带券商简称（如“中信建投臻享财富2003号...”、“中信建投收益凭证...”）
            for bk in sorted(BROKER_KEYWORDS, key=lambda x: -len(x)):
                if bk in text_clean:
                    main = bk if bk.endswith(("证券", "公司", "金融")) else f"{bk}证券"
                    break
        if main in ("中金公司", "中国国际金融"):
            main = "中金公司"
        main = re.sub(r"(?:股份有限公司|有限责任公司|有限公司)$", "", main)
        if main == "中国国际金融":
            main = "中金公司"

    elif inst_type == "银行理财子":
        m = re.search(r"([\u4e00-\u9fa5]{2,10}理财)", text_clean)
        main = m.group(1) if m else re.sub(r"(?:有限责任公司|股份有限公司|有限公司)$", "", text_clean)

    elif inst_type == "信托":
        # 避免把“中信信托启元1号财富管理服务信托”整串产品名当机构名
        m = re.search(r"([\u4e00-\u9fa5]{2,10}信托)", text_clean)
        main = m.group(1) if m else re.sub(r"(?:有限责任公司|股份有限公司|有限公司)$", "", text_clean)

    elif inst_type == "基金/资管":
        m = re.search(r"([\u4e00-\u9fa5]{2,10}(?:基金|资管|资产管理|期货))", text_clean)
        main = m.group(1) if m else re.sub(r"(?:有限责任公司|股份有限公司|有限公司)$", "", text_clean)

    else:
        main = re.sub(r"(?:股份有限公司|有限责任公司|有限公司)$", "", text_clean)

    # 拦截明显非机构的脏值（纯数字、百分比、过短泛称）
    if re.search(r"^\d|%", main) or main in ("银行", "商业银行", "证券", "理财", "信托", "基金", "资管", "机构"):
        return ("其他", "未披露", "未披露")

    # 3. 如果表格中未披露分行，尝试从公告正文中反查该银行的唯一分支行
    if branch == "未披露" and body_text and main and inst_type in ("银行", "券商"):
        branch_found = _find_branch_in_body(main, inst_type, body_text)
        if branch_found:
            branch = branch_found

    if not branch or branch in ("总行", "公司", "有限公司", "股份有限公司"):
        branch = "未披露"

    return (inst_type, main, branch)


def _find_branch_in_body(main: str, inst_type: str, body_text: str) -> str:
    clean_body = clean_text(body_text)
    escaped = re.escape(main)
    if inst_type == "银行":
        pat = rf"{escaped}(?:股份有限公司|有限责任公司|有限公司)?(?:（中国）|\(中国\))?(?:有限公司)?([\u4e00-\u9fa5A-Za-z0-9]{{2,18}}(?:分行营业部|分行|支行|营业部))"
    else:
        pat = rf"{escaped}(?:股份有限公司|有限责任公司|有限公司)?([\u4e00-\u9fa5A-Za-z0-9]{{2,18}}(?:证券营业部|营业部|分公司))"
    matches = set(re.findall(pat, clean_body))
    # 过滤掉像“账户开立在XX支行”但出现多个不同支行时的歧义（只保留唯一或最多提取第一个清晰分支行？为严谨，仅当唯一时自动补全！）
    filtered = [
        m for m in matches
        if not any(
            bad in m
            for bad in (
                "募集资金", "专用", "专户", "监管", "协议", "公司", "签署", "购买", "开展", "办理",
                "银行", "存款", "理财", "产品", "存单", "结构", "收益", "通知", "定期", "协定",
                "证券", "信托", "基金", "公告", "闲置", "资金", "理财产品",
            )
        )
    ]
    if len(filtered) == 1:
        return filtered[0]
    return ""


def normalize_product_type(product_name: str, product_type_raw: str = "") -> str:
    combined = f"{product_name or ''} {product_type_raw or ''}"
    if "结构性存款" in combined or "挂钩型" in combined or "共赢智信" in combined or "蕴通财富" in combined or "点金看涨" in combined or "点金看跌" in combined or "聚赢" in combined:
        return "结构性存款"
    if "大额存单" in combined:
        return "大额存单"
    if "通知存款" in combined:
        return "通知存款"
    if "协定存款" in combined:
        return "协定存款"
    if "定期存款" in combined or "定存" in combined or "整存整取" in combined or "定期储蓄" in combined:
        return "定期存款"
    if "收益凭证" in combined or "本金保障" in combined or "质押式报价回购" in combined or "报价回购" in combined:
        return "券商收益凭证"
    if "逆回购" in combined or "国债回购" in combined:
        return "国债逆回购"
    if "货币基金" in combined or "货币市场基金" in combined:
        return "货币基金"
    if "信托" in combined:
        return "信托产品"
    if "资管" in combined or "资产管理计划" in combined or "集合资产" in combined:
        return "资管计划"
    if "存款" in combined:
        return "其他存款"
    if any(k in combined for k in ("理财", "固定收益", "保本", "浮动收益", "现金管理", "封闭式", "开放式", "净值型")):
        return "银行/机构理财"
    return "其他"


def parse_amount_wan(val_raw: str, header_raw: str = "", table_unit_hint: str = "") -> Tuple[Optional[float], str]:
    """
    解析金额并统一转换为“万元”，返回 (amount_wan, currency)。
    若原文为外币并附带人民币折算（如 '1,100亿印尼盾（约4,157.01万人民币）'），自动提取人民币折算值！
    """
    raw = clean_text(val_raw)
    if not raw or raw in ("-", "--", "/", "无", "不适用"):
        return (None, "人民币")

    # 优先检查括号内是否已折算为人民币（常见于海外子公司印尼盾/越南盾/美元存款公告）
    rmb_eq = re.search(
        r"[（(][^）)]*?(?:约|折合|合)?(?:人民币)?([\d,]+(?:\.\d+)?)\s*(万|亿)?(?:人民币|元)[^）)]*[）)]",
        raw,
    )
    if rmb_eq and any(fc in raw for fc in ("印尼", "越南", "泰铢", "美元", "港元", "港币", "欧元", "日元", "英镑", "台币", "韩元", "林吉特")):
        num = float(rmb_eq.group(1).replace(",", ""))
        u = rmb_eq.group(2) or ""
        if u == "亿":
            return (round(num * 10000.0, 4), "人民币")
        if u == "万":
            return (round(num, 4), "人民币")
        return (round(num / 10000.0 if num >= 100000 else num, 4), "人民币")

    combined_unit = f"{raw}|{header_raw or ''}|{table_unit_hint or ''}"
    upper_u = combined_unit.upper()
    currency = "人民币"
    if "印尼" in combined_unit or "卢比" in combined_unit or "IDR" in upper_u:
        currency = "印尼盾"
    elif "越南" in combined_unit or "VND" in upper_u:
        currency = "越南盾"
    elif "泰铢" in combined_unit or "THB" in upper_u:
        currency = "泰铢"
    elif "新台币" in combined_unit or "台币" in combined_unit or "TWD" in upper_u:
        currency = "新台币"
    elif "美元" in combined_unit or "USD" in upper_u or "$" in raw:
        currency = "美元"
    elif "港元" in combined_unit or "港币" in combined_unit or "HKD" in upper_u:
        currency = "港币"
    elif "欧元" in combined_unit or "EUR" in upper_u:
        currency = "欧元"
    elif "日元" in combined_unit or "JPY" in upper_u:
        currency = "日元"
    elif "英镑" in combined_unit or "GBP" in upper_u:
        currency = "英镑"
    elif "新加坡元" in combined_unit or "新币" in combined_unit or "SGD" in upper_u:
        currency = "新加坡元"
    elif "澳元" in combined_unit or "AUD" in upper_u:
        currency = "澳元"
    elif "韩元" in combined_unit or "KRW" in upper_u:
        currency = "韩元"
    elif "林吉特" in combined_unit or "马币" in combined_unit or "MYR" in upper_u:
        currency = "林吉特"

    # 抽取数值（注意：带千分位逗号的分支必须至少包含一个逗号，避免 1000 被截断成 100！）
    m = re.search(r"([+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?)", raw)
    if not m:
        return (None, currency)
    num_str = m.group(1).replace(",", "")
    try:
        num = float(num_str)
    except ValueError:
        return (None, currency)

    # 判定单位优先级：单元格自带 > 表头 > 表前提示 > 数量级启发式
    unit = None
    if re.search(r"亿", raw):
        unit = "yi"
    elif re.search(r"万", raw):
        unit = "wan"
    elif re.search(r"(?:元|美元|港元|港币|欧元)", raw):
        unit = "yuan"
    elif re.search(r"亿", header_raw or ""):
        unit = "yi"
    elif re.search(r"万", header_raw or ""):
        unit = "wan"
    elif re.search(r"[（(]\s*(?:人民币)?元\s*[）)]|单位[：:]\s*元|金额[（(]元[）)]", header_raw or ""):
        unit = "yuan"
    elif re.search(r"亿", table_unit_hint or ""):
        unit = "yi"
    elif re.search(r"万", table_unit_hint or ""):
        unit = "wan"
    elif re.search(r"元", table_unit_hint or ""):
        unit = "yuan"

    if unit == "yi":
        val_wan = num * 10000.0
    elif unit == "yuan":
        val_wan = num / 10000.0
    elif unit == "wan":
        val_wan = num
    else:
        # 未标注单位时：单笔理财如果数值 >= 1,000,000（即若按万元算>=100亿），极大概率原单位是“元”
        if abs(num) >= 1_000_000:
            val_wan = num / 10000.0
        else:
            val_wan = num

    return (round(val_wan, 4), currency)


def parse_income_wan(
    val_raw: str,
    header_raw: str = "",
    principal_wan: Optional[float] = None,
    table_unit_hint: str = "",
) -> Optional[float]:
    val_wan, _ = parse_amount_wan(val_raw, header_raw, table_unit_hint)
    if val_wan is None:
        return None
    # 针对很多公告表头漏写“实际收益(元)”导致把 133698.63 元误当万元的纠偏：
    # 现金管理产品年化一般 < 10%，单笔收益不可能超过本金的 30%（且值 > 100）
    has_explicit_unit = bool(re.search(r"[万亿]", f"{val_raw or ''}{header_raw or ''}"))
    if not has_explicit_unit:
        if principal_wan and principal_wan > 0 and val_wan > principal_wan * 0.25 and val_wan > 50:
            val_wan = round(val_wan / 10000.0, 4)
    return val_wan


def parse_yield_rate(raw: str) -> Tuple[str, Optional[float], Optional[float]]:
    """
    解析预期年化收益率/收益率区间，返回 (display_str, yield_min, yield_max)。
    自动把冗长的“保底+浮动”“固定+n/N”“高/中/低档”条款规整为简洁准确的收益率区间（如 '0.7%-1.75%'），
    并把基准指数/加点公式保留为文本且不误解析权重或点差数值。
    """
    if not raw:
        return ("", None, None)
    text = clean_keep_spaces(raw).replace("％", "%")
    if text in ("-", "--", "/", "无", "不适用", "N/A"):
        return ("", None, None)

    def _fmt_range(lo: float, hi: float) -> str:
        lo_r, hi_r = round(lo, 4), round(hi, 4)
        if abs(lo_r - hi_r) < 1e-6:
            return f"{lo_r:g}%"
        return f"{lo_r:g}%-{hi_r:g}%"

    # 1. 区间累计叠加公式 “年化收益率=0.95%+0.72%*n/N” -> [0.95%, 1.67%]（放在基准权重规则之前！）
    m_accrual = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*\+\s*(\d+(?:\.\d+)?)\s*%\s*[*×xX]\s*[nN]\s*/\s*[nN]",
        text,
    )
    if m_accrual:
        base_v = float(m_accrual.group(1))
        add_v = float(m_accrual.group(2))
        if 0.0 < base_v <= 12.0 and 0.0 < base_v + add_v <= 12.0:
            ymin = round(base_v, 4)
            ymax = round(base_v + add_v, 4)
            return (_fmt_range(ymin, ymax), ymin, ymax)

    # 2. 复杂基准指数权重 / 利率加减点公式（如“活期基准利率*80%+中债指数*20%”“定存利率+0.05%”“协定存款利率+25BP”）
    if re.search(
        r"(?:[*×xX]\s*\d+(?:\.\d+)?%|\d+(?:\.\d+)?%\s*[*×xX]|指数(?:收益率|（|\(|代码)|基准利率\s*[*×xX]|"
        r"(?:利率|指数)[^%]{0,18}(?:\+|加|减|上浮|下浮)\s*\d+(?:\.\d+)?(?:\s*(?:BP|bps|个基点)|%))",
        text,
        re.I,
    ):
        return (text, None, None)

    # 3. 浦发利多多等“保底收益率 X%，浮动收益率为 0% 或 Y% 或 Z%”叠加结构 -> 总收益区间 [X, X + max(float)]
    m_base_float = re.search(
        r"(?:保底|固定|最低)(?:年化)?收益率(?:（年化）|\(年化\))?[：:为]?\s*(\d+(?:\.\d+)?)\s*%.*?浮动(?:年化)?收益率(?:（年化）|\(年化\))?[：:为]?\s*([^。；\n]{1,90})",
        text,
    )
    if m_base_float:
        base_v = float(m_base_float.group(1))
        tail = m_base_float.group(2)
        # 若浮动收益是复杂期权公式（含 Max / 期初价格），仅保留保底起点，避免误算
        if not any(k in tail for k in ("Max", "期初价格", "期末价格", "收盘价格")):
            flts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", tail) if 0.0 <= float(x) <= 10.0]
            if 0.0 < base_v <= 12.0 and flts:
                ymin = round(base_v + min(flts), 4)
                ymax = round(base_v + max(flts), 4)
                if 0.0 < ymax <= 12.0:
                    return (_fmt_range(ymin, ymax), ymin, ymax)

    # 4. 常规收益率解析：先剥离完整日期、汇率/价格阈值条件（如 '*【95%】' '>108%'）、期限数字
    sanitized = re.sub(r"20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?", "", text)
    # 剥离被正文粘连的后续条目（如 '1.67%7、产品期限：...'）
    sanitized = re.split(r"\d+[、.]\s*(?:产品期限|投资期限|起息日|到期日)", sanitized)[0]
    # 剥离条件价格比率（如 '*【95.0000】%'、'>108%'、'100%'、'Max[0%, 18%...'）
    sanitized = re.sub(r"[*×<>≥≤]\s*[【\[]?\s*\d+(?:\.\d+)?\s*[】\]]?\s*%", "", sanitized)
    sanitized = re.sub(r"Max\[[^\]]*\]", "", sanitized, flags=re.I)
    sanitized = re.sub(r"\d+(?:天|个月|年|期|号|点)", "", sanitized)

    pct_nums = re.findall(r"(\d+(?:\.\d+)?)\s*[】\]]?\s*%", sanitized)
    if not pct_nums and len(sanitized) <= 20:
        pct_nums = re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)(?![\d.])", sanitized)

    vals = []
    for x in pct_nums:
        try:
            v = float(x)
            # 现金管理/理财年化收益率合理区间：0 < v <= 12%
            if 0.0 < v <= 12.0:
                vals.append(v)
        except ValueError:
            pass

    if not vals:
        return (text, None, None)

    ymin = round(min(vals), 4)
    ymax = round(max(vals), 4)

    # 若原文已是简洁区间，或原文过长（冗长分档说明/条款）但已精准提炼出明确 % 收益区间，则统一显示简洁区间
    if re.fullmatch(r"[\d.\s%-~—至到/或]+", text) or len(text) > 16:
        # 特殊：仅固定部分已知而浮动是期权公式（如 惠城环保 “固定1.2% + Max[...]”）保留简洁提示
        if "浮动收益率" in text and any(k in text for k in ("Max", "期初价格", "收盘价格")):
            pretty = f"固定{ymin:g}%+浮动"
        else:
            pretty = _fmt_range(ymin, ymax)
    else:
        pretty = text

    return (pretty, ymin, ymax)


def parse_single_date(raw: str) -> Tuple[str, str]:
    """
    返回 (display_date, iso_date)
    如果是标准日期，display_date == iso_date == 'YYYY-MM-DD'
    如果是非标准文本（如'随时转让'），display_date = 原文, iso_date = ''
    """
    text = clean_text(raw)
    if not text or text in ("-", "--", "/", "无", "不适用"):
        return ("", "")

    # 匹配 YYYY年M月D日 / YYYY-M-D / YYYY.M.D / YYYY/M/D
    m = re.search(r"(20\d{2})[年./\-](\d{1,2})[月./\-](\d{1,2})日?", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                dt = date(y, mo, d)
                s = dt.strftime("%Y-%m-%d")
                return (s, s)
            except ValueError:
                pass

    # 匹配 8位紧凑数字 YYYYMMDD
    m2 = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", text)
    if m2:
        y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            dt = date(y, mo, d)
            s = dt.strftime("%Y-%m-%d")
            return (s, s)
        except ValueError:
            pass

    return (text, "")


def parse_date_range(raw: str) -> Tuple[str, str, str, str]:
    """
    处理把起息日和到期日合在一列的格式，例如 '2026.08.20-2026.11.20' 或 '2026年8月20日至2026年11月20日'
    返回 (start_disp, end_disp, end_iso, term_raw)
    """
    text = clean_text(raw)
    if not text:
        return ("", "", "", "")
    dates = re.findall(r"20\d{2}[年./\-]\d{1,2}[月./\-]\d{1,2}日?", text)
    if len(dates) >= 2:
        s_disp, _ = parse_single_date(dates[0])
        e_disp, e_iso = parse_single_date(dates[-1])
        return (s_disp, e_disp, e_iso, "")
    if len(dates) == 1:
        # 可能是 "2026-11-20起随时转让"
        s_disp, _ = parse_single_date(dates[0])
        rest = text.replace(dates[0], "").strip("-~至到起 ")
        if rest:
            return (s_disp, rest, "", rest)
        return (s_disp, "", "", "")
    return ("", "", "", text)


def estimate_end_date(start_iso: str, term_raw: str) -> str:
    """
    若公告只写了起息日 + 期限（如 30天 / 3个月 / 1年），推算标准到期日 iso 供内部判断到期状态
    """
    if not start_iso or not term_raw:
        return ""
    try:
        dt = datetime.strptime(start_iso, "%Y-%m-%d").date()
    except ValueError:
        return ""

    t = clean_text(term_raw)
    m_day = re.search(r"(\d+)\s*[天日]", t)
    if m_day:
        days = int(m_day.group(1))
        if 1 <= days <= 3650:
            return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    m_mon = re.search(r"(\d+)\s*个?月", t)
    if m_mon:
        months = int(m_mon.group(1))
        if 1 <= months <= 120:
            return (dt + timedelta(days=months * 30)).strftime("%Y-%m-%d")
    m_yr = re.search(r"(\d+)\s*年", t)
    if m_yr:
        yrs = int(m_yr.group(1))
        if 1 <= yrs <= 10:
            return (dt + timedelta(days=yrs * 365)).strftime("%Y-%m-%d")
    return ""


def compute_maturity_status(
    record_type: str,
    end_date_disp: str,
    end_date_iso: str,
    term_raw: str = "",
    today: Optional[date] = None,
) -> str:
    if record_type == "到期赎回":
        return "已到期赎回"

    today = today or date.today()
    combined = f"{end_date_disp or ''} {term_raw or ''}"
    if any(k in combined for k in FLEXIBLE_KEYWORDS):
        return "随时可转让/赎回"

    if end_date_iso:
        try:
            dt = datetime.strptime(end_date_iso, "%Y-%m-%d").date()
            return "已到期" if dt <= today else "存续中"
        except ValueError:
            pass

    return "未知"


def normalize_fund_source(raw: str, fallback_hint: str = "") -> str:
    text = f"{raw or ''}"
    for src in (text, fallback_hint or ""):
        if not src:
            continue
        has_raise = any(k in src for k in ("募集", "募资", "超募", "可转债", "定增", "首发"))
        has_own = any(k in src for k in ("自有", "自筹"))
        if has_raise and has_own:
            return "募集+自有资金"
        if "超募" in src:
            return "闲置超募资金"
        if has_raise:
            return "闲置募集资金"
        if has_own:
            return "闲置自有资金"
    return "未注明"


def make_fingerprint(rec: Dict) -> str:
    """
    稳定去重指纹：同一公告内允许不同行（用序号或关键字段区分），跨公告同笔同类型自动去重
    """
    amt = f"{rec.get('amount_wan'):.2f}" if rec.get("amount_wan") is not None else ""
    parts = [
        str(rec.get("sec_code") or "").strip(),
        str(rec.get("record_type") or "").strip(),
        str(rec.get("institution_main") or rec.get("institution_raw") or "").strip(),
        clean_text(rec.get("product_name") or ""),
        amt,
        str(rec.get("start_date") or "").strip(),
        str(rec.get("end_date") or "").strip(),
        str(rec.get("term_raw") or "").strip(),
        str(rec.get("yield_rate_raw") or "").strip(),
        # 区分同公司同日同产品买多笔（来自不同委托方或同一公告内明确多行由 _dup_seq 区分）
        clean_text(rec.get("entrusting_party") or ""),
        str(rec.get("_dup_seq") or 0),
    ]
    key = "|".join(parts)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

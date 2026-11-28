import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from app.config import DB_PATH
from app.db import get_conn


PROVINCE_NORMALIZE = {
    "北京市": "北京", "北京": "北京",
    "上海市": "上海", "上海": "上海",
    "天津市": "天津", "天津": "天津",
    "重庆市": "重庆", "重庆": "重庆",
    "广东省": "广东", "广东": "广东",
    "江苏省": "江苏", "江苏": "江苏",
    "浙江省": "浙江", "浙江": "浙江",
    "山东省": "山东", "山东": "山东",
    "福建省": "福建", "福建": "福建",
    "四川省": "四川", "四川": "四川",
    "湖北省": "湖北", "湖北": "湖北",
    "湖南省": "湖南", "湖南": "湖南",
    "安徽省": "安徽", "安徽": "安徽",
    "河南省": "河南", "河南": "河南",
    "河北省": "河北", "河北": "河北",
    "陕西省": "陕西", "陕西": "陕西",
    "江西省": "江西", "江西": "江西",
    "辽宁省": "辽宁", "辽宁": "辽宁",
    "山西省": "山西", "山西": "山西",
    "云南省": "云南", "云南": "云南",
    "贵州省": "贵州", "贵州": "贵州",
    "吉林省": "吉林", "吉林": "吉林",
    "黑龙江省": "黑龙江", "黑龙江": "黑龙江",
    "广西壮族自治区": "广西", "广西": "广西",
    "内蒙古自治区": "内蒙古", "内蒙古": "内蒙古",
    "新疆维吾尔自治区": "新疆", "新疆": "新疆",
    "宁夏回族自治区": "宁夏", "宁夏": "宁夏",
    "西藏自治区": "西藏", "西藏": "西藏",
    "甘肃省": "甘肃", "甘肃": "甘肃",
    "青海省": "青海", "青海": "青海",
    "海南省": "海南", "海南": "海南",
}

MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}

# 常见工业强县/县级市/特区 -> 所属地级市映射（防止注册地址漏写地级市）
COUNTY_TO_PREFECTURE = {
    "昆山": "苏州市", "常熟": "苏州市", "张家港": "苏州市", "太仓": "苏州市", "扬子江国际化学工业园": "苏州市",
    "江阴": "无锡市", "宜兴": "无锡市",
    "溧阳": "常州市", "武进": "常州市",
    "启东": "南通市", "如皋": "南通市", "海门": "南通市", "海安": "南通市", "如东": "南通市",
    "扬中": "镇江市", "丹阳": "镇江市", "句容": "镇江市",
    "靖江": "泰州市", "泰兴": "泰州市", "兴化": "泰州市",
    "高邮": "扬州市", "仪征": "扬州市", "宝应": "扬州市",
    "东台": "盐城市", "建湖": "盐城市",
    "邳州": "徐州市", "新沂": "徐州市",
    "慈溪": "宁波市", "余姚": "宁波市", "宁海": "宁波市", "象山": "宁波市",
    "瑞安": "温州市", "乐清": "温州市", "龙港": "温州市", "永嘉": "温州市", "平阳": "温州市", "泰顺": "温州市",
    "海宁": "嘉兴市", "平湖": "嘉兴市", "桐乡": "嘉兴市", "嘉善": "嘉兴市", "海盐": "嘉兴市",
    "诸暨": "绍兴市", "嵊州": "绍兴市", "新昌": "绍兴市",
    "义乌": "金华市", "东阳": "金华市", "永康": "金华市", "兰溪": "金华市", "武义": "金华市", "浦江": "金华市",
    "温岭": "台州市", "临海": "台州市", "玉环": "台州市", "天台": "台州市", "仙居": "台州市",
    "长兴": "湖州市", "德清": "湖州市", "安吉": "湖州市",
    "建德": "杭州市", "桐庐": "杭州市", "淳安": "杭州市", "萧山": "杭州市",
    "江山": "衢州市", "龙泉": "丽水市", "青田": "丽水市",
    "晋江": "泉州市", "石狮": "泉州市", "南安": "泉州市", "惠安": "泉州市", "安溪": "泉州市",
    "福清": "福州市", "闽侯": "福州市", "连江": "福州市",
    "龙海": "漳州市", "邵武": "南平市", "武夷山": "南平市", "永安": "三明市", "福鼎": "宁德市", "福安": "宁德市", "仙游": "莆田市",
    "胶州": "青岛市", "平度": "青岛市", "莱西": "青岛市",
    "龙口": "烟台市", "招远": "烟台市", "莱州": "烟台市", "莱阳": "烟台市", "海阳": "烟台市", "栖霞": "烟台市",
    "寿光": "潍坊市", "诸城": "潍坊市", "青州": "潍坊市", "高密": "潍坊市", "昌邑": "潍坊市", "安丘": "潍坊市",
    "荣成": "威海市", "乳山": "威海市",
    "邹平": "滨州市", "滕州": "枣庄市", "曲阜": "济宁市", "邹城": "济宁市", "肥城": "泰安市", "新泰": "泰安市", "乐陵": "德州市", "禹城": "德州市", "齐河": "德州市", "临清": "聊城市", "阳谷": "聊城市",
    "顺德": "佛山市", "南海": "佛山市", "开平": "江门市", "台山": "江门市", "鹤山": "江门市", "恩平": "江门市",
    "普宁": "揭阳市", "英德": "清远市", "四会": "肇庆市", "高州": "茂名市", "信宜": "茂名市", "廉江": "湛江市", "兴宁": "梅州市", "陆丰": "汕尾市", "阳春": "阳江市", "博罗": "惠州市", "惠东": "惠州市",
    "宁国": "宣城市", "广德": "宣城市", "天长": "滁州市", "明光": "滁州市", "全椒": "滁州市", "萧县": "宿州市", "桐城": "安庆市", "潜山": "安庆市", "巢湖": "合肥市", "肥西": "合肥市", "肥东": "合肥市", "长丰": "合肥市", "无为": "芜湖市", "界首": "阜阳市",
    "醴陵": "株洲市", "浏阳": "长沙市", "宁乡": "长沙市", "湘乡": "湘潭市", "韶山": "湘潭市", "耒阳": "衡阳市", "常宁": "衡阳市", "冷水江": "娄底市", "涟源": "娄底市", "汨罗": "岳阳市", "临湘": "岳阳市", "资兴": "郴州市", "武冈": "邵阳市", "津市": "常德市", "沅江": "益阳市", "吉首": "湘西自治州",
    "大冶": "黄石市", "宜都": "宜昌市", "枝江": "宜昌市", "当阳": "宜昌市", "枣阳": "襄阳市", "老河口": "襄阳市", "宜城": "襄阳市", "汉川": "孝感市", "应城": "孝感市", "安陆": "孝感市", "京山": "荆门市", "钟祥": "荆门市", "石首": "荆州市", "洪湖": "荆州市", "松滋": "荆州市", "麻城": "黄冈市", "武穴": "黄冈市", "赤壁": "咸宁市", "通城": "咸宁市", "广水": "随州市", "丹江口": "十堰市", "恩施": "恩施自治州", "仙桃": "仙桃市", "潜江": "潜江市", "天门": "天门市",
    "巩义": "郑州市", "新密": "郑州市", "新郑": "郑州市", "登封": "郑州市", "荥阳": "郑州市", "偃师": "洛阳市", "汝州": "平顶山市", "舞钢": "平顶山市", "林州": "安阳市", "卫辉": "新乡市", "辉县": "新乡市", "长垣": "新乡市", "沁阳": "焦作市", "孟州": "焦作市", "禹州": "许昌市", "长葛": "许昌市", "义马": "三门峡市", "灵宝": "三门峡市", "邓州": "南阳市", "西峡": "南阳市", "永城": "商丘市", "柘城": "商丘市", "项城": "周口市", "济源": "济源市",
    "迁安": "唐山市", "遵化": "唐山市", "滦州": "唐山市", "霸州": "廊坊市", "三河": "廊坊市", "武安": "邯郸市", "南宫": "邢台市", "沙河": "邢台市", "清河": "邢台市", "涿州": "保定市", "定州": "保定市", "安国": "保定市", "高碑店": "保定市", "任丘": "沧州市", "黄骅": "沧州市", "泊头": "沧州市", "河间": "沧州市", "辛集": "石家庄市", "晋州": "石家庄市", "新乐": "石家庄市", "深州": "衡水市",
    "都江堰": "成都市", "彭州": "成都市", "邛崃": "成都市", "崇州": "成都市", "简阳": "成都市", "双流": "成都市", "天府新区": "成都市", "广汉": "德阳市", "什邡": "德阳市", "绵竹": "德阳市", "江油": "绵阳市", "峨眉山": "乐山市", "西昌": "凉山自治州", "射洪": "遂宁市", "隆昌": "内江市", "阆中": "南充市", "华蓥": "广安市", "万源": "达州市",
    "瑞昌": "九江市", "共青城": "九江市", "庐山": "九江市", "乐平": "景德镇市", "丰城": "宜春市", "樟树": "宜春市", "高安": "宜春市", "贵溪": "鹰潭市", "瑞金": "赣州市", "龙南": "赣州市", "井冈山": "吉安市", "德兴": "上饶市",
    "瓦房店": "大连市", "庄河": "大连市", "海城": "鞍山市", "东港": "丹东市", "凤城": "丹东市", "凌海": "锦州市", "北镇": "锦州市", "盖州": "营口市", "大石桥": "营口市", "灯塔": "辽阳市", "调兵山": "铁岭市", "开原": "铁岭市", "北票": "朝阳市", "凌源": "朝阳市", "兴城": "葫芦岛市", "新民": "沈阳市",
    "韩城": "渭南市", "华阴": "渭南市", "兴平": "咸阳市", "彬州": "咸阳市", "杨凌": "咸阳市", "西咸新区": "西安市", "神木": "榆林市", "子长": "延安市", "旬阳": "安康市",
    "孝义": "吕梁市", "汾阳": "吕梁市", "介休": "晋中市", "高平": "晋城市", "原平": "忻州市", "侯马": "临汾市", "霍州": "临汾市", "永济": "运城市", "河津": "运城市", "怀仁": "朔州市", "古交": "太原市",
    "安宁": "昆明市", "宣威": "曲靖市", "澄江": "玉溪市", "腾冲": "保山市", "水富": "昭通市", "个旧": "红河自治州", "开远": "红河自治州", "蒙自": "红河自治州", "弥勒": "红河自治州", "文山": "文山自治州", "景洪": "西双版纳自治州", "大理": "大理自治州", "瑞丽": "德宏自治州", "芒市": "德宏自治州", "楚雄": "楚雄自治州", "香格里拉": "迪庆自治州",
    "仁怀": "遵义市", "赤水": "遵义市", "清镇": "贵阳市", "盘州": "六盘水市", "黔西": "毕节市", "兴义": "黔西南自治州", "兴仁": "黔西南自治州", "凯里": "黔东南自治州", "都匀": "黔南自治州", "福泉": "黔南自治州",
    "满洲里": "呼伦贝尔市", "锡林浩特": "锡林郭勒盟", "乌兰浩特": "兴安盟", "二连浩特": "锡林郭勒盟",
    "石河子": "石河子市", "库尔勒": "巴音郭楞自治州", "伊宁": "伊犁自治州", "昌吉": "昌吉自治州", "阿克苏": "阿克苏地区", "喀什": "喀什地区", "阿勒泰": "阿勒泰地区", "塔城": "塔城地区", "和田": "和田地区",
    "格尔木": "海西自治州", "德令哈": "海西自治州", "互助": "海东市", "玉门": "酒泉市", "敦煌": "酒泉市", "临夏": "临夏自治州", "合作": "甘南自治州",
    "桂平": "贵港市", "北流": "玉林市", "岑溪": "梧州市", "东兴": "防城港市", "靖西": "百色市", "凭祥": "崇左市", "合山": "来宾市", "横州": "南宁市",
    "延吉": "延边自治州", "梅河口": "通化市", "公主岭": "长春市", "珲春": "延边自治州", "敦化": "延边自治州", "磐石": "吉林市",
    "绥芬河": "牡丹江市", "海林": "牡丹江市", "尚志": "哈尔滨市", "五常": "哈尔滨市", "安达": "绥化市", "肇东": "绥化市", "海伦": "绥化市", "北安": "黑河市", "五大连池": "黑河市",
    "琼海": "琼海市", "文昌": "文昌市", "万宁": "万宁市", "东方": "东方市", "儋州": "儋州市", "定安": "定安县",
}

# 全国主要地级市简称（用于地址中漏写“市”字时的精准识别，如“山西综改示范区太原唐槐园区” -> “太原市”）
PREFECTURE_STEMS = (
    "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮",
    "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁",
    "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
    "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽",
    "福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德",
    "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳",
    "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州",
    "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底",
    "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城",
    "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店",
    "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水",
    "西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛",
    "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶",
    "沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛",
    "太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁",
    "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧",
    "贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁",
    "长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城",
    "哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化",
    "南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左",
    "呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布",
    "乌鲁木齐", "克拉玛依", "吐鲁番", "哈密",
    "银川", "石嘴山", "吴忠", "固原", "中卫",
    "兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南",
    "西宁", "海东",
    "拉萨", "日喀则", "昌都", "林芝", "山南", "那曲",
    "海口", "三亚", "三沙", "儋州",
)


def normalize_province(raw_prov: str, addr: str = "") -> str:
    p = (raw_prov or "").strip()
    if p in PROVINCE_NORMALIZE:
        return PROVINCE_NORMALIZE[p]
    for k, v in PROVINCE_NORMALIZE.items():
        if k and (k in p or k in (addr or "")):
            return v
    return p or "未知"


def extract_city(province: str, reg_addr: str = "", office_addr: str = "") -> str:
    if province in MUNICIPALITIES:
        return f"{province}市"

    for addr in (reg_addr or "", office_addr or ""):
        text = re.sub(r"\s+", "", addr)
        if not text:
            continue
        # 去掉前导国家/省份名，避免“吉林省”误匹配为“吉林市”
        body = re.sub(r"^(?:中华人民共和国|中国)?(?:[\u4e00-\u9fa5]{2,5}(?:省|壮族自治区|维吾尔自治区|回族自治区|自治区))?", "", text)
        if province and body.startswith(province):
            body = body[len(province):]

        # 1. 优先直接匹配标准地级市名称（含“市”字）
        for stem in PREFECTURE_STEMS:
            if f"{stem}市" in body[:24]:
                return f"{stem}市"

        # 2. 匹配自治州 / 地区 / 盟
        m_az = re.search(r"([\u4e00-\u9fa5]{2,8}(?:自治州|地区|盟))", body[:28])
        if m_az:
            return m_az.group(1)

        # 3. 匹配强县/县级市映射到地级市
        for county, pref in COUNTY_TO_PREFECTURE.items():
            if county in body[:24]:
                return pref

        # 4. 匹配地址中省掉“市”字的地级市名（如“山西综改示范区太原唐槐园区”）
        for stem in PREFECTURE_STEMS:
            if stem in body[:20]:
                return f"{stem}市"

        # 5. 兜底匹配任意“XX市”
        m_city = re.search(r"([\u4e00-\u9fa5]{2,5}市)", body[:24])
        if m_city:
            c = m_city.group(1)
            if not c.startswith(("开发", "高新", "保税", "经济")):
                return c

    return "其他/未注明"


def _make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    return s


def _sec_code_to_em_code(sec_code: str) -> str:
    c = sec_code.strip()
    if c.startswith(("60", "68", "90")):
        return f"SH{c}"
    if c.startswith(("00", "30", "20")):
        return f"SZ{c}"
    return f"BJ{c}"


def fetch_regions_batch(sec_codes: Iterable[str]) -> Dict[str, Tuple[str, str, str]]:
    """
    批量从东方财富数据中心 RPT_F10_BASIC_ORGINFO + emweb 兜底拉取上市公司 (province, city, reg_address)
    速度极快：500+ 家公司仅需 ~2-3 秒！
    """
    codes = sorted({str(c).strip() for c in sec_codes if c})
    if not codes:
        return {}

    sess = _make_session()
    results: Dict[str, Tuple[str, str, str]] = {}

    # 第一步：东方财富数据中心批量查询（每次 80 只股票，只需 ~7 次请求）
    batch_size = 80
    for i in range(0, len(codes), batch_size):
        chunk = codes[i : i + batch_size]
        in_list = ",".join(f'"{c}"' for c in chunk)
        try:
            r = sess.get(
                "https://datacenter-web.eastmoney.com/api/data/v1/get",
                params={
                    "reportName": "RPT_F10_BASIC_ORGINFO",
                    "columns": "SECURITY_CODE,PROVINCE,REG_ADDRESS,ADDRESS",
                    "filter": f"(SECURITY_CODE in ({in_list}))",
                    "pageSize": 200,
                    "pageNumber": 1,
                },
                timeout=12,
            )
            data = (r.json().get("result") or {}).get("data") or []
            for row in data:
                sc = str(row.get("SECURITY_CODE") or "").strip()
                if not sc or sc in results:
                    continue
                reg_addr = str(row.get("REG_ADDRESS") or "").strip()
                off_addr = str(row.get("ADDRESS") or "").strip()
                prov = normalize_province(str(row.get("PROVINCE") or ""), reg_addr or off_addr)
                city = extract_city(prov, reg_addr, off_addr)
                if prov and prov != "未知":
                    results[sc] = (prov, city, reg_addr or off_addr)
        except Exception:
            pass

    # 第二步：对数据中心漏掉的少量北交所/次新股用 emweb 单票并发兜底
    missing = [c for c in codes if c not in results]
    if missing:
        def fetch_one(sc: str) -> Tuple[str, Optional[Tuple[str, str, str]]]:
            em_code = _sec_code_to_em_code(sc)
            for attempt in range(2):
                try:
                    r = sess.get(
                        f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_code}",
                        timeout=10,
                    )
                    jbzl = (r.json().get("jbzl") or [{}])[0]
                    reg_addr = str(jbzl.get("REG_ADDRESS") or "").strip()
                    off_addr = str(jbzl.get("ADDRESS") or "").strip()
                    prov = normalize_province(str(jbzl.get("PROVINCE") or ""), reg_addr or off_addr)
                    city = extract_city(prov, reg_addr, off_addr)
                    if prov and prov != "未知":
                        return (sc, (prov, city, reg_addr or off_addr))
                except Exception:
                    time.sleep(0.3)
            return (sc, None)

        with ThreadPoolExecutor(max_workers=10) as ex:
            for fut in as_completed([ex.submit(fetch_one, c) for c in missing]):
                sc, val = fut.result()
                if val:
                    results[sc] = val

    return results


def enrich_company_regions(db_path: Path = DB_PATH, force_refresh: bool = False) -> Dict[str, int]:
    """
    为数据库中所有上市公司补齐省市区域信息（已缓存过的股票自动跳过，仅查新增公司），
    并同步回写到 records 表的 province / city 字段。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn(db_path) as conn:
        all_codes = {
            r[0]
            for r in conn.execute("SELECT DISTINCT sec_code FROM records WHERE sec_code != ''").fetchall()
        }
        if force_refresh:
            to_fetch = all_codes
        else:
            cached = {
                r[0]
                for r in conn.execute(
                    "SELECT sec_code FROM companies WHERE province IS NOT NULL AND province != '' AND province != '未知'"
                ).fetchall()
            }
            to_fetch = all_codes - cached

    fetched = fetch_regions_batch(to_fetch) if to_fetch else {}

    with get_conn(db_path) as conn:
        for sc, (prov, city, addr) in fetched.items():
            conn.execute(
                """
                INSERT INTO companies (sec_code, province, city, reg_address, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sec_code) DO UPDATE SET
                    province=excluded.province,
                    city=excluded.city,
                    reg_address=excluded.reg_address,
                    updated_at=excluded.updated_at
                """,
                (sc, prov, city, addr[:200], now),
            )

        # 同步所有 records 表的 province / city
        conn.execute(
            """
            UPDATE records
            SET province = COALESCE((SELECT c.province FROM companies c WHERE c.sec_code = records.sec_code), province, ''),
                city     = COALESCE((SELECT c.city     FROM companies c WHERE c.sec_code = records.sec_code), city, '')
            WHERE EXISTS (SELECT 1 FROM companies c WHERE c.sec_code = records.sec_code)
            """
        )

        # 对极个别仍缺失的公司，尝试从公司全称前缀（如“山西锦波”、“福建海峡”、“浙江XX”）推断省份
        still_empty = conn.execute(
            "SELECT DISTINCT sec_code, company_name FROM records WHERE province IS NULL OR province = '' OR province = '未知'"
        ).fetchall()
        for r in still_empty:
            sc, cname = r["sec_code"], r["company_name"] or ""
            prov = normalize_province("", cname)
            if prov != "未知":
                city = extract_city(prov, cname)
                conn.execute(
                    "UPDATE records SET province=?, city=? WHERE sec_code=?",
                    (prov, city, sc),
                )

        covered = conn.execute(
            "SELECT COUNT(DISTINCT sec_code) FROM records WHERE province IS NOT NULL AND province != '' AND province != '未知'"
        ).fetchone()[0]

    return {"total_companies": len(all_codes), "newly_fetched": len(fetched), "covered_companies": covered}

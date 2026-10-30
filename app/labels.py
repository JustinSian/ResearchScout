"""词条中文化：主题（OpenAlex concepts）与机构（OpenAlex display_name）。

做法：**先查词表，查不到用模式兜底，再查不到保留英文**。
为什么不做成机器翻译：这些是分类标签，点开筛选后必须和库里的英文值一致；
一旦翻译漂移，筛选就会返回空，比显示英文更糟。

约定：
- `name`  永远是库里的英文原值 → 用作筛选参数
- `name_zh` 只用于显示
"""
from __future__ import annotations

import re

TOPIC_ZH = {
    "Medicine": "医学",
    "Biology": "生物学",
    "Computer science": "计算机科学",
    "Physics": "物理学",
    "Internal medicine": "内科学",
    "Disease": "疾病",
    "Computational biology": "计算生物学",
    "Cardiology": "心脏病学",
    "Astrophysics": "天体物理学",
    "Intensive care medicine": "重症医学",
    "Burden of disease": "疾病负担",
    "Disease burden": "疾病负担",
    "Environmental health": "环境健康",
    "Genome": "基因组",
    "Incidence (geometry)": "发病率",
    "Demography": "人口学",
    "Psychology": "心理学",
    "Gene": "基因",
    "Genetics": "遗传学",
    "Astronomy": "天文学",
    "Stroke (engine)": "中风",
    "Cancer": "癌症",
    "Software": "软件",
    "Particle physics": "粒子物理",
    "Higgs boson": "希格斯玻色子",
    "Hazard ratio": "风险比",
    "Oncology": "肿瘤学",
    "Guideline": "临床指南",
    "Health professionals": "卫生专业人员",
    "Medical prescription": "处方",
    "Environmental science": "环境科学",
    "Chemistry": "化学",
    "Cancer research": "癌症研究",
    "Population": "人口",
    "Human genome": "人类基因组",
    "CMB cold spot": "宇宙微波背景冷斑",
    "Lung cancer": "肺癌",
    "Risk assessment": "风险评估",
    "Materials science": "材料科学",
    "Cosmic microwave background": "宇宙微波背景",
    "Artificial intelligence": "人工智能",
    "Machine learning": "机器学习",
    "Deep learning": "深度学习",
    "Neural network": "神经网络",
    "Pattern recognition": "模式识别",
    "Mathematics": "数学",
    "Statistics": "统计学",
    "Economics": "经济学",
    "Business": "商业",
    "Sociology": "社会学",
    "Political science": "政治学",
    "Geography": "地理学",
    "Geology": "地质学",
    "Ecology": "生态学",
    "Neuroscience": "神经科学",
    "Immunology": "免疫学",
    "Pharmacology": "药理学",
    "Public health": "公共卫生",
    "Epidemiology": "流行病学",
    "Surgery": "外科",
    "Pediatrics": "儿科",
    "Psychiatry": "精神病学",
    "Diabetes": "糖尿病",
    "Obesity": "肥胖",
    "Asthma": "哮喘",
    "Dementia": "痴呆",
    "Alzheimer's disease": "阿尔茨海默病",
    "COVID-19": "新冠肺炎",
    "SARS-CoV-2": "新冠病毒",
    "Influenza": "流感",
    "Tuberculosis": "结核病",
    "Hypertension": "高血压",
    "Myocardial infarction": "心肌梗死",
    "Heart failure": "心力衰竭",
    "Renal failure": "肾功能衰竭",
    "Chronic obstructive pulmonary disease": "慢性阻塞性肺疾病",
    "Respiratory system": "呼吸系统",
    "Antibiotic resistance": "抗生素耐药",
    "Vaccination": "疫苗接种",
    "Clinical trial": "临床试验",
    "Meta-analysis": "荟萃分析",
    "Systematic review": "系统综述",
    "Randomized controlled trial": "随机对照试验",
    "Cohort study": "队列研究",
    "Biomarker": "生物标志物",
    "Proteomics": "蛋白质组学",
    "Genomics": "基因组学",
    "Transcriptomics": "转录组学",
    "Microbiome": "微生物组",
    "Bioinformatics": "生物信息学",
    "Signal processing": "信号处理",
    "Computer vision": "计算机视觉",
    "Natural language processing": "自然语言处理",
    "Data mining": "数据挖掘",
    "Information retrieval": "信息检索",
    "Human–computer interaction": "人机交互",
    "Programming language": "程序设计语言",
    "Operating system": "操作系统",
    "Computer network": "计算机网络",
    "Cryptography": "密码学",
    "Quantum mechanics": "量子力学",
    "Condensed matter physics": "凝聚态物理",
    "Optics": "光学",
    "Thermodynamics": "热力学",
    "Electromagnetism": "电磁学",
    "Nuclear physics": "核物理",
    "General relativity": "广义相对论",
    "Black hole": "黑洞",
    "Galaxy": "星系",
    "Exoplanet": "系外行星",
    "Solar system": "太阳系",
    "Climate change": "气候变化",
    "Atmospheric science": "大气科学",
    "Oceanography": "海洋学",
    "Remote sensing": "遥感",
    "Sustainability": "可持续性",
    "Energy": "能源",
    "Materials": "材料",
    "Nanotechnology": "纳米技术",
    "Polymer": "高分子",
    "Catalysis": "催化",
    "Organic chemistry": "有机化学",
    "Inorganic chemistry": "无机化学",
    "Analytical chemistry": "分析化学",
    "Biochemistry": "生物化学",
    "Molecular biology": "分子生物学",
    "Cell biology": "细胞生物学",
    "Physiology": "生理学",
    "Anatomy": "解剖学",
    "Pathology": "病理学",
    "Radiology": "放射学",
    "Anesthesiology": "麻醉学",
    "Obstetrics": "产科学",
    "Gynecology": "妇科学",
    "Nursing": "护理学",
    "Dentistry": "牙科学",
    "Veterinary medicine": "兽医学",
    "Agriculture": "农业",
    "Food science": "食品科学",
    "Nutrition": "营养学",
    "Toxicology": "毒理学",
    "Genetics of disease": "疾病遗传学",
    "Health care": "医疗保健",
    "Quality of life": "生活质量",
    "Depression (economics)": "抑郁",
    "Anxiety": "焦虑",
    "Autism": "自闭症",
    "Schizophrenia": "精神分裂症",
    "Epilepsy": "癫痫",
    "Multiple sclerosis": "多发性硬化",
    "Parkinson's disease": "帕金森病",
    "Inflammation": "炎症",
    "Oxidative stress": "氧化应激",
    "Apoptosis": "细胞凋亡",
    "Stem cell": "干细胞",
    "Regenerative medicine": "再生医学",
    "Tissue engineering": "组织工程",
    "Drug delivery": "药物递送",
    "Pharmacokinetics": "药代动力学",
    "Clinical pharmacy": "临床药学",
    "Surgery (medicine)": "外科手术",
    "Transplantation": "移植",
    "Medical imaging": "医学影像",
    "Computed tomography": "计算机断层扫描",
    "Magnetic resonance imaging": "磁共振成像",
    "Ultrasound": "超声",
    "Wearable technology": "可穿戴技术",
    "Telemedicine": "远程医疗",
    "Health informatics": "卫生信息学",
    "Electronic health record": "电子健康档案",
    "Patient safety": "患者安全",
    "Health policy": "卫生政策",
    "Health economics": "卫生经济学",
    "Global health": "全球健康",
}

INST_ZH = {
    "University of Washington": "华盛顿大学",
    "Broad Institute": "布罗德研究所",
    "Harvard University": "哈佛大学",
    "Centre National de la Recherche Scientifique": "法国国家科学研究中心",
    "Brigham and Women's Hospital": "布莱根妇女医院",
    "National Institutes of Health": "美国国立卫生研究院",
    "Johns Hopkins University": "约翰斯·霍普金斯大学",
    "European Bioinformatics Institute": "欧洲生物信息研究所",
    "California Institute of Technology": "加州理工学院",
    "Memorial Sloan Kettering Cancer Center": "纪念斯隆-凯特琳癌症中心",
    "University of North Carolina at Chapel Hill": "北卡罗来纳大学教堂山分校",
    "University of California, Berkeley": "加州大学伯克利分校",
    "University of Cambridge": "剑桥大学",
    "Auckland University of Technology": "奥克兰理工大学",
    "Howard Hughes Medical Institute": "霍华德·休斯医学研究所",
    "European Centre for Medium-Range Weather Forecasts": "欧洲中期天气预报中心",
    "Stanford University": "斯坦福大学",
    "Princeton University": "普林斯顿大学",
    "Boston University": "波士顿大学",
    "Wellcome Sanger Institute": "惠康桑格研究所",
    "Lawrence Berkeley National Laboratory": "劳伦斯伯克利国家实验室",
    "The University of Texas MD Anderson Cancer Center": "德克萨斯大学 MD 安德森癌症中心",
    "University of Oxford": "牛津大学",
    "Massachusetts Institute of Technology": "麻省理工学院",
    "Washington University in St. Louis": "圣路易斯华盛顿大学",
    "Google DeepMind (United Kingdom)": "谷歌 DeepMind（英国）",
    "First Affiliated Hospital of Guangzhou Medical University": "广州医科大学附属第一医院",
    "University of California, Los Angeles": "加州大学洛杉矶分校",
    "University of California, San Francisco": "加州大学旧金山分校",
    "University of Michigan": "密歇根大学",
    "University of Wisconsin–Madison": "威斯康星大学麦迪逊分校",
    "The University of Sydney": "悉尼大学",
    "University of Pennsylvania": "宾夕法尼亚大学",
    "Austrian Academy of Sciences": "奥地利科学院",
    "University of Toronto": "多伦多大学",
    "University of Siena": "锡耶纳大学",
    "University of Bristol": "布里斯托大学",
    "Inserm": "法国国家健康与医学研究院",
    "Goddard Space Flight Center": "戈达德太空飞行中心",
    "SIB Swiss Institute of Bioinformatics": "瑞士生物信息学研究所",
    "University of California, San Diego": "加州大学圣地亚哥分校",
    "University of Chicago": "芝加哥大学",
    "Yale University": "耶鲁大学",
    "Columbia University": "哥伦比亚大学",
    "Duke University": "杜克大学",
    "Cornell University": "康奈尔大学",
    "New York University": "纽约大学",
    "University of Florida": "佛罗里达大学",
    "University of Minnesota": "明尼苏达大学",
    "University of Pittsburgh": "匹兹堡大学",
    "Emory University": "埃默里大学",
    "Vanderbilt University": "范德堡大学",
    "Mayo Clinic": "梅奥诊所",
    "Cleveland Clinic": "克利夫兰诊所",
    "Massachusetts General Hospital": "麻省总医院",
    "Charité – Universitätsmedizin Berlin": "柏林夏里特医学院",
    "Karolinska Institutet": "卡罗林斯卡学院",
    "University of Amsterdam": "阿姆斯特丹大学",
    "Utrecht University": "乌得勒支大学",
    "University of Copenhagen": "哥本哈根大学",
    "University of Helsinki": "赫尔辛基大学",
    "University of Oslo": "奥斯陆大学",
    "University of Zurich": "苏黎世大学",
    "ETH Zurich": "苏黎世联邦理工学院",
    "École Polytechnique Fédérale de Lausanne": "洛桑联邦理工学院",
    "Max Planck Society": "马克斯·普朗克学会",
    "Chinese Academy of Sciences": "中国科学院",
    "Tsinghua University": "清华大学",
    "Peking University": "北京大学",
    "Zhejiang University": "浙江大学",
    "Fudan University": "复旦大学",
    "Shanghai Jiao Tong University": "上海交通大学",
    "Sun Yat-sen University": "中山大学",
    "University of Hong Kong": "香港大学",
    "National University of Singapore": "新加坡国立大学",
    "Nanyang Technological University": "南洋理工大学",
    "University of Tokyo": "东京大学",
    "Kyoto University": "京都大学",
    "Seoul National University": "首尔国立大学",
    "University of Melbourne": "墨尔本大学",
    "University of Queensland": "昆士兰大学",
    "Monash University": "莫纳什大学",
    "University of British Columbia": "不列颠哥伦比亚大学",
    "McGill University": "麦吉尔大学",
    "University of Edinburgh": "爱丁堡大学",
    "Imperial College London": "帝国理工学院",
    "King's College London": "伦敦国王学院",
    "University College London": "伦敦大学学院",
    "University of Manchester": "曼彻斯特大学",
    "University of Glasgow": "格拉斯哥大学",
    "Inserm U1015": "法国国家健康与医学研究院 U1015",
    "Weizmann Institute of Science": "魏茨曼科学研究所",
    "Technion – Israel Institute of Technology": "以色列理工学院",
    "IBM (United States)": "IBM（美国）",
    "Microsoft (United States)": "微软（美国）",
    "Google (United States)": "谷歌（美国）",
    "Microsoft Research (United States)": "微软研究院（美国）",
    "Meta (United States)": "Meta（美国）",
    "Amazon (United States)": "亚马逊（美国）",
    "NVIDIA (United States)": "英伟达（美国）",
    "American Cancer Society": "美国癌症协会",
    "Kessler Foundation": "凯斯勒基金会",
    "Université de Lille": "里尔大学",
    "American Heart Association": "美国心脏协会",
    "American Diabetes Association": "美国糖尿病协会",
    "World Health Organization": "世界卫生组织",
    "Centers for Disease Control and Prevention": "美国疾病控制与预防中心",
    "Food and Drug Administration": "美国食品药品监督管理局",
    "National Cancer Institute": "美国国家癌症研究所",
    "Fred Hutchinson Cancer Research Center": "弗雷德·哈钦森癌症研究中心",
    "Dana-Farber Cancer Institute": "丹娜-法伯癌症研究所",
    "Cleveland Clinic Foundation": "克利夫兰诊所基金会",
    "Mount Sinai Hospital": "西奈山医院",
    "University Health Network": "大学健康网络",
    "Public Health England": "英格兰公共卫生署",
    "Institut Pasteur": "巴斯德研究所",
    "Max Delbrück Center": "马克斯·德尔布吕克中心",
    "German Cancer Research Center": "德国癌症研究中心",
}

# 长尾兜底：只做最保守的几种模式，翻错不如不翻
_PATTERNS = [
    (re.compile(r"^University of (.+)$"), "{0}大学"),
    (re.compile(r"^(.+) University$"), "{0}大学"),
    (re.compile(r"^(.+) University of (.+)$"), "{0}大学{1}分校"),
    (re.compile(r"^(.+) Hospital$"), "{0}医院"),
    (re.compile(r"^(.+) Cancer Center$"), "{0}癌症中心"),
    (re.compile(r"^(.+) Cancer Institute$"), "{0}癌症研究所"),
    (re.compile(r"^(.+) Institute of Technology$"), "{0}理工学院"),
    (re.compile(r"^(.+) Medical Center$"), "{0}医学中心"),
    (re.compile(r"^(.+) National Laboratory$"), "{0}国家实验室"),
    (re.compile(r"^(.+) University Hospital$"), "{0}大学医院"),
    (re.compile(r"^(.+) Society$"), "{0}学会"),
    (re.compile(r"^(.+) Foundation$"), "{0}基金会"),
    (re.compile(r"^(.+) Research Institute$"), "{0}研究所"),
    (re.compile(r"^(.+) Institute$"), "{0}研究所"),
    (re.compile(r"^Université de (.+)$"), "{0}大学"),
    (re.compile(r"^(.+) College$"), "{0}学院"),
    (re.compile(r"^(.+) School of Medicine$"), "{0}医学院"),
]


QUAL_ZH = {
    "networking": "网络", "optics": "光学", "astronomy": "天文学", "computing": "计算",
    "programming language": "编程语言", "abstract data type": "抽象数据类型",
    "engine": "发动机", "archaeology": "考古学", "medicine": "医学",
    "economics": "经济学", "geometry": "几何", "statistics": "统计学",
    "mathematics": "数学", "biology": "生物学", "physics": "物理学",
    "computer science": "计算机科学", "chemistry": "化学", "geology": "地质学",
    "psychology": "心理学", "sociology": "社会学", "law": "法学",
    "machine learning": "机器学习", "artificial intelligence": "人工智能",
    "structural engineering": "结构工程", "cell biology": "细胞生物学",
}


def label_topic(en: str) -> str:
    if not en:
        return ""
    if en in TOPIC_ZH:
        return TOPIC_ZH[en]
    # "Black hole (networking)" → 主体 + 括号限定词分别翻
    m = re.match(r"^(.+?)\s*\(([^()]+)\)$", en)
    if m:
        base_raw = m.group(1).strip()
        base = label_topic(base_raw)
        qual = label_topic(m.group(2).strip())
        base_known = base_raw in TOPIC_ZH or base != base_raw
        if base_known and qual != m.group(2).strip():
            return f"{base}（{qual}）"
        if base_known:
            q = QUAL_ZH.get(m.group(2).strip().lower())
            if q:
                return f"{base}（{q}）"
    # 单词也走词级表（"Comorbidity" 这类只有单个词的条目）
    single = en.strip().lower()
    if single in TOPIC_WORDS:
        return TOPIC_WORDS[single] or en
    # 词级组合：只有当**每个词**都在词表里时才拼，避免出现"结直肠 cancer"这种半截翻译
    words = re.split(r"[\s\-–/]+", en.strip())
    if len(words) > 1:
        parts = []
        for w in words:
            key = w.strip("(),.").lower()
            if not key:
                continue
            zh = TOPIC_WORDS.get(key)
            if zh is None:
                return en            # 有一个词不认识就整体保留英文
            parts.append(zh)
        joined = "".join(parts)
        if joined and joined != en:
            return joined
    return en


# 词级词表：只放"确认不会翻错"的词，供上面的组合逻辑使用
TOPIC_WORDS = {
    "cancer": "癌", "lung": "肺", "breast": "乳腺", "prostate": "前列腺",
    "colorectal": "结直肠", "gastric": "胃", "liver": "肝", "pancreatic": "胰腺",
    "ovarian": "卵巢", "cervical": "宫颈", "thyroid": "甲状腺", "bladder": "膀胱",
    "skin": "皮肤", "brain": "脑", "bone": "骨", "blood": "血液",
    "dementia": "痴呆", "frontotemporal": "额颞叶", "vascular": "血管",
    "alzheimer": "阿尔茨海默", "parkinson": "帕金森", "disease": "病",
    "diabetes": "糖尿病", "mellitus": "", "obesity": "肥胖", "hypertension": "高血压",
    "asthma": "哮喘", "copd": "慢阻肺", "pneumonia": "肺炎", "sepsis": "脓毒症",
    "stroke": "中风", "heart": "心脏", "failure": "衰竭", "renal": "肾",
    "kidney": "肾",
    "intensive": "重症", "care": "监护", "unit": "病房",
    "comorbidity": "合并症", "apathy": "淡漠", "depression": "抑郁",
    "anxiety": "焦虑", "pain": "疼痛", "infection": "感染", "inflammation": "炎症",
    "immunotherapy": "免疫治疗", "chemotherapy": "化疗", "radiotherapy": "放疗",
    "surgery": "外科", "transplantation": "移植", "screening": "筛查",
    "diagnosis": "诊断", "prognosis": "预后", "treatment": "治疗",
    "mortality": "死亡率", "incidence": "发病率", "prevalence": "患病率",
    "risk": "风险", "factor": "因素", "outcome": "结局", "survival": "生存",
    "quality": "质量", "life": "生活", "health": "健康", "public": "公共",
    "mental": "心理", "cognitive": "认知", "impairment": "损害",
    "gene": "基因", "expression": "表达", "mutation": "突变", "protein": "蛋白",
    "cell": "细胞", "immune": "免疫", "antibody": "抗体", "vaccine": "疫苗",
    "microbiome": "微生物组", "gut": "肠道", "metabolism": "代谢",
    "machine": "机器", "learning": "学习", "deep": "深度", "neural": "神经",
    "network": "网络", "model": "模型", "algorithm": "算法", "data": "数据",
    "analysis": "分析", "clinical": "临床", "trial": "试验", "cohort": "队列",
    "randomized": "随机", "controlled": "对照", "study": "研究",
    "cardiovascular": "心血管", "respiratory": "呼吸", "pulmonary": "肺",
    "gastrointestinal": "胃肠", "neurological": "神经", "psychiatric": "精神",
    "pediatric": "儿科", "geriatric": "老年", "nursing": "护理",
    "hospital": "医院", "primary": "初级",
}


def label_inst(en: str) -> str:
    """只查词表 + 少量后缀规则（后缀规则只加中文后缀，不改英文主体）。

    教训：一开始写了 "University of {0} → {0}大学" 这类模板，
    结果 "American Cancer Society" 被拼成 "American Cancer学会"——半中半英，比不翻更糟。
    所以现在**不翻译主体**，只处理"整体后缀可判定且主体是中文"的情况。
    """
    if not en:
        return ""
    if en in INST_ZH:
        return INST_ZH[en]
    return en


def topic_items(names: list[str]) -> list[dict]:
    """给前端用：[{name: 英文(筛选值), zh: 中文(显示)}]"""
    return [{"name": n, "zh": label_topic(n)} for n in names]


def inst_items(names: list[str]) -> list[dict]:
    return [{"name": n, "zh": label_inst(n)} for n in names]


# ---- 补充词表（app/labels_data.py）：按覆盖人数挑选，避免主文件无限膨胀 ----
from app.labels_data import (EXTRA_INST_ZH, EXTRA_QUAL_ZH, EXTRA_TOPIC_WORDS,
                             EXTRA_TOPIC_ZH)

TOPIC_ZH.update(EXTRA_TOPIC_ZH)
TOPIC_WORDS.update({k: v for k, v in EXTRA_TOPIC_WORDS.items() if k != "system2"})
INST_ZH.update(EXTRA_INST_ZH)
QUAL_ZH.update(EXTRA_QUAL_ZH)
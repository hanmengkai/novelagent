"""
config/genre_templates.py — 网文类型故事结构模板

为常见网文类型提供预定义的 act_structure、卷目标和节奏模板。
init_novel() 调用时可通过 `--template` 参数选择模板，
LLM 在生成 story_outline 和 volume_plan 时以此作为结构参考。

用法：
  python main.py init --title xxx --desc xxx --template 末世重生
"""

from typing import Optional

# ════════════════════════════════════════════════════════════
#  模板定义
# ════════════════════════════════════════════════════════════

TEMPLATES: dict[str, dict] = {
    "末世重生": {
        "title": "末世重生流",
        "aliases": ["末世", "末日", "重生末世"],
        "description": "主角重生回末日爆发前，携带前世记忆和/or神级系统，提前布局改变命运",
        "act_structure": [
            {
                "name": "重生布局",
                "volumes": "1",
                "goal": "确定重生时间点，激活系统/金手指，与国家/势力对接，末日降临",
                "pacing": "快节奏，前三章必须打出重生+被背叛+金手指三重钩子",
            },
            {
                "name": "不一样的末日",
                "volumes": "2-3",
                "goal": "末日爆发后利用先知优势和系统快速建立基地/势力，面对进化丧尸和人类内斗",
                "pacing": "爽点密集：每5-8章有一次升级/打脸/救人/造新装备",
            },
            {
                "name": "大国崛起",
                "volumes": "4-7",
                "goal": "从区域霸主到全球领袖，工业体系建立，幕后黑手浮出水面",
                "pacing": "中期推进，引入暗线（前世的背叛者），丧尸出现智慧种",
            },
            {
                "name": "统一全球",
                "volumes": "8-9",
                "goal": "与幕后组织最终对决，统一全球，与背叛者的情感纠葛收尾",
                "pacing": "高潮迭起，每卷一个大决战，情绪张力拉满",
            },
            {
                "name": "新纪元",
                "volumes": "10",
                "goal": "战后重建，主角治愈内心创伤，开放式结局",
                "pacing": "舒缓收尾，情感沉淀",
            },
        ],
        "key_volumes": {
            "1": "核心情节：重生确认→系统激活→验证先知→与国家对接→末日降临。卷末钩子：末日来临但国家已有准备",
            "2": "末日第一波爆发，利用先知优势快速建立基地，首次面对进化丧尸。卷末钩子：发现有人暗中搞鬼",
            "3": "工业体系初步建立，收复失地，暗线人物（背叛者）首次出现",
        },
        "character_archetypes": {
            "protagonist": "被最信任的人背叛致死的重生者，这一世冷酷果断但有底线，对敌人狠对自己人重情义",
            "military_ally": "前世战死的军方大佬，这一世被主角提前拯救，成为最可靠的战友",
            "betrayer": "前世背叛者（恋人/兄弟），这一世仍然出现在主角生活中，是暗线核心",
            "sidekick": "前世为保护主角而死的兄弟，这一世提前救下",
        },
    },

    "种田日常": {
        "title": "种田日常流",
        "aliases": ["种田", "悠然", "日常"],
        "description": "主角穿越/重生到异世界或古代，从零开始建设家园，主打悠闲日常+美食+多女主",
        "act_structure": [
            {
                "name": "落地生根",
                "volumes": "1",
                "goal": "主角穿越落地，建立基本生活设施（住所、水源、食物）",
                "pacing": "慢热细腻，从一无所有到温饱解决的过程要有获得感",
            },
            {
                "name": "逐步扩张",
                "volumes": "2-4",
                "goal": "种田规模扩大，陆续有新角色加入（女主们自然登场），建成小型庄园",
                "pacing": "轻松欢快，日月更替+种植收获+美食日常+人物互动穿插",
            },
            {
                "name": "声名远扬",
                "volumes": "5-7",
                "goal": "庄园品质被外界认可，贸易往来增多，面临外部势力的压力/觊觎",
                "pacing": "保持70%日常+20%外部事件+10%暧昧互动的比例",
            },
            {
                "name": "风雨同舟",
                "volumes": "8-9",
                "goal": "较大外部冲突（天灾/战争/势力吞并），全员协作共渡难关",
                "pacing": "有紧张感但不过度压抑，最终回归温馨日常",
            },
            {
                "name": "岁月静好",
                "volumes": "10",
                "goal": "一切尘埃落定，庄园成为世外桃源，温暖的群像收尾",
                "pacing": "温馨治愈，情感沉淀",
            },
        ],
        "key_volumes": {
            "1": "核心情节：穿越落地→砍树建屋→种下第一粒种子→救下第一位女主（猫娘战士）。卷末：第一顿像样的晚餐",
            "2": "陆续加入新角色（精灵、吸血姬、狼耳大姐头），集市贸易开启",
            "3": "庄园扩建，温泉浴场建成，第一次丰收祭",
        },
        "character_archetypes": {
            "protagonist": "性格温和乐观但不傻白甜，动手能力强，遇到问题能冷静解决",
            "catgirl_warrior": "第一位女主，受伤被主角所救，武力担当，傲娇护主，被摸头会炸毛",
            "vampire_aristocrat": "第二位女主，被家族流放的吸血贵族，嘴硬傲娇对主角厨艺毫无抵抗力",
            "elf_archer": "温柔知性的精灵，团队的理性担当，自然融入庄园生活",
        },
    },

    "修仙": {
        "title": "修仙/仙侠",
        "aliases": ["修仙", "仙侠", "修真"],
        "description": "主角在修仙世界中从凡人起步，历经磨难飞升大道",
        "act_structure": [
            {"name": "凡人入道", "volumes": "1", "goal": "主角获得修炼机缘，入门筑基，初步了解修仙世界规则", "pacing": "慢热铺垫，展现修仙世界的广阔与神秘"},
            {"name": "崭露头角", "volumes": "2-4", "goal": "修为突破结丹/金丹，参加宗门大比/秘境探险，建立人脉", "pacing": "爽点密集：突破+夺宝+打脸+收徒"},
            {"name": "风云际会", "volumes": "5-7", "goal": "元婴/化神期，卷入宗门纷争/正魔大战，开始接触世界真相", "pacing": "格局扩大，世界观揭秘穿插在冲突中"},
            {"name": "飞升之路", "volumes": "8-9", "goal": "突破大乘/渡劫，与最终对手决战，打开飞升通道", "pacing": "高潮迭起，每卷一个关键突破"},
            {"name": "大道永恒", "volumes": "10", "goal": "飞升上界/留下传说，开放式结局", "pacing": "境界升华，余韵悠长"},
        ],
        "key_volumes": {
            "1": "核心情节：机缘获得→入门测试→第一次修炼突破→离开新手村。卷末：正式踏上修仙之路",
            "2": "进入宗门/加入势力，结识道友，首次秘境探险",
            "3": "修为突破中期，结丹成功，遭遇第一次重大危机",
        },
        "character_archetypes": {
            "protagonist": "悟性超群但有性格缺陷（太善良/太执着/有执念），在历练中成长",
            "master": "严师/高冷/隐藏高手，主角的引路人",
            "dao_friend": "同期入门的好友/对手，良性竞争关系",
            "antagonist": "同辈打压者/反派宗门，不断给主角制造麻烦的垫脚石",
        },
    },

    "科幻": {
        "title": "科幻/星际",
        "aliases": ["科幻", "星际", "未来", "赛博"],
        "description": "未来/星际背景下，主角利用科技/系统/进化在宏大世界中崛起",
        "act_structure": [
            {"name": "觉醒", "volumes": "1", "goal": "主角获得金手指（系统/机甲/进化），初步适应新世界", "pacing": "世界观展示+能力觉醒，信息量大但不堆砌"},
            {"name": "成长", "volumes": "2-4", "goal": "实力提升，加入学院/军队，首次实战中证明自己", "pacing": "训练→实战→升级的循环，每几章一次小高潮"},
            {"name": "冲突爆发", "volumes": "5-7", "goal": "卷入阵营战争/星际冲突，发现更大的阴谋和敌人", "pacing": "战争场面+战术博弈，格局从个人上升到阵营"},
            {"name": "巅峰对决", "volumes": "8-9", "goal": "与最终势力决战，揭示世界真相", "pacing": "战斗烈度递增，终极对决"},
            {"name": "新秩序", "volumes": "10", "goal": "战后重建，主角在新时代的位置", "pacing": "收束各角色线，展望未来"},
        ],
        "key_volumes": {
            "1": "核心情节：能力觉醒→第一次实战→加入核心组织。卷末：走出新手村",
            "2": "系统训练/学院生活，结下战友情谊，首次中等规模冲突",
            "3": "实力质变，参与关键战役，发现暗线阴谋",
        },
        "character_archetypes": {
            "protagonist": "聪明/坚韧/有领导力，可能在科技或战斗方面有特殊天赋",
            "mentor": "资深前辈/指挥官，严厉但可靠",
            "rival": "同期竞争者，亦敌亦友的成长催化剂",
            "teammate": "可靠的后勤/技术伙伴，性格互补",
        },
    },

    "玄幻": {
        "title": "玄幻/异世界",
        "aliases": ["玄幻", "异世界", "奇幻", "魔法"],
        "description": "异世界/架空奇幻，主角穿越或土著，在剑与魔法的世界中崛起",
        "act_structure": [
            {"name": "异世新生", "volumes": "1", "goal": "主角进入异世界，获得立足之本（天赋/神器/系统），初步了解世界", "pacing": "快速建立世界观基调"},
            {"name": "初露锋芒", "volumes": "2-4", "goal": "实力提升，参加学院/冒险者工会，积累名望和伙伴", "pacing": "冒险+升级+结识伙伴"},
            {"name": "风云变换", "volumes": "5-7", "goal": "卷入大陆纷争/王国战争，发现世界深层秘密", "pacing": "宏大叙事，多势力博弈"},
            {"name": "王者之路", "volumes": "8-9", "goal": "整合势力，与黑暗势力/邪神最终对决", "pacing": "史诗感，大型战争场面"},
            {"name": "和平年代", "volumes": "10", "goal": "战后重建，主角成为传说中的英雄", "pacing": "群像收尾，温暖的结局"},
        ],
        "key_volumes": {
            "1": "核心情节：穿越/觉醒→第一次战斗→获得伙伴/武器。卷末：踏上冒险旅程",
            "2": "进入学院/城市，学业/任务中成长，结识主要伙伴",
            "3": "实力达到中期，第一次大型冒险/讨伐",
        },
        "character_archetypes": {
            "protagonist": "坚韧/热血/正义感强，有独特的战斗风格或天赋",
            "love_interest": "在冒险中相遇的女主，性格与主角互补",
            "best_friend": "搞笑担当/吐槽役，主角的精神支柱",
            "sage": "智慧长者/隐藏高手，关键时候提供指引",
        },
    },

    "都市": {
        "title": "都市/现代",
        "aliases": ["都市", "现代", "现实"],
        "description": "现代都市背景下，主角获得异能/系统/重生，在都市中展开新生活",
        "act_structure": [
            {"name": "转折", "volumes": "1", "goal": "主角获得金手指，人生轨迹改变", "pacing": "快节奏切入"},
            {"name": "崛起", "volumes": "2-4", "goal": "在事业/修炼/关系中崭露头角，建立自己的圈子", "pacing": "日常+爽点交替"},
            {"name": "暗流涌动", "volumes": "5-7", "goal": "面临真正的对手和挑战，展现格局和智慧", "pacing": "冲突升级，悬念增多"},
            {"name": "巅峰之路", "volumes": "8-9", "goal": "解决最终的敌人/难题，达到事业/实力巅峰", "pacing": "高潮迭起"},
            {"name": "功成身退", "volumes": "10", "goal": "回归平静生活，收束各人物线", "pacing": "温馨收尾"},
        ],
        "key_volumes": {
            "1": "核心情节：获得能力→第一次使用→站稳脚跟。卷末：人生新篇章开启",
        },
        "character_archetypes": {
            "protagonist": "有隐藏天赋或前世记忆，在都市中如鱼得水",
            "best_friend": "从小一起长大的兄弟/闺蜜，负责搞笑和情感支持",
            "love_interest": "在主角崛起过程中相遇的特别的人",
        },
    },
}


def get_template(template_name: Optional[str] = None) -> Optional[dict]:
    """通过名称或别名查找模板。不传参数返回第一个匹配或 None。"""
    if not template_name:
        return None
    name_lower = template_name.lower().strip()

    # Exact match
    for key, tpl in TEMPLATES.items():
        if key == name_lower:
            return tpl
        if tpl["title"] == template_name:
            return tpl

    # Alias match
    for tpl in TEMPLATES.values():
        for alias in tpl.get("aliases", []):
            if alias.lower() in name_lower or name_lower in alias.lower():
                return tpl

    # Partial name match
    for key, tpl in TEMPLATES.items():
        if name_lower in key or name_lower in tpl["title"].lower():
            return tpl

    return None


def format_template_prompt(template: dict, volume_no: int = 1) -> str:
    """将模板格式化为 init_novel 和 volume_plan 的上下文提示。"""
    parts = [f"## 类型指引：{template['title']}\n"]
    parts.append(f"{template['description']}\n")

    # Act structure
    parts.append("### 推荐幕结构\n")
    for act in template["act_structure"]:
        parts.append(
            f"- 【{act['name']}】（第{act['volumes']}卷）\n"
            f"  目标：{act['goal']}\n"
            f"  节奏建议：{act['pacing']}\n"
        )

    # Key volumes guidance for early volumes
    if template.get("key_volumes"):
        vol_str = str(volume_no)
        if vol_str in template["key_volumes"]:
            parts.append(f"### 第{volume_no}卷参考\n")
            parts.append(template["key_volumes"][vol_str] + "\n")

    return "\n".join(parts)
